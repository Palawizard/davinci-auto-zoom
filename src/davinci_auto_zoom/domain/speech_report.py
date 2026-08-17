"""Pure descriptive statistics over detected speech, for humans reading a probe report.

Nothing here decides anything. It exists so that a person can look at one screen of output
and judge whether a segmentation is plausible, and so that "is the threshold stable?" is
answered with numbers instead of an impression.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from davinci_auto_zoom.domain.models import SpeechSegment
from davinci_auto_zoom.domain.snapshot import TimelineSnapshot
from davinci_auto_zoom.domain.timebase import Timebase, frames_to_timecode
from davinci_auto_zoom.domain.vad import VadSettings, segments_from_probabilities


def _summary(values: Sequence[int]) -> dict[str, int | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": min(values),
        "median": int(statistics.median(values)),
        "max": max(values),
    }


@dataclass(frozen=True, slots=True)
class SegmentStatistics:
    """Shape of a segmentation: how many, how long, how far apart."""

    segment_count: int
    speech_frames: int
    analysed_frames: int
    duration_frames: dict[str, int | None]
    gap_frames: dict[str, int | None]

    @property
    def speech_ratio(self) -> float:
        return self.speech_frames / self.analysed_frames if self.analysed_frames else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_count": self.segment_count,
            "speech_frames": self.speech_frames,
            "analysed_frames": self.analysed_frames,
            "speech_ratio": self.speech_ratio,
            "duration_frames": self.duration_frames,
            "gap_frames": self.gap_frames,
        }


def segment_statistics(
    segments: Sequence[SpeechSegment], analysed_frames: int
) -> SegmentStatistics:
    """Assumes sorted, non-overlapping input (see `normalize_speech_segments`)."""

    durations = [segment.duration_frames for segment in segments]
    gaps = [
        later.start_frame - earlier.end_frame
        for earlier, later in zip(segments, segments[1:], strict=False)
    ]
    return SegmentStatistics(
        segment_count=len(segments),
        speech_frames=sum(durations),
        analysed_frames=analysed_frames,
        duration_frames=_summary(durations),
        gap_frames=_summary(gaps),
    )


@dataclass(frozen=True, slots=True)
class ThresholdTrial:
    """One re-segmentation of the *same* probabilities at a different threshold."""

    threshold: float
    segment_count: int
    speech_samples: int
    speech_ratio: float
    boundary_shift_samples: dict[str, int | None]
    matched_segments: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "segment_count": self.segment_count,
            "speech_samples": self.speech_samples,
            "speech_ratio": self.speech_ratio,
            "boundary_shift_samples": self.boundary_shift_samples,
            "matched_segments": self.matched_segments,
        }


def threshold_stability(
    probabilities: Sequence[float],
    total_samples: int,
    settings: VadSettings,
    thresholds: Sequence[float],
) -> tuple[ThresholdTrial, ...]:
    """Re-run only the post-processing at several thresholds.

    Inference is the expensive part and it does not depend on the threshold, so a stability
    check costs nothing but arithmetic. This measures whether the default sits on a plateau
    or on a cliff — it is *not* a way to pick the threshold that best flatters one video.

    `boundary_shift_samples` compares each trial against `settings.threshold`, matching
    segments by overlap; `matched_segments` says how many of the baseline's segments still
    have a counterpart at all.
    """

    baseline = segments_from_probabilities(probabilities, total_samples, settings)
    trials: list[ThresholdTrial] = []
    for threshold in thresholds:
        trial_settings = VadSettings(
            threshold=threshold,
            min_speech_ms=settings.min_speech_ms,
            min_silence_ms=settings.min_silence_ms,
            speech_pad_ms=settings.speech_pad_ms,
            # An explicitly configured exit threshold is part of the segmentation being
            # measured; dropping it would silently re-run each trial with Silero's default
            # instead, and the stability table would describe settings nobody is using.
            # It is capped at the trial threshold because a neg_threshold above the entry
            # threshold is not a valid state machine (VadSettings rejects it).
            neg_threshold=(
                None if settings.neg_threshold is None
                else min(settings.neg_threshold, threshold)
            ),
        )
        segments = segments_from_probabilities(probabilities, total_samples, trial_settings)
        shifts: list[int] = []
        matched = 0
        for reference in baseline:
            overlapping = [
                candidate
                for candidate in segments
                if candidate.start < reference.end and candidate.end > reference.start
            ]
            if not overlapping:
                continue
            matched += 1
            best = min(overlapping, key=lambda c: abs(c.start - reference.start))
            shifts.append(abs(best.start - reference.start))
            shifts.append(abs(best.end - reference.end))
        speech = sum(segment.duration for segment in segments)
        trials.append(
            ThresholdTrial(
                threshold=threshold,
                segment_count=len(segments),
                speech_samples=speech,
                speech_ratio=speech / total_samples if total_samples else 0.0,
                boundary_shift_samples=_summary(shifts),
                matched_segments=matched,
            )
        )
    return tuple(trials)


@dataclass(frozen=True, slots=True)
class ReferenceZoom:
    """One human-made zoom clip on the reference timeline, in absolute frames."""

    name: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ReferenceDiagnostics:
    """How detected speech lines up with a human edit.

    **This is not an accuracy measurement.** The reference timeline is one editor's taste:
    they deliberately leave speech un-zoomed, and they zoom for reasons speech cannot
    express. Read these numbers as "does the speech track look like it could plausibly drive
    that edit", never as a score for the VAD.
    """

    reference_timeline: str
    zoom_count: int
    zooms_overlapping_speech: int
    zooms_without_speech: int
    speech_without_zoom: int
    speech_start_to_zoom_start_frames: dict[str, int | None]
    speech_end_to_reset_start_frames: dict[str, int | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_timeline": self.reference_timeline,
            "zoom_count": self.zoom_count,
            "zooms_overlapping_speech": self.zooms_overlapping_speech,
            "zooms_without_speech": self.zooms_without_speech,
            "speech_without_zoom": self.speech_without_zoom,
            "speech_start_to_zoom_start_frames": self.speech_start_to_zoom_start_frames,
            "speech_end_to_reset_start_frames": self.speech_end_to_reset_start_frames,
            "caveat": (
                "qualitative editing-reference diagnostics, NOT VAD accuracy: the reference "
                "timeline encodes editorial choices that speech alone cannot predict"
            ),
        }


def compare_to_reference_zooms(
    segments: Sequence[SpeechSegment],
    enter_zooms: Sequence[ReferenceZoom],
    reset_zooms: Sequence[ReferenceZoom],
    reference_timeline: str,
) -> ReferenceDiagnostics:
    """Qualitative alignment between speech and a human's zoom-in/reset clips."""

    enter_offsets: list[int] = []
    reset_offsets: list[int] = []
    overlapping = 0

    for zoom in enter_zooms:
        touching = [s for s in segments if s.start_frame < zoom.end and s.end_frame > zoom.start]
        if not touching:
            continue
        overlapping += 1
        nearest = min(touching, key=lambda s: abs(s.start_frame - zoom.start))
        enter_offsets.append(zoom.start - nearest.start_frame)

    for zoom in reset_zooms:
        earlier = [s for s in segments if s.end_frame <= zoom.start]
        if earlier:
            reset_offsets.append(zoom.start - max(s.end_frame for s in earlier))

    zoomed_speech = {
        index
        for zoom in enter_zooms
        for index, s in enumerate(segments)
        if s.start_frame < zoom.end and s.end_frame > zoom.start
    }
    return ReferenceDiagnostics(
        reference_timeline=reference_timeline,
        zoom_count=len(enter_zooms),
        zooms_overlapping_speech=overlapping,
        zooms_without_speech=len(enter_zooms) - overlapping,
        speech_without_zoom=len(segments) - len(zoomed_speech),
        speech_start_to_zoom_start_frames=_summary(sorted(enter_offsets)),
        speech_end_to_reset_start_frames=_summary(sorted(reset_offsets)),
    )


