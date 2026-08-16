"""Pure decisions for the Phase 5 executor: where it may write, and what it must find after.

Two questions, both answerable without Resolve and therefore both tested:

1. **May the target track be written to at all?** The MVP answer is narrow on purpose: DAZ
   writes only to a dedicated zoom video track it has verified is *empty*. It does not
   discover what Resolve does when clips collide, it does not overwrite, shuffle or delete
   anything, and it does not try to recognise its own earlier output — ownership and
   idempotence are later work (D032).

2. **Did the timeline end up holding exactly the plan?** The executor checks each insertion
   as it goes, but "each call looked fine" is weaker than "the track now equals the plan".
   `placement_differences` compares the two as ordered sequences: same count, same order,
   same names, same frames, no extras, no overlaps.

Nothing here mutates anything, and nothing here talks to Resolve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from davinci_auto_zoom.domain.planner import AssetPlacement, ZoomPlan
from davinci_auto_zoom.domain.snapshot import TimelineItemSnapshot, TimelineSnapshot


@dataclass(frozen=True, slots=True)
class TrackPreparation:
    """What must happen to reach the configured zoom track, and whether it is usable."""

    target_index: int
    existing_video_tracks: int
    tracks_to_add: int
    #: Reasons the target track may not be written to. Non-empty means refuse before writing.
    blockers: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_index": self.target_index,
            "existing_video_tracks": self.existing_video_tracks,
            "tracks_to_add": self.tracks_to_add,
            "blockers": list(self.blockers),
            "usable": self.usable,
        }


def plan_target_track(timeline: TimelineSnapshot, target_index: int) -> TrackPreparation:
    """Decide how to reach `target_index` on `timeline`, fail-closed on anything occupied.

    A missing track is fine: video tracks are appended until the configured index exists,
    which is the ordinary case (`DAZ_INPUT` has V1-V2 and the zoom track is V3). A track that
    exists and already holds *any* `TimelineItem` is a refusal — whether those items are
    somebody's titles or a previous DAZ run's zooms, this phase does not touch them.
    """

    existing = len(timeline.tracks_of("video"))
    blockers: list[str] = []
    if target_index < 1:
        blockers.append(f"zoom_video_track must be a 1-based index, got {target_index}")
        return TrackPreparation(target_index, existing, 0, tuple(blockers))

    track = timeline.track("video", target_index)
    if track is not None and track.items:
        blockers.append(
            f"target video track V{target_index} of {timeline.name!r} already holds "
            f"{len(track.items)} item(s): "
            + ", ".join(
                f"{item.name!r} [{item.start},{item.end})" for item in track.items[:5]
            )
            + (" ..." if len(track.items) > 5 else "")
            + ". This phase only writes to a dedicated, empty zoom track and never removes "
            "or overwrites an existing clip."
        )
    return TrackPreparation(
        target_index=target_index,
        existing_video_tracks=existing,
        tracks_to_add=max(0, target_index - existing),
        blockers=tuple(blockers),
    )


@dataclass(frozen=True, slots=True)
class ExpectedItem:
    """One planned insertion, expressed as the TimelineItem it must produce."""

    role: str
    name: str
    start: int
    end: int
    track_index: int

    @property
    def duration(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "track_index": self.track_index,
        }


def expected_items(
    plan: ZoomPlan, assets: Mapping[str, str], track_index: int
) -> tuple[ExpectedItem, ...]:
    """The plan, restated as the timeline items it must produce. No decision is made here."""

    return tuple(
        ExpectedItem(
            role=placement.asset_role,
            name=assets[placement.asset_role],
            start=placement.start_frame,
            end=placement.end_frame,
            track_index=track_index,
        )
        for placement in plan.placements
    )


def clip_info_for(
    placement: AssetPlacement, media_pool_item: Any, track_index: int
) -> dict[str, Any]:
    """The exact `{clipInfo}` dict for one placement — the proven Phase 2 call (D013).

    `endFrame` is **exclusive**, so it equals the duration: a 15-frame reset is
    `startFrame=0, endFrame=15`, never 14. Nothing here consults the asset's native Media
    Pool length, and nothing here re-derives a frame the planner already decided.
    """

    return {
        "mediaPoolItem": media_pool_item,
        "startFrame": 0,
        "endFrame": placement.duration_frames,
        "trackIndex": track_index,
        "recordFrame": placement.start_frame,
    }


def insertion_differences(expected: ExpectedItem, actual: dict[str, Any]) -> tuple[str, ...]:
    """Check one `AppendToTimeline` result against what the placement asked for."""

    problems: list[str] = []
    if actual.get("name") != expected.name:
        problems.append(f"name {actual.get('name')!r}, expected {expected.name!r}")
    if actual.get("track_index") != expected.track_index:
        problems.append(
            f"landed on track {actual.get('track_index')}, expected V{expected.track_index}"
        )
    if actual.get("start") != expected.start:
        problems.append(f"start {actual.get('start')}, expected {expected.start}")
    if actual.get("end") != expected.end:
        problems.append(f"end {actual.get('end')}, expected {expected.end}")
    if actual.get("duration") != expected.duration:
        problems.append(f"duration {actual.get('duration')}, expected {expected.duration}")
    if not actual.get("fusion_comp_count"):
        problems.append(
            "the created instance reports no Fusion composition, so the user's zoom effect "
            "did not travel with it"
        )
    return tuple(problems)


def placement_differences(
    expected: Sequence[ExpectedItem], actual: Sequence[TimelineItemSnapshot]
) -> tuple[str, ...]:
    """Structural comparison of the whole target track against the whole plan.

    Ordered, one-to-one and total: a missing item, an extra item, a frame out of place or two
    items that overlap all produce a difference. Empty means the track *is* the plan.
    """

    problems: list[str] = []
    if len(expected) != len(actual):
        problems.append(
            f"target track holds {len(actual)} item(s), the plan has {len(expected)}"
        )
    for index, (want, got) in enumerate(zip(expected, actual, strict=False)):
        details: list[str] = []
        if got.name != want.name:
            details.append(f"name {got.name!r} != {want.name!r}")
        if got.start != want.start:
            details.append(f"start {got.start} != {want.start}")
        if got.end != want.end:
            details.append(f"end {got.end} != {want.end}")
        if got.duration != want.duration:
            details.append(f"duration {got.duration} != {want.duration}")
        if not got.fusion_comp_count:
            details.append("no Fusion composition on the created instance")
        if details:
            problems.append(f"item {index} ({want.role}): " + ", ".join(details))
    for index, (earlier, later) in enumerate(zip(actual, actual[1:], strict=False)):
        if later.start < earlier.end:
            problems.append(
                f"items {index} and {index + 1} overlap: "
                f"[{earlier.start},{earlier.end}) then [{later.start},{later.end})"
            )
    return tuple(problems)


__all__ = [
    "ExpectedItem",
    "TrackPreparation",
    "clip_info_for",
    "expected_items",
    "insertion_differences",
    "placement_differences",
    "plan_target_track",
]
