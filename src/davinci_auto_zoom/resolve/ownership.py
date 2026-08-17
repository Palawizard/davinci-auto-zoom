"""The only place DAZ ownership metadata is written to, or read from, a live Resolve object.

Two directions, both thin:

* **read** — `TimelineItem.GetMarkers()` into `MarkerSnapshot`/`OwnedItemSnapshot`, which the
  pure classifier in `domain/ownership.py` then judges. Nothing here decides anything;
* **write** — one `TimelineItem.AddMarker(...)` per created item, onto a local frame proven
  free first, followed by an immediate re-read that must find the record back.

Both `AddMarker` and `GetMarkers` are documented on `TimelineItem` in the Developer/Scripting
README installed with Studio 21.0.4.5, and neither appears in that README's deprecated or
unsupported sections. `customData` is documented as "not exposed via UI and is useful for
scripting developer to attach any user specific data to markers" — exactly the slot this
needs.

**Never destructive.** Nothing in this module deletes or updates a marker. DAZ adds one
marker to items it has just created itself, and it does not touch a marker it did not write —
not even its own, and never a user's (D036). Cleanup removes whole TimelineItems, which takes
their markers with them; it never edits markers in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from davinci_auto_zoom.domain.ownership import (
    MARKER_COLOR,
    MARKER_DURATION,
    MARKER_NAME,
    MARKER_NOTE,
    MarkerSnapshot,
    OwnedItemSnapshot,
    OwnershipRecord,
    claims_ownership,
    free_marker_frame,
    parse,
    serialize,
)


def _int(value: Any, default: int = 0) -> int:
    """Resolve returns marker frames as floats (`96.0`) and durations likewise."""

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def read_markers(item: Any) -> tuple[MarkerSnapshot, ...]:
    """Every marker on one TimelineItem, sorted by local frame. Read-only.

    An unreadable `GetMarkers()` is **not** silently turned into "no markers": that would
    read as "no DAZ ownership and nothing of the user's to protect", which is the most
    dangerous possible misreading. The exception propagates.
    """

    markers = item.GetMarkers() or {}
    snapshots = [
        MarkerSnapshot(
            frame=_int(frame),
            custom_data=str(info.get("customData") or ""),
            color=str(info.get("color") or ""),
            name=str(info.get("name") or ""),
            note=str(info.get("note") or ""),
            duration=_int(info.get("duration"), 1),
        )
        for frame, info in markers.items()
    ]
    return tuple(sorted(snapshots, key=lambda marker: marker.frame))


def snapshot_owned_item(item: Any) -> OwnedItemSnapshot:
    """One live TimelineItem as the enriched snapshot the classifier consumes."""

    track = item.GetTrackTypeAndIndex() or [None, None]
    return OwnedItemSnapshot(
        name=str(item.GetName()),
        start=int(item.GetStart()),
        end=int(item.GetEnd()),
        unique_id=item.GetUniqueId(),
        track_index=int(track[1]) if track[1] is not None else None,
        markers=read_markers(item),
    )


def snapshot_owned_track(timeline: Any, track_index: int) -> tuple[OwnedItemSnapshot, ...]:
    """Every item on one video track, marker-enriched, in timeline order."""

    items = timeline.GetItemListInTrack("video", track_index) or []
    return tuple(snapshot_owned_item(item) for item in items)


@dataclass(frozen=True, slots=True)
class TagResult:
    """What tagging one item attempted, and what the re-read proved."""

    ok: bool
    marker_frame: int | None = None
    custom_data: str = ""
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "marker_frame": self.marker_frame,
            "problems": list(self.problems),
        }


def tag_item(item: Any, record: OwnershipRecord) -> TagResult:
    """Attach `record` to one live TimelineItem and prove it can be read back.

    Fail-closed at every step, and never at the user's expense:

    1. the item's existing markers are read **first**. DAZ picks a local frame nothing already
       occupies, so a user marker at frame 0 is stepped around rather than overwritten. If the
       item has no free local frame at all — possible on a 15-frame reset — this returns a
       failure instead of taking somebody else's frame (D036);
    2. `AddMarker` must return truthy;
    3. the markers are re-read from Resolve and must contain exactly one DAZ record, at the
       frame we asked for, whose `customData` parses back into the record we wrote.

    Step 3 is the one that matters. "AddMarker returned True" is a claim; a record that comes
    back out of Resolve unchanged is evidence, and only evidence lets a preview be declared
    owned (D035).
    """

    custom_data = serialize(record)
    try:
        existing = read_markers(item)
    except Exception as exc:
        return TagResult(
            False, custom_data=custom_data,
            problems=(f"GetMarkers() could not be read before tagging: {exc!r}",),
        )

    duration = int(item.GetDuration() or 0)
    frame = free_marker_frame(duration, existing)
    if frame is None:
        return TagResult(
            False, custom_data=custom_data,
            problems=(
                f"every one of the {duration} local frame(s) of this item already carries a "
                "marker; refusing to overwrite one to make room for ownership metadata",
            ),
        )

    try:
        added = item.AddMarker(
            frame, MARKER_COLOR, MARKER_NAME, MARKER_NOTE, MARKER_DURATION, custom_data
        )
    except Exception as exc:
        return TagResult(
            False, frame, custom_data, (f"AddMarker raised {exc!r}",)
        )
    if not added:
        return TagResult(
            False, frame, custom_data,
            (f"AddMarker(frame={frame}, ...) returned {added!r}",),
        )

    try:
        after = read_markers(item)
    except Exception as exc:
        return TagResult(
            False, frame, custom_data,
            (f"GetMarkers() could not be re-read after tagging: {exc!r}",),
        )

    daz = [marker for marker in after if claims_ownership(marker.custom_data)]
    problems: list[str] = []
    if len(daz) != 1:
        problems.append(
            f"after tagging, the item carries {len(daz)} DAZ marker(s), expected exactly 1"
        )
    else:
        written = daz[0]
        if written.frame != frame:
            problems.append(
                f"the DAZ marker came back at local frame {written.frame}, expected {frame}"
            )
        if written.custom_data != custom_data:
            problems.append(
                f"customData came back as {written.custom_data!r}, expected {custom_data!r}"
            )
        else:
            try:
                if parse(written.custom_data) != record:
                    problems.append("the re-read record does not equal the one written")
            except Exception as exc:
                problems.append(f"the re-read record no longer parses: {exc}")

    lost = [
        marker
        for marker in existing
        if marker not in after and not claims_ownership(marker.custom_data)
    ]
    if lost:
        problems.append(
            "tagging disturbed pre-existing marker(s) at local frame(s) "
            + ", ".join(str(marker.frame) for marker in lost)
        )

    return TagResult(not problems, frame, custom_data, tuple(problems))


__all__ = [
    "TagResult",
    "read_markers",
    "snapshot_owned_item",
    "snapshot_owned_track",
    "tag_item",
]
