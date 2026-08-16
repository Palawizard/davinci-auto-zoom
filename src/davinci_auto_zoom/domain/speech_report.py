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


def reference_zooms(
    timeline: TimelineSnapshot, asset_name: str
) -> tuple[ReferenceZoom, ...]:
    """Every clip on any video track whose name matches a configured zoom asset.

    Instances are recognised by name because Resolve gives a placed generator no link back
    to its Media Pool item (D008).
    """

    return tuple(
        ReferenceZoom(name=item.name, start=item.start, end=item.end)
        for track in timeline.tracks_of("video")
        for item in track.items
        if item.name == asset_name
    )


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
    "ReferenceDiagnostics",
    "ReferenceZoom",
    "SegmentStatistics",
    "ThresholdTrial",
    "compare_to_reference_zooms",
    "reference_zooms",
    "segment_rows",
    "segment_statistics",
    "threshold_stability",
]
