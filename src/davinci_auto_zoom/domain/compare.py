from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from davinci_auto_zoom.domain.models import Frame
from davinci_auto_zoom.domain.snapshot import TimelineItemSnapshot, TimelineSnapshot


@dataclass(frozen=True, slots=True)
class AddedItem:
    """An item present in the reference timeline but not in the input timeline."""

    name: str
    track_type: str
    track_index: int
    start: Frame
    end: Frame
    duration: int
    start_offset_to_nearest_cut: int | None
    end_offset_to_nearest_cut: int | None
    gap_from_previous: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TimelineComparison:
    """Structural, read-only diff. Descriptive only: the reference is not ground truth."""

    input_timeline: str
    reference_timeline: str
    shared_tracks: tuple[str, ...]
    reference_only_tracks: tuple[str, ...]
    input_only_tracks: tuple[str, ...]
    added_items: tuple[AddedItem, ...]
    cut_count: int
    ends_on_cut: int
    starts_on_cut: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _track_key(track_type: str, index: int) -> str:
    return f"{track_type}{index}"


def _item_signature(item: TimelineItemSnapshot) -> tuple[Any, ...]:
    return (item.name, item.start, item.end, item.source_start_frame)


def _nearest_offset(frame: Frame, cuts: tuple[Frame, ...]) -> int | None:
    if not cuts:
        return None
    return frame - min(cuts, key=lambda cut: abs(cut - frame))


def compare_timelines(
    input_timeline: TimelineSnapshot,
    reference_timeline: TimelineSnapshot,
    cut_source_video_track: int = 1,
) -> TimelineComparison:
    """Report what the reference timeline adds on top of the input timeline.

    Only structural facts are produced. Nothing here judges whether an editorial choice
    was correct; the reference timeline is a qualitative human example, not a target.
    """

    input_tracks = {_track_key(t.track_type, t.index): t for t in input_timeline.tracks}
    reference_tracks = {_track_key(t.track_type, t.index): t for t in reference_timeline.tracks}

    shared = tuple(sorted(input_tracks.keys() & reference_tracks.keys()))
    reference_only = tuple(sorted(reference_tracks.keys() - input_tracks.keys()))
    input_only = tuple(sorted(input_tracks.keys() - reference_tracks.keys()))

    cuts = input_timeline.edit_boundaries(cut_source_video_track)

    added: list[AddedItem] = []
    for key in sorted(reference_tracks.keys()):
        reference_track = reference_tracks[key]
        counterpart = input_tracks.get(key)
        existing = {_item_signature(i) for i in counterpart.items} if counterpart else set()
        previous_end: Frame | None = None
        for item in reference_track.items:
            if _item_signature(item) in existing:
                previous_end = item.end
                continue
            added.append(
                AddedItem(
                    name=item.name,
                    track_type=reference_track.track_type,
                    track_index=reference_track.index,
                    start=item.start,
                    end=item.end,
                    duration=item.duration,
                    start_offset_to_nearest_cut=_nearest_offset(item.start, cuts),
                    end_offset_to_nearest_cut=_nearest_offset(item.end, cuts),
                    gap_from_previous=None if previous_end is None else item.start - previous_end,
                )
            )
            previous_end = item.end

    return TimelineComparison(
        input_timeline=input_timeline.name,
        reference_timeline=reference_timeline.name,
        shared_tracks=shared,
        reference_only_tracks=reference_only,
        input_only_tracks=input_only,
        added_items=tuple(added),
        cut_count=len(cuts),
        ends_on_cut=sum(1 for i in added if i.end_offset_to_nearest_cut == 0),
        starts_on_cut=sum(1 for i in added if i.start_offset_to_nearest_cut == 0),
    )


def added_generator_items(comparison: TimelineComparison) -> tuple[AddedItem, ...]:
    """Added items that are placed on a video track, i.e. candidate zoom instances."""
    return tuple(i for i in comparison.added_items if i.track_type == "video")


def alternation(items: tuple[AddedItem, ...]) -> tuple[str, ...]:
    """The observed sequence of asset names, useful to spot an x1/x0 alternation pattern."""
    return tuple(item.name for item in items)


__all__ = [
    "AddedItem",
    "TimelineComparison",
    "added_generator_items",
    "alternation",
    "compare_timelines",
]
