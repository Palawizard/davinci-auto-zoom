"""Exact conversion between audio samples and absolute Resolve timeline frames.

The VAD works in samples of a 16 kHz mono render; the planner works in integer timeline
frames whose origin is the timeline's own `GetStartFrame()` (216000 on the test project,
not 0). This module is the only place that bridges the two, and it does so with
`fractions.Fraction` so that no float error accumulates over a long timeline.

Rounding policy (deliberate, and tested):

* a segment's **start** frame is floored,
* a segment's **end** frame is ceiled,
* the resulting range is half-open `[start, end)`, matching `FrameRange` everywhere else.

Flooring the start and ceiling the end means the frame range always *covers* every audio
sample the VAD attributed to speech; it never trims speech away to make a rounder number.
The maximum error introduced at each boundary is therefore strictly less than one frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from davinci_auto_zoom.domain.models import FrameRange

#: Rounded NTSC frame rates as Resolve reports them, mapped to their exact value. Resolve
#: returns "23.976"/"29.97"/"59.94" from `GetSetting("timelineFrameRate")`, but the real
#: rate is 24000/1001 and friends; using the rounded decimal drifts by ~3.6 frames/hour.
NTSC_FRAME_RATES: dict[str, Fraction] = {
    "23.976": Fraction(24000, 1001),
    "23.98": Fraction(24000, 1001),
    "29.97": Fraction(30000, 1001),
    "47.952": Fraction(48000, 1001),
    "59.94": Fraction(60000, 1001),
    "119.88": Fraction(120000, 1001),
}


def frame_rate_fraction(value: float | int | str) -> Fraction:
    """Exact frame rate from whatever Resolve reports.

    Integer-ish rates (24, 25, 30, 50, 60) are exact already. The rounded NTSC decimals are
    mapped to their true `n * 1000 / 1001` value; anything else is taken literally.
    """

    text = str(value).strip()
    # Resolve reports "60.0" for 60; normalise so the NTSC table can be a plain lookup.
    if text.endswith(".0"):
        text = text[:-2]
    if text in NTSC_FRAME_RATES:
        return NTSC_FRAME_RATES[text]
    rate = Fraction(text)
    if rate <= 0:
        raise ValueError(f"frame rate must be > 0, got {value!r}")
    return rate


@dataclass(frozen=True, slots=True)
class Timebase:
    """Maps samples of a rendered audio file onto absolute timeline frames.

    `start_frame` is the timeline's own first frame, so sample 0 of the render is exactly
    that frame: the render must not be trimmed at either end for this to hold.
    """

    frame_rate: Fraction
    start_frame: int
    sample_rate: int = 16000

    def __post_init__(self) -> None:
        if self.frame_rate <= 0:
            raise ValueError("frame_rate must be > 0")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")
        if self.start_frame < 0:
            raise ValueError("start_frame must be >= 0")

    @classmethod
    def from_timeline(
        cls, frame_rate: float | int | str, start_frame: int, sample_rate: int = 16000
    ) -> Timebase:
        return cls(frame_rate_fraction(frame_rate), start_frame, sample_rate)

    @property
    def frames_per_sample(self) -> Fraction:
        return self.frame_rate / self.sample_rate

    def sample_to_frame_offset(self, sample: int) -> Fraction:
        """Exact (unrounded) number of frames between the render start and `sample`."""

        return sample * self.frames_per_sample

    def sample_to_seconds(self, sample: int) -> Fraction:
        return Fraction(sample, self.sample_rate)

    def start_frame_of(self, sample: int) -> int:
        """Absolute timeline frame containing `sample` (floor)."""

        return self.start_frame + math.floor(self.sample_to_frame_offset(sample))

    def end_frame_of(self, sample: int) -> int:
        """Absolute exclusive end frame covering `sample` (ceil)."""

        return self.start_frame + math.ceil(self.sample_to_frame_offset(sample))

    def frame_to_sample(self, frame: int) -> int:
        """Nearest sample index for an absolute timeline frame. Inverse of the above.

        Rounds half up; a frame is longer than a sample at any realistic rate, so this is a
        lossy direction by construction.
        """

        offset = (frame - self.start_frame) * Fraction(self.sample_rate) / self.frame_rate
        return math.floor(offset + Fraction(1, 2))

    def frame_range(self, start_sample: int, end_sample: int) -> FrameRange:
        """Half-open frame range covering `[start_sample, end_sample)`.

        A sample range shorter than one frame would floor and ceil onto the same frame;
        `FrameRange` forbids an empty range, so it is widened to a single frame rather than
        silently dropped.
        """

        if end_sample <= start_sample:
            raise ValueError(f"end_sample must be > start_sample ({start_sample}, {end_sample})")
        if start_sample < 0:
            raise ValueError("start_sample must be >= 0")
        start = self.start_frame_of(start_sample)
        end = self.end_frame_of(end_sample)
        return FrameRange(start, max(end, start + 1))

    def frames_for_samples(self, samples: int) -> Fraction:
        """Exact duration of `samples` expressed in frames. For duration verification."""

        return self.sample_to_frame_offset(samples)


def frames_to_timecode(frame: int, frame_rate: Fraction) -> str:
    """Non-drop `HH:MM:SS:FF` for an absolute timeline frame.

    Display only — the planner never uses timecode. Drop-frame notation is deliberately not
    produced: it renumbers frames for human readability and would disagree with the integer
    frames every other line of the report shows. For an NTSC rate the frame counter runs to
    the next whole number of frames per second (29 for 29.97), which is what Resolve does in
    non-drop mode.
    """

    if frame < 0:
        raise ValueError("frame must be >= 0")
    frames_per_second = math.ceil(frame_rate)
    seconds, frames = divmod(frame, frames_per_second)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


__all__ = [
    "NTSC_FRAME_RATES",
    "Timebase",
    "frame_rate_fraction",
    "frames_to_timecode",
]
