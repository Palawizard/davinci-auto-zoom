from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

Frame = int


@dataclass(frozen=True, slots=True, order=True)
class FrameRange:
    """Half-open timeline frame range [start, end)."""

    start: Frame
    end: Frame

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("start must be >= 0")
        if self.end <= self.start:
            raise ValueError("end must be > start")

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    """One region where the voice track actually carries speech.

    **This is a speech fact, not an editing decision.** A segment says "the creator was
    talking here"; it does not say a zoom belongs here. Turning segments into zoom actions
    (how much silence justifies a reset, whether to snap to a cut, minimum zoom length) is
    the planner's job.

    `frames` is half-open `[start_frame, end_frame)` in **absolute** timeline frames — the
    same coordinate space as `TimelineItem.GetStart()`/`GetEnd()`, so a timeline starting at
    frame 216000 yields segments in the 216000+ range, never 0-based offsets.
    """

    frames: FrameRange
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def start_frame(self) -> Frame:
        """First frame of the segment, inclusive."""
        return self.frames.start

    @property
    def end_frame(self) -> Frame:
        """One past the last frame of the segment (exclusive)."""
        return self.frames.end

    @property
    def duration_frames(self) -> int:
        return self.frames.duration


def normalize_speech_segments(
    segments: Iterable[SpeechSegment],
) -> tuple[SpeechSegment, ...]:
    """Sorted, non-overlapping segments.

    Providers may emit segments in any order, and two segments that overlap or merely touch
    describe one continuous stretch of speech, so they are merged. The merged confidence is
    the lowest of the inputs: a merged region is only as certain as its weakest part.
    """

    ordered = sorted(segments, key=lambda s: (s.frames.start, s.frames.end))
    merged: list[SpeechSegment] = []
    for segment in ordered:
        if merged and segment.frames.start <= merged[-1].frames.end:
            previous = merged[-1]
            confidences = [
                c for c in (previous.confidence, segment.confidence) if c is not None
            ]
            merged[-1] = SpeechSegment(
                FrameRange(
                    previous.frames.start, max(previous.frames.end, segment.frames.end)
                ),
                min(confidences) if confidences else None,
            )
        else:
            merged.append(segment)
    return tuple(merged)


# The Phase 0 scaffold also carried `ZoomState` / `ZoomActionKind` / `ZoomAction`: a state
# machine emitting ENTER/RESET events at single frames. Phase 4 replaced it with
# `domain.planner.AssetPlacement`, which carries the full half-open range of each asset
# instance, because an event pair forces the executor to re-derive the durations the planner
# already computed. A further facecam level becomes another role and another placement.