@dataclass(frozen=True, slots=True)
class PlanReferenceDiagnostics:
    """How an automatic plan lines up with a human's zoom clips.

    **A diagnostic, never a score, and never a tuning target.** The human timeline contains
    deliberate choices the planner cannot know: speech left un-zoomed on purpose, zooms held
    for rhythm. A human correction pass is part of how the product is meant to work, so
    "planned placements with no manual equivalent" is expected output, not error count.
    """

    reference_timeline: str
    planned_x1: int
    manual_x1: int
    planned_x0: int
    manual_x0: int
    matched_x1: int
    planned_x1_without_manual: int
    manual_x1_without_planned: int
    start_offset_frames: dict[str, int | None]
    duration_ratio_percent: dict[str, int | None]
    reset_offset_frames: dict[str, int | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_timeline": self.reference_timeline,
            "planned_x1": self.planned_x1,
            "manual_x1": self.manual_x1,
            "planned_x0": self.planned_x0,
            "manual_x0": self.manual_x0,
            "matched_x1": self.matched_x1,
            "planned_x1_without_manual": self.planned_x1_without_manual,
            "manual_x1_without_planned": self.manual_x1_without_planned,
            "start_offset_frames": self.start_offset_frames,
            "duration_ratio_percent": self.duration_ratio_percent,
            "reset_offset_frames": self.reset_offset_frames,
            "caveat": (
                "qualitative comparison only: the human timeline encodes editorial choices "
                "(deliberately un-zoomed speech, held zooms) that no planner can derive, and "
                "the planner is NOT tuned to reproduce it"
            ),
        }


