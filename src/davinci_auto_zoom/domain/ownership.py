"""Phase 7: how DAZ proves a TimelineItem is one it created — and how it admits it cannot.

The problem this solves has one shape. Two `FACE_X1` TimelineItems can sit on the same track,
at the same frames, for the same duration, carrying the same Fusion comp and pointing at the
same Media Pool asset — and one of them can be DAZ's while the other was dragged there by the
user. **Nothing observable about the clip distinguishes them.** Therefore:

    ownership is never inferred from a clip name, a track index, a position, a duration, a
    Fusion graph or a Media Pool asset.

The only proof is a marker on the **TimelineItem instance** whose `customData` carries this
module's record (D035). Markers belong to the instance, not to the shared Media Pool asset,
they are re-readable through documented getters, they survive switching timelines, and they
require no UI automation. `TimelineItem.AddMarker` / `GetMarkers` / `GetMarkerByCustomData` /
`DeleteMarkerByCustomData` are all documented and non-deprecated in the Developer/Scripting
README installed with Studio 21.0.4.5.

Everything here is pure: it takes strings and snapshots, and returns records and verdicts.
The Resolve side lives in `resolve/ownership.py`.

## Four states, not two

`unowned` is the *safe* answer, not the fallback one. An item DAZ cannot positively claim is
somebody else's, and destructive commands leave it alone. But "I cannot claim it" and "I
cannot even classify it" are different, and collapsing them would let a corrupt record be
quietly deleted-around:

* `owned` — a well-formed record that matches every expectation. Deletable.
* `unowned` — no marker claims to be DAZ's. Never touched.
* `stale` — a well-formed record belonging to a *different* preview or a different source
  fingerprint. Typically a timeline the user duplicated by hand, carrying copies of the
  original's markers. Never deleted automatically (D038).
* `ambiguous` — a marker claims to be DAZ's and then contradicts itself: unparseable JSON, an
  unknown schema, an unknown role, an asset that is not the configured one for that role, a
  placement id that does not match its own fields, or two DAZ markers making different
  claims. **A single ambiguous item on a track means zero deletions on that track.** Partially
  cleaning a track whose ownership is not fully classifiable is exactly the failure mode this
  phase exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Incontestable prefix. Present verbatim in every DAZ `customData`, and the only thing that
#: makes a marker *claim* to be ours. A marker without it is somebody else's, full stop.
NAMESPACE = "davinci-auto-zoom"

#: Bumped when the record's shape changes. An unknown version is `ambiguous`, never ignored:
#: a newer DAZ may have written something this build cannot safely reason about.
SCHEMA = 1

#: Cosmetic, and never used as evidence. Colour and name only exist so a human scrubbing the
#: timeline can see that something automated touched the clip.
MARKER_COLOR = "Lavender"
MARKER_NAME = "DAZ ownership"
MARKER_NOTE = f"{NAMESPACE} ownership metadata — do not edit"
MARKER_DURATION = 1

#: Version of the placement-id digest. Part of the hashed material, so changing the recipe
#: cannot make an old id collide with a new one.
PLACEMENT_ID_VERSION = 1
#: 128 bits of SHA-256. Long enough that a collision is not a thing that happens, short enough
#: to keep `customData` readable.
PLACEMENT_ID_LENGTH = 32

OWNED = "owned"
UNOWNED = "unowned"
STALE = "stale"
AMBIGUOUS = "ambiguous"

#: A role is a configuration key, so it is constrained like one. This is a *syntactic* check;
#: whether the role is one this project actually configures is checked against the config.
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class OwnershipFormatError(ValueError):
    """A marker claimed to be DAZ's and then failed to be a DAZ record."""


