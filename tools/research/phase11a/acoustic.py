"""Objective audio facts measured around a cut. No editorial reading happens here.

Same split the product enforces (D050): this module answers "how loud was it", "was there
speech", "how long was the pause" and stops there. Whether any of that *deserves* a reset is
the question the study asks elsewhere.

Everything is pure: it consumes the `(frame, dB)` envelope and the speech ranges the shipped
pipeline already produces, and it never opens an audio file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from davinci_auto_zoom.domain.dynamics import EnergyPoint


@dataclass(frozen=True, slots=True)
class CutAudio:
    """What the voice was doing on either side of one cut."""

    cut_frame: int
    #: Median dB of the window before / after the cut. `None` when the window has no points,
    #: which happens at an island edge and is not the same as "silent".
    pre_db: float | None
    post_db: float | None
    #: Quietest point of the window straddling the cut, and where it sat.
    local_min_db: float | None
    local_min_frame: int | None
    #: Frames of continuous non-speech containing the cut. 0 means the cut is inside speech.
    pause_frames: int
    #: Frames from the cut back to the end of the previous speech run, and forward to the
    #: start of the next. `None` when there is none inside the island.
    silence_before: int | None
    silence_after: int | None

    @property
    def discontinuity_db(self) -> float | None:
        """Post minus pre. A positive number means the edit jumps into a louder delivery."""

        if self.pre_db is None or self.post_db is None:
            return None
        return self.post_db - self.pre_db

    @property
    def dip_db(self) -> float | None:
        """How far below the surrounding voice level the cut's neighbourhood dropped."""

        levels = [level for level in (self.pre_db, self.post_db) if level is not None]
        if not levels or self.local_min_db is None:
            return None
        return max(levels) - self.local_min_db


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def cut_audio(
    cut_frame: int,
    envelope: Sequence[EnergyPoint],
    speech_ranges: Sequence[tuple[int, int]],
    *,
    window_frames: int,
    bounds: tuple[int, int],
) -> CutAudio:
    """Measure one cut. `bounds` is the island, so no window ever reaches into another Short."""

    if window_frames <= 0:
        raise ValueError("window_frames must be > 0")
    low, high = bounds
    if not low <= cut_frame <= high:
        raise ValueError(f"cut {cut_frame} is outside its island [{low},{high})")

    pre_start = max(low, cut_frame - window_frames)
    post_end = min(high, cut_frame + window_frames)
    pre = [p.db for p in envelope if pre_start <= p.frame < cut_frame]
    post = [p.db for p in envelope if cut_frame <= p.frame < post_end]
    around = [p for p in envelope if pre_start <= p.frame < post_end]
    lowest = min(around, key=lambda p: p.db) if around else None

    ordered = sorted(speech_ranges)
    inside = next((r for r in ordered if r[0] <= cut_frame < r[1]), None)
    if inside is not None:
        pause = 0
        silence_before: int | None = 0
        silence_after: int | None = 0
    else:
        previous_end = max(
            (end for _, end in ordered if end <= cut_frame), default=None
        )
        next_start = min(
            (start for start, _ in ordered if start >= cut_frame), default=None
        )
        silence_before = cut_frame - previous_end if previous_end is not None else None
        silence_after = next_start - cut_frame if next_start is not None else None
        gap_start = previous_end if previous_end is not None else low
        gap_end = next_start if next_start is not None else high
        pause = max(0, gap_end - gap_start)

    return CutAudio(
        cut_frame=cut_frame,
        pre_db=_median(pre),
        post_db=_median(post),
        local_min_db=lowest.db if lowest else None,
        local_min_frame=lowest.frame if lowest else None,
        pause_frames=pause,
        silence_before=silence_before,
        silence_after=silence_after,
    )


__all__ = ["CutAudio", "cut_audio"]