def compare_plan_to_reference(
    planned_x1: Sequence[tuple[int, int]],
    planned_x0: Sequence[tuple[int, int]],
    manual_x1: Sequence[ReferenceZoom],
    manual_x0: Sequence[ReferenceZoom],
    reference_timeline: str,
) -> PlanReferenceDiagnostics:
    """Match planned zoom-ins to manual ones by overlap and measure the offsets."""

    start_offsets: list[int] = []
    ratios: list[int] = []
    matched_manual: set[int] = set()
    matched = 0

    for start, end in planned_x1:
        overlapping = [
            (index, zoom)
            for index, zoom in enumerate(manual_x1)
            if zoom.start < end and zoom.end > start
        ]
        if not overlapping:
            continue
        matched += 1
        index, nearest = min(overlapping, key=lambda pair: abs(pair[1].start - start))
        matched_manual.add(index)
        start_offsets.append(start - nearest.start)
        manual_duration = nearest.end - nearest.start
        if manual_duration:
            ratios.append(round(100 * (end - start) / manual_duration))

    # Resets are matched to the nearest manual reset, with no window: a plan and an edit with
    # different reset counts would make any window an arbitrary filter, and the point of the
    # number is to show how far apart the two conventions are, not to hide the far ones.
    reset_offsets = [
        start - min(manual_x0, key=lambda zoom: abs(zoom.start - start)).start
        for start, _ in planned_x0
        if manual_x0
    ]

    return PlanReferenceDiagnostics(
        reference_timeline=reference_timeline,
        planned_x1=len(planned_x1),
        manual_x1=len(manual_x1),
        planned_x0=len(planned_x0),
        manual_x0=len(manual_x0),
        matched_x1=matched,
        planned_x1_without_manual=len(planned_x1) - matched,
        manual_x1_without_planned=len(manual_x1) - len(matched_manual),
        start_offset_frames=_summary(sorted(start_offsets)),
        duration_ratio_percent=_summary(sorted(ratios)),
        reset_offset_frames=_summary(sorted(reset_offsets)),
    )


def reference_zooms(
    timeline: TimelineSnapshot, *asset_names: str
) -> tuple[ReferenceZoom, ...]:
    """Every clip on any video track whose name matches one of the given zoom assets.

    Instances are recognised by name because Resolve gives a placed generator no link back
    to its Media Pool item (D008).

    Several names, because a facecam side of the graph now has three assets and a reset side
    three more: comparing a multi-level plan against a human edit means asking "was the frame
    zoomed here", not "was this one clip here". Results stay in timeline order so adjacent
    promotion clips read as the single cycle they are.
    """

    wanted = {name for name in asset_names if name}
    return tuple(
        sorted(
            (
                ReferenceZoom(name=item.name, start=item.start, end=item.end)
                for track in timeline.tracks_of("video")
                for item in track.items
                if item.name in wanted
            ),
            key=lambda zoom: (zoom.start, zoom.end, zoom.name),
        )
    )


def merge_adjacent(zooms: Sequence[ReferenceZoom]) -> tuple[ReferenceZoom, ...]:
    """Collapse touching clips into the zoom *cycles* a viewer actually sees.

    A multi-level cycle is several clips — `FACE_X1` then `FACE_X2` then `FACE_X3` — laid end
    to end with no gap, and counting those as three zooms would say a 14-cycle edit has 21.
    Only clips that touch exactly are merged; a gap of even one frame is a return to X0 and
    therefore a different cycle.
    """

    merged: list[ReferenceZoom] = []
    for zoom in sorted(zooms, key=lambda z: (z.start, z.end)):
        if merged and zoom.start <= merged[-1].end:
            # The cycle keeps the name of the clip that opened it: that is the transition the
            # viewer saw first, and the one a start offset is meaningfully measured against.
            merged[-1] = ReferenceZoom(
                merged[-1].name, merged[-1].start, max(merged[-1].end, zoom.end)
            )
        else:
            merged.append(zoom)
    return tuple(merged)


def segment_rows(
    segments: Sequence[SpeechSegment], timebase: Timebase
) -> list[dict[str, Any]]:
    """One reportable row per segment: frames, relative seconds and timeline timecode."""

    rows: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        start_offset = segment.start_frame - timebase.start_frame
        end_offset = segment.end_frame - timebase.start_frame
        rows.append(
            {
                "index": index,
                "start_frame": segment.start_frame,
                "end_frame": segment.end_frame,
                "duration_frames": segment.duration_frames,
                "start_seconds": float(start_offset / timebase.frame_rate),
                "end_seconds": float(end_offset / timebase.frame_rate),
                "duration_seconds": float(segment.duration_frames / timebase.frame_rate),
                "start_timecode": frames_to_timecode(segment.start_frame, timebase.frame_rate),
                "end_timecode": frames_to_timecode(segment.end_frame, timebase.frame_rate),
            }
        )
    return rows


__all__ = [
    "PlanReferenceDiagnostics",
    "ReferenceDiagnostics",
    "ReferenceZoom",
    "SegmentStatistics",
    "ThresholdTrial",
    "compare_plan_to_reference",
    "compare_to_reference_zooms",
    "merge_adjacent",
    "reference_zooms",
    "segment_rows",
    "segment_statistics",
    "threshold_stability",
]