@dataclass(frozen=True, slots=True)
class OwnershipRecord:
    """What DAZ writes onto every TimelineItem it creates.

    Every field earns its place:

    * `namespace` / `schema` — the claim and its version. Without both, nothing is parsed.
    * `preview_id` — the unique id of the preview timeline this item was created *for*. This
      is what makes a hand-duplicated timeline detectable: the copy has a new timeline id
      while its markers still name the original (D038).
    * `role` — the semantic slot (`facecam_x1`, `reset_x0`, and whatever later rules add), not
      the asset name. Roles survive an asset being renamed; names do not.
    * `asset` — the configured Media Pool clip name for that role *at creation time*. Kept so
      a later run can notice the configuration moved underneath it.
    * `start` / `end` — the half-open timeline range the planner asked for. Kept so a moved
      item is still recognisably ours, with a diagnostic saying it moved.
    * `source_fingerprint` — the plan's structural fingerprint. Ties the item to the material
      it was planned from, so a rebuild against re-cut source refuses instead of half-working.
    * `placement_id` — the deterministic identity of this placement (see `placement_id`).
    """

    preview_id: str
    role: str
    asset: str
    start: int
    end: int
    source_fingerprint: str
    placement_id: str
    namespace: str = NAMESPACE
    schema: int = SCHEMA

    @property
    def duration(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "end": self.end,
            "namespace": self.namespace,
            "placement_id": self.placement_id,
            "preview_id": self.preview_id,
            "role": self.role,
            "schema": self.schema,
            "source_fingerprint": self.source_fingerprint,
            "start": self.start,
        }


def placement_id(
    *, source_fingerprint: str, role: str, start: int, end: int, asset: str
) -> str:
    """The deterministic identity of one placement. **No clock, no randomness, no counter.**

    This is the contract idempotence rests on. For the same source material, the same planner
    configuration, the same role, the same frames and the same configured asset, this returns
    the same string — on any machine, in any session, after any number of rebuilds. Two
    rebuilds that produce identical placement ids produced the identical desired state; new
    `TimelineItem.GetUniqueId()` values in between are irrelevant and are deliberately not
    part of the digest.

    Hashed material, canonically serialized (sorted keys, no insignificant whitespace, UTF-8):

    * `version` — the recipe, so a future change cannot collide with today's ids;
    * `source_fingerprint` — the material the plan was built from. A re-cut V1 or a moved
      voice clip changes it, and therefore changes every placement id;
    * `role`, `start`, `end` — what the planner decided;
    * `asset` — the configured clip name, so pointing a role at a different asset is a
      different placement even at the same frames.

    Deliberately **not** hashed: the preview timeline's identity. A placement is a property of
    the plan, not of whichever preview happens to hold it.
    """

    material = {
        "asset": asset,
        "end": int(end),
        "role": role,
        "source_fingerprint": source_fingerprint,
        "start": int(start),
        "version": PLACEMENT_ID_VERSION,
    }
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:PLACEMENT_ID_LENGTH]


def build_record(
    *, preview_id: str, role: str, asset: str, start: int, end: int, source_fingerprint: str
) -> OwnershipRecord:
    """An `OwnershipRecord` with its `placement_id` derived, never supplied."""

    return OwnershipRecord(
        preview_id=preview_id,
        role=role,
        asset=asset,
        start=int(start),
        end=int(end),
        source_fingerprint=source_fingerprint,
        placement_id=placement_id(
            source_fingerprint=source_fingerprint,
            role=role,
            start=start,
            end=end,
            asset=asset,
        ),
    )


