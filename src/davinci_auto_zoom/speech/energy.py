"""Normalized PCM -> objective short-time energy envelope. No editorial decision here.

This is the audio half of Phase 8c and it is deliberately dull: RMS over a sliding window,
converted to dBFS, lightly smoothed. It answers "how loud was the voice at this instant",
never "does this deserve a tighter zoom" — the same boundary `speech/` already keeps for the
VAD (D021). Reading the envelope as valleys and promotion cues happens in `domain/dynamics.py`.

It runs on **the same `samples` array the VAD consumes** (D050): one Resolve render, one
ffmpeg normalization, one decode. A second pass over an already-decoded numpy array costs
milliseconds and cannot drift out of sync with the speech segments the way a second render
could.
"""

from __future__ import annotations

import numpy as np

from davinci_auto_zoom.domain.dynamics import (
    EnergyEnvelope,
    EnergyPoint,
    EnergySettings,
)
from davinci_auto_zoom.domain.timebase import Timebase

#: Floor for the log: -140 dBFS. Digital silence has no logarithm, and a rendered gap really
#: is exactly zero, so it needs a defined value rather than `-inf` (which poisons any average).
SILENCE_FLOOR = 1e-7


def energy_envelope(
    samples: np.ndarray,
    timebase: Timebase,
    settings: EnergySettings | None = None,
) -> EnergyEnvelope:
    """Short-time RMS of `samples` in dBFS, on absolute timeline frames.

    Each point is stamped at the **centre** of its window, which is what makes an onset land
    on the frame the ear hears it rather than `window_ms/2` early.
    """

    settings = settings or EnergySettings()
    window = max(1, int(timebase.sample_rate * settings.window_ms / 1000))
    hop = max(1, int(timebase.sample_rate * settings.hop_ms / 1000))
    count = int(samples.shape[0])
    if count < window:
        return EnergyEnvelope((), settings)

    starts = np.arange(0, count - window + 1, hop)
    # Cumulative sums in float64: a 16-bit waveform squared over a minute overflows nothing
    # here, and the difference of two partial sums is exact enough for a dB curve.
    squares = np.concatenate(([0.0], np.cumsum(np.asarray(samples, dtype=np.float64) ** 2)))
    rms = np.sqrt((squares[starts + window] - squares[starts]) / window)
    db = 20.0 * np.log10(np.maximum(rms, SILENCE_FLOOR))

    span = max(1, round(settings.smoothing_ms / settings.hop_ms))
    if span > 1 and db.shape[0] >= span:
        # Edge-padded, not zero-padded: `mode="same"` on a raw convolution would average the
        # first and last points against implicit 0 dB and invent a loud burst at each end of
        # every render — which is exactly where a real burst tends to start.
        pad = span // 2
        db = np.convolve(np.pad(db, (pad, span - 1 - pad), mode="edge"), np.ones(span) / span,
                         mode="valid")

    centres = starts + window / 2.0
    frames = [timebase.start_frame_of(int(round(c))) for c in centres]
    return EnergyEnvelope(
        tuple(EnergyPoint(frame, float(level)) for frame, level in zip(frames, db, strict=True)),
        settings,
    )


__all__ = ["SILENCE_FLOOR", "energy_envelope"]
