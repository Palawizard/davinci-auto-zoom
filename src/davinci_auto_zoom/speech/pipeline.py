"""Rendered audio -> normalized PCM -> VAD -> SpeechSegments in absolute timeline frames.

This is the seam between "a file exists on disk" and "the planner has speech segments". It
knows about ffmpeg and the VAD but nothing about Resolve, so the whole chain below the render
can be exercised on any WAV.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.models import SpeechSegment
from davinci_auto_zoom.domain.speech_report import (
    ReferenceDiagnostics,
    SegmentStatistics,
    ThresholdTrial,
    segment_rows,
    segment_statistics,
    threshold_stability,
)
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.vad import VadSettings
from davinci_auto_zoom.speech.audio import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    NormalizedAudio,
    normalized_audio,
)
from davinci_auto_zoom.speech.silero import SileroVad, SpeechAnalysis, analyze_samples

#: Thresholds probed around the default purely to measure stability. Not a tuning sweep: the
#: default stays whatever Silero recommends unless there is clear evidence against it.
STABILITY_THRESHOLDS: tuple[float, ...] = (0.4, 0.5, 0.6)

#: A render can legitimately differ from the timeline length by a fraction of a frame
#: (sample/frame rounding at both ends, plus the codec's own block alignment). Anything
#: beyond a couple of frames means the render was trimmed, padded or ranged, which would
#: offset every speech frame — so it fails the probe rather than being explained away.
DURATION_TOLERANCE_FRAMES = 2.0


@dataclass(frozen=True, slots=True)
class DurationCheck:
    """Does the rendered audio really cover the timeline range it claims to?"""

    expected_frames: int
    expected_seconds: float
    rendered_samples: int
    rendered_seconds: float
    delta_seconds: float
    delta_frames: float
    tolerance_frames: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_frames": self.expected_frames,
            "expected_seconds": self.expected_seconds,
            "rendered_samples": self.rendered_samples,
            "rendered_seconds": self.rendered_seconds,
            "delta_seconds": self.delta_seconds,
            "delta_frames": self.delta_frames,
            "tolerance_frames": self.tolerance_frames,
            "ok": self.ok,
        }


def verify_duration(
    expected_frames: int,
    sample_count: int,
    frame_rate: Fraction,
    sample_rate: int = TARGET_SAMPLE_RATE,
    tolerance_frames: float = DURATION_TOLERANCE_FRAMES,
) -> DurationCheck:
    """Compare rendered audio length against the timeline range it was rendered from."""

    expected_seconds = float(Fraction(expected_frames) / frame_rate)
    rendered_seconds = sample_count / sample_rate
    delta_seconds = rendered_seconds - expected_seconds
    delta_frames = float(Fraction(delta_seconds).limit_denominator(10**9) * frame_rate)
    return DurationCheck(
        expected_frames=expected_frames,
        expected_seconds=expected_seconds,
        rendered_samples=sample_count,
        rendered_seconds=rendered_seconds,
        delta_seconds=delta_seconds,
        delta_frames=delta_frames,
        tolerance_frames=tolerance_frames,
        ok=abs(delta_frames) <= tolerance_frames,
    )


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """One complete analysis, in the coordinate system the planner will consume."""

    timebase: Timebase
    audio: NormalizedAudio
    analysis: SpeechAnalysis
    segments: tuple[SpeechSegment, ...]
    statistics: SegmentStatistics
    duration: DurationCheck | None = None
    stability: tuple[ThresholdTrial, ...] = ()
    reference: ReferenceDiagnostics | None = None
    ffmpeg_seconds: float = 0.0
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        analysis = self.analysis
        return {
            "timebase": {
                "frame_rate": str(self.timebase.frame_rate),
                "frame_rate_float": float(self.timebase.frame_rate),
                "start_frame": self.timebase.start_frame,
                "sample_rate": self.timebase.sample_rate,
                "rounding_policy": "start=floor, end=ceil, half-open [start, end)",
                "max_rounding_error_frames": "< 1 at each boundary",
            },
            "audio": {
                "path": str(self.audio.path),
                "sample_rate": self.audio.sample_rate,
                "channels": TARGET_CHANNELS,
                "sample_count": self.audio.sample_count,
                "duration_seconds": self.audio.duration_seconds,
            },
            "vad": {
                "implementation": "silero-vad ONNX via onnxruntime (CPU)",
                "model_version": analysis.model_version,
                "model_checksum": analysis.model_checksum,
                "onnxruntime_version": analysis.onnxruntime_version,
                "settings": {
                    "threshold": analysis.settings.threshold,
                    "neg_threshold": analysis.settings.effective_neg_threshold,
                    "min_speech_ms": analysis.settings.min_speech_ms,
                    "min_silence_ms": analysis.settings.min_silence_ms,
                    "speech_pad_ms": analysis.settings.speech_pad_ms,
                },
                "speech_ratio": analysis.speech_ratio,
            },
            "timings": {
                "ffmpeg_seconds": self.ffmpeg_seconds,
                "model_load_seconds": analysis.model_load_seconds,
                "inference_seconds": analysis.inference_seconds,
            },
            "duration_check": self.duration.to_dict() if self.duration else None,
            "statistics": self.statistics.to_dict(),
            "segments": segment_rows(self.segments, self.timebase),
            "threshold_stability": [trial.to_dict() for trial in self.stability],
            "editing_reference_diagnostics": (
                self.reference.to_dict() if self.reference else None
            ),
            "notes": list(self.notes),
        }

    def to_text(self) -> str:
        analysis = self.analysis
        stats = self.statistics
        lines = [
            f"speech analysis of {self.audio.path.name}",
            f"  audio     : {self.audio.sample_count} samples @ {self.audio.sample_rate} Hz "
            f"mono = {self.audio.duration_seconds:.3f}s",
            f"  timebase  : {float(self.timebase.frame_rate):.6f} fps, timeline starts at "
            f"frame {self.timebase.start_frame} "
            "(segment start=floor, end=ceil, half-open)",
            f"  model     : {analysis.model_version} via onnxruntime "
            f"{analysis.onnxruntime_version}",
            f"  settings  : threshold={analysis.settings.threshold} "
            f"neg={analysis.settings.effective_neg_threshold:.2f} "
            f"min_speech={analysis.settings.min_speech_ms}ms "
            f"min_silence={analysis.settings.min_silence_ms}ms "
            f"pad={analysis.settings.speech_pad_ms}ms",
            f"  timings   : ffmpeg={self.ffmpeg_seconds:.2f}s "
            f"model_load={analysis.model_load_seconds:.2f}s "
            f"inference={analysis.inference_seconds:.2f}s",
        ]
        if self.duration:
            check = self.duration
            lines.append(
                f"  duration  : expected {check.expected_frames} frames "
                f"({check.expected_seconds:.3f}s), rendered {check.rendered_samples} samples "
                f"({check.rendered_seconds:.3f}s), delta {check.delta_seconds:+.4f}s "
                f"({check.delta_frames:+.3f} frames) -> {'ok' if check.ok else 'MISMATCH'}"
            )
        lines.append("")
        lines.append(
            "  idx  start_frame    end_frame  frames   start_tc     end_tc      "
            "start_s    end_s"
        )
        for row in segment_rows(self.segments, self.timebase):
            lines.append(
                f"  {row['index']:>3}  {row['start_frame']:>11}  {row['end_frame']:>11}  "
                f"{row['duration_frames']:>6}   {row['start_timecode']}  "
                f"{row['end_timecode']}  {row['start_seconds']:>8.2f} "
                f"{row['end_seconds']:>8.2f}"
            )
        speech_seconds = float(stats.speech_frames / self.timebase.frame_rate)
        lines.extend(
            [
                "",
                f"  segments      : {stats.segment_count}",
                f"  speech        : {stats.speech_frames} frames ({speech_seconds:.2f}s), "
                f"{stats.speech_ratio * 100:.1f}% of the analysed range",
                f"  durations     : min={stats.duration_frames['min']} "
                f"median={stats.duration_frames['median']} "
                f"max={stats.duration_frames['max']} frames",
                f"  gaps          : min={stats.gap_frames['min']} "
                f"median={stats.gap_frames['median']} "
                f"max={stats.gap_frames['max']} frames",
            ]
        )
        if self.stability:
            lines.append("")
            lines.append(
                "  threshold stability (same probabilities, post-processing re-run):"
            )
            lines.append(
                "    thr   segments  speech_ratio  matched  boundary shift min/med/max (samples)"
            )
            for trial in self.stability:
                shift = trial.boundary_shift_samples
                lines.append(
                    f"    {trial.threshold:.2f}  {trial.segment_count:>8}  "
                    f"{trial.speech_ratio * 100:>11.1f}%  {trial.matched_segments:>7}  "
                    f"{shift['min']}/{shift['median']}/{shift['max']}"
                )
        if self.reference:
            reference = self.reference
            lines.extend(
                [
                    "",
                    f"  editing-reference diagnostics vs {reference.reference_timeline} "
                    "(NOT VAD accuracy — the human edit is taste, not ground truth):",
                    f"    zoom-in clips              : {reference.zoom_count}",
                    f"    overlapping a speech region: {reference.zooms_overlapping_speech}",
                    f"    with no speech at all      : {reference.zooms_without_speech}",
                    f"    speech regions with no zoom: {reference.speech_without_zoom}",
                    f"    speech start -> zoom start : min/med/max = "
                    f"{reference.speech_start_to_zoom_start_frames['min']}/"
                    f"{reference.speech_start_to_zoom_start_frames['median']}/"
                    f"{reference.speech_start_to_zoom_start_frames['max']} frames",
                    f"    speech end -> reset start  : min/med/max = "
                    f"{reference.speech_end_to_reset_start_frames['min']}/"
                    f"{reference.speech_end_to_reset_start_frames['median']}/"
                    f"{reference.speech_end_to_reset_start_frames['max']} frames",
                ]
            )
        lines.extend(f"  note: {note}" for note in self.notes)
        return "\n".join(lines)


def analyze_audio_file(
    source: Path,
    destination: Path,
    timebase: Timebase,
    settings: VadSettings | None = None,
    *,
    expected_frames: int | None = None,
    engine: SileroVad | None = None,
    stability_thresholds: tuple[float, ...] = STABILITY_THRESHOLDS,
    tolerance_frames: float = DURATION_TOLERANCE_FRAMES,
) -> SpeechResult:
    """Normalize `source` to `destination`, run the VAD, and map results onto the timeline."""

    settings = settings or VadSettings()
    started = time.perf_counter()
    audio = normalized_audio(source, destination)
    ffmpeg_seconds = time.perf_counter() - started

    analysis = analyze_samples(audio.samples, settings, engine=engine)
    duration = (
        verify_duration(
            expected_frames,
            audio.sample_count,
            timebase.frame_rate,
            audio.sample_rate,
            tolerance_frames,
        )
        if expected_frames is not None
        else None
    )
    segments = analysis.to_segments(timebase)
    analysed_frames = expected_frames or int(
        timebase.frames_for_samples(audio.sample_count)
    )
    return SpeechResult(
        timebase=timebase,
        audio=audio,
        analysis=analysis,
        segments=segments,
        statistics=segment_statistics(segments, analysed_frames),
        duration=duration,
        stability=threshold_stability(
            analysis.probabilities, audio.sample_count, settings, stability_thresholds
        ),
        ffmpeg_seconds=ffmpeg_seconds,
    )


__all__ = [
    "DURATION_TOLERANCE_FRAMES",
    "STABILITY_THRESHOLDS",
    "DurationCheck",
    "SpeechResult",
    "analyze_audio_file",
    "verify_duration",
]