def serialize(record: OwnershipRecord) -> str:
    """Canonical JSON. The same record always produces the same string, byte for byte."""

    return json.dumps(
        record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def claims_ownership(custom_data: str) -> bool:
    """Does this marker *claim* to be DAZ's?

    The namespace appearing anywhere in the string is enough. This is intentionally generous:
    a marker that mentions the namespace and then fails to parse must become `ambiguous`, not
    be waved through as somebody else's. Being strict here would let a corrupted DAZ record
    disguise itself as a user marker and get deleted around.
    """

    return NAMESPACE in (custom_data or "")


def parse(custom_data: str) -> OwnershipRecord:
    """Parse one `customData` string, or raise `OwnershipFormatError` explaining why not."""

    try:
        payload = json.loads(custom_data)
    except (ValueError, TypeError) as exc:
        raise OwnershipFormatError(f"customData is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise OwnershipFormatError(
            f"customData is a JSON {type(payload).__name__}, expected an object"
        )

    namespace = payload.get("namespace")
    if namespace != NAMESPACE:
        raise OwnershipFormatError(
            f"namespace {namespace!r}, expected {NAMESPACE!r}"
        )
    schema = payload.get("schema")
    if schema != SCHEMA:
        raise OwnershipFormatError(
            f"schema {schema!r} is not supported by this build (expected {SCHEMA})"
        )

    values: dict[str, Any] = {}
    for key, kind in (
        ("preview_id", str),
        ("role", str),
        ("asset", str),
        ("start", int),
        ("end", int),
        ("source_fingerprint", str),
        ("placement_id", str),
    ):
        if key not in payload:
            raise OwnershipFormatError(f"missing field {key!r}")
        value = payload[key]
        # bool is an int in Python and must not slip through as a frame number.
        if not isinstance(value, kind) or isinstance(value, bool):
            raise OwnershipFormatError(
                f"field {key!r} is {type(value).__name__}, expected {kind.__name__}"
            )
        values[key] = value

    unexpected = sorted(set(payload) - set(values) - {"namespace", "schema"})
    if unexpected:
        raise OwnershipFormatError(f"unexpected field(s) {unexpected}")

    if not _ROLE.match(values["role"]):
        raise OwnershipFormatError(f"role {values['role']!r} is not a valid role name")
    if values["end"] <= values["start"]:
        raise OwnershipFormatError(
            f"frames [{values['start']},{values['end']}) are not a forward range"
        )

    expected_id = placement_id(
        source_fingerprint=values["source_fingerprint"],
        role=values["role"],
        start=values["start"],
        end=values["end"],
        asset=values["asset"],
    )
    if values["placement_id"] != expected_id:
        raise OwnershipFormatError(
            f"placement_id {values['placement_id']!r} does not match this record's own "
            f"fields (expected {expected_id!r})"
        )

    return OwnershipRecord(**values)


@dataclass(frozen=True, slots=True)
class MarkerSnapshot:
    """One marker read off a TimelineItem, as plain data. Frames are item-local."""

    frame: int
    custom_data: str = ""
    color: str = ""
    name: str = ""
    note: str = ""
    duration: int = 1

    @property
    def span(self) -> tuple[int, int]:
        """Half-open local range the marker occupies. Zero-duration markers still take a frame."""

        return self.frame, self.frame + max(1, self.duration)


@dataclass(frozen=True, slots=True)
class OwnedItemSnapshot:
    """A TimelineItem enriched with its markers — the classifier's whole input."""

    name: str
    start: int
    end: int
    unique_id: str | None = None
    track_index: int | None = None
    markers: tuple[MarkerSnapshot, ...] = ()

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def label(self) -> str:
        return f"{self.name!r} [{self.start},{self.end}) V{self.track_index}"


@dataclass(frozen=True, slots=True)
class OwnershipExpectations:
    """What a *correct* record would say, for the caller's current situation.

    Both optional fields are `None`-means-do-not-check on purpose, and the difference is the
    difference between the two destructive commands:

    * `clean-preview` passes `source_fingerprint=None`. Cleaning does not care whether the
      source material has moved since the preview was built — it is removing what DAZ made,
      not rebuilding it;
    * `rebuild-preview` passes the fingerprint of the *fresh* plan, so an item planned from
      material that has since been re-cut comes back `stale` and the rebuild refuses before
      deleting anything.
    """

    preview_id: str | None = None
    source_fingerprint: str | None = None
    #: role -> configured Media Pool clip name. An empty mapping skips the role/asset checks.
    assets: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ItemOwnership:
    """The verdict on one item, with everything needed to explain it to a human."""

    item: OwnedItemSnapshot
    state: str
    record: OwnershipRecord | None = None
    marker_frame: int | None = None
    #: Why the state is `ambiguous` or `stale`. Empty for `owned` and `unowned`.
    problems: tuple[str, ...] = ()
    #: Defence-in-depth observations that do NOT change the verdict — an item that moved
    #: since it was tagged is still ours, and saying so is more useful than refusing.
    warnings: tuple[str, ...] = ()

    @property
    def owned(self) -> bool:
        return self.state == OWNED

    @property
    def blocks_deletion(self) -> bool:
        """States that must stop a destructive run before its first delete."""

        return self.state in (AMBIGUOUS, STALE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.item.name,
            "start": self.item.start,
            "end": self.item.end,
            "track_index": self.item.track_index,
            "unique_id": self.item.unique_id,
            "state": self.state,
            "marker_frame": self.marker_frame,
            "placement_id": self.record.placement_id if self.record else None,
            "role": self.record.role if self.record else None,
            "preview_id": self.record.preview_id if self.record else None,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
        }


def _sanity(
    item: OwnedItemSnapshot,
    record: OwnershipRecord,
    expectations: OwnershipExpectations,
) -> tuple[list[str], list[str], list[str]]:
    """`(contradictions, stale_reasons, warnings)` — defence in depth around the marker.

    The marker stays the primary proof. These checks only ever make the verdict *stricter*:
    they can turn `owned` into `ambiguous` or `stale`, never the other way round.
    """

    contradictions: list[str] = []
    stale: list[str] = []
    warnings: list[str] = []

    assets = expectations.assets
    if assets is not None:
        configured = assets.get(record.role)
        if configured is None:
            contradictions.append(
                f"role {record.role!r} is not configured in this project "
                f"(known roles: {sorted(assets) or 'none'})"
            )
        elif configured != record.asset:
            contradictions.append(
                f"role {record.role!r} is configured for asset {configured!r} but the record "
                f"names {record.asset!r}"
            )
        elif item.name != record.asset:
            contradictions.append(
                f"the clip is named {item.name!r} but its record claims asset "
                f"{record.asset!r}"
            )

    if expectations.preview_id is not None and record.preview_id != expectations.preview_id:
        stale.append(
            f"the record was written for preview {record.preview_id!r}, but this timeline is "
            f"{expectations.preview_id!r}"
        )
    if (
        expectations.source_fingerprint is not None
        and record.source_fingerprint != expectations.source_fingerprint
    ):
        stale.append(
            f"the record was planned from source {record.source_fingerprint} but the current "
            f"source is {expectations.source_fingerprint}"
        )

    if (record.start, record.end) != (item.start, item.end):
        warnings.append(
            f"the item sits at [{item.start},{item.end}) but was tagged at "
            f"[{record.start},{record.end}); it has been moved or retimed since"
        )

    return contradictions, stale, warnings


def classify_item(
    item: OwnedItemSnapshot, expectations: OwnershipExpectations | None = None
) -> ItemOwnership:
    """Decide what one TimelineItem is. Fail-closed on anything self-contradictory."""

    expectations = expectations or OwnershipExpectations()
    candidates = [m for m in item.markers if claims_ownership(m.custom_data)]
    if not candidates:
        return ItemOwnership(item=item, state=UNOWNED)

    records: list[tuple[MarkerSnapshot, OwnershipRecord]] = []
    problems: list[str] = []
    for marker in candidates:
        try:
            records.append((marker, parse(marker.custom_data)))
        except OwnershipFormatError as exc:
            problems.append(f"DAZ marker at local frame {marker.frame}: {exc}")

    if problems:
        return ItemOwnership(item=item, state=AMBIGUOUS, problems=tuple(problems))

    distinct = {serialize(record) for _, record in records}
    if len(distinct) > 1:
        return ItemOwnership(
            item=item,
            state=AMBIGUOUS,
            problems=(
                f"{len(records)} DAZ markers make {len(distinct)} different ownership claims: "
                + "; ".join(sorted(distinct)),
            ),
        )

    marker, record = records[0]
    warnings: list[str] = []
    if len(records) > 1:
        # Identical duplicates are a redundancy, not a contradiction: they all say the same
        # thing, so the item's identity is not in doubt.
        warnings.append(
            f"{len(records)} identical DAZ markers on this item at local frames "
            + ", ".join(str(m.frame) for m, _ in records)
        )

    contradictions, stale, more_warnings = _sanity(item, record, expectations)
    warnings.extend(more_warnings)
    if contradictions:
        return ItemOwnership(
            item=item,
            state=AMBIGUOUS,
            record=record,
            marker_frame=marker.frame,
            problems=tuple(contradictions),
            warnings=tuple(warnings),
        )
    if stale:
        return ItemOwnership(
            item=item,
            state=STALE,
            record=record,
            marker_frame=marker.frame,
            problems=tuple(stale),
            warnings=tuple(warnings),
        )
    return ItemOwnership(
        item=item,
        state=OWNED,
        record=record,
        marker_frame=marker.frame,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class TrackOwnership:
    """Every item on one track, classified, plus the gate destructive commands must pass."""

    track_index: int
    items: tuple[ItemOwnership, ...] = ()

    @property
    def owned(self) -> tuple[ItemOwnership, ...]:
        return tuple(item for item in self.items if item.state == OWNED)

    @property
    def unowned(self) -> tuple[ItemOwnership, ...]:
        return tuple(item for item in self.items if item.state == UNOWNED)

    @property
    def stale(self) -> tuple[ItemOwnership, ...]:
        return tuple(item for item in self.items if item.state == STALE)

    @property
    def ambiguous(self) -> tuple[ItemOwnership, ...]:
        return tuple(item for item in self.items if item.state == AMBIGUOUS)

    def deletion_blockers(self) -> tuple[str, ...]:
        """Reasons no item on this track may be deleted. Empty means the track is classified.

        Note what is *not* here: an unowned item. A foreign clip is a perfectly good reason to
        leave that clip alone, and no reason at all to refuse to remove DAZ's own. Only
        ownership that cannot be established one way or the other stops the whole operation
        (D037).
        """

        return tuple(
            f"{item.item.label} is {item.state}: " + "; ".join(item.problems)
            for item in self.items
            if item.blocks_deletion
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_index": self.track_index,
            "counts": {
                "total": len(self.items),
                OWNED: len(self.owned),
                UNOWNED: len(self.unowned),
                STALE: len(self.stale),
                AMBIGUOUS: len(self.ambiguous),
            },
            "items": [item.to_dict() for item in self.items],
            "deletion_blockers": list(self.deletion_blockers()),
        }


def classify_track(
    track_index: int,
    items: Sequence[OwnedItemSnapshot],
    expectations: OwnershipExpectations | None = None,
) -> TrackOwnership:
    return TrackOwnership(
        track_index=track_index,
        items=tuple(classify_item(item, expectations) for item in items),
    )


def free_marker_frame(
    item_duration: int, markers: Sequence[MarkerSnapshot]
) -> int | None:
    """The first item-local frame no existing marker occupies, or None if there is none.

    Local frame 0 is *preferred*, never assumed: a user marker may already be sitting there,
    and DAZ overwriting it would be exactly the silent data loss this phase must not cause
    (D036). A marker's occupied span includes its duration, so a five-frame user marker at
    frame 0 pushes DAZ to frame 5 rather than landing inside it.

    Returning None is a real outcome for our shortest asset: a 15-frame reset whose every
    local frame already carries a marker has nowhere safe to put one. The caller must fail
    that item closed rather than overwrite anything.
    """

    if item_duration <= 0:
        return None
    occupied: set[int] = set()
    for marker in markers:
        start, end = marker.span
        occupied.update(range(max(0, start), min(item_duration, end)))
    for frame in range(item_duration):
        if frame not in occupied:
            return frame
    return None


__all__ = [
    "AMBIGUOUS",
    "MARKER_COLOR",
    "MARKER_DURATION",
    "MARKER_NAME",
    "MARKER_NOTE",
    "NAMESPACE",
    "OWNED",
    "PLACEMENT_ID_VERSION",
    "SCHEMA",
    "STALE",
    "UNOWNED",
    "ItemOwnership",
    "MarkerSnapshot",
    "OwnedItemSnapshot",
    "OwnershipExpectations",
    "OwnershipFormatError",
    "OwnershipRecord",
    "TrackOwnership",
    "build_record",
    "claims_ownership",
    "classify_item",
    "classify_track",
    "free_marker_frame",
    "parse",
    "placement_id",
    "serialize",
]
