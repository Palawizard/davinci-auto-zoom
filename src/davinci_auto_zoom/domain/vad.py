"""Pure post-processing that turns per-window speech probabilities into sample ranges.

This is deliberately separate from the ONNX engine: the engine's only job is to produce one
probability per 512-sample window, and everything that decides *where a speech segment
begins and ends* is plain arithmetic that runs without numpy, onnxruntime, ffmpeg or
Resolve — so it can be tested exhaustively.

The state machine is a faithful port of Silero's own `get_speech_timestamps`
(https://github.com/snakers4/silero-vad, MIT, v6.2.1) so that our results match the
reference implementation the model was tuned against. Two deliberate differences:

* `max_speech_duration_s` and its silence-bookkeeping are omitted. Nothing in this project
  wants a speech segment force-split at an arbitrary length, and dropping it removes the
  most intricate third of the loop.
* the audio is a file, not a stream, so there is no partial-window bookkeeping.

**Everything here is technical VAD behaviour, not editorial policy.** `min_silence_ms` is
"how long must the model stay quiet before this really is the end of an utterance", which is
a property of speech; it is *not* "how long should the tool wait before zooming out", which
is a planner decision and lives under `[planner]` in the config. Likewise `speech_pad_ms`
exists so a segment does not clip the first/last phoneme — it is not zoom lead-in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Silero VAD operates on fixed 512-sample windows at 16 kHz (32 ms). Not configurable:
#: the model's exported graph accepts nothing else.
WINDOW_SAMPLES = 512
SAMPLE_RATE = 16000

#: Silero's documented default gap between the entry and exit thresholds.
NEG_THRESHOLD_MARGIN = 0.15
MIN_NEG_THRESHOLD = 0.01


@dataclass(frozen=True, slots=True)
class VadSettings:
    """Technical Silero parameters. Defaults are the official ones (v6.2.1)."""

    threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 100
    speech_pad_ms: int = 30
    #: Exit threshold. `None` means Silero's default of `threshold - 0.15`.
    neg_threshold: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("threshold must be strictly between 0 and 1")
        if self.neg_threshold is not None and not 0.0 < self.neg_threshold <= self.threshold:
            raise ValueError("neg_threshold must be > 0 and <= threshold")
        for name in ("min_speech_ms", "min_silence_ms", "speech_pad_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")

    @property
    def effective_neg_threshold(self) -> float:
        if self.neg_threshold is not None:
            return self.neg_threshold
        return max(self.threshold - NEG_THRESHOLD_MARGIN, MIN_NEG_THRESHOLD)


@dataclass(frozen=True, slots=True)
class SampleSegment:
    """Half-open `[start, end)` range of audio samples judged to contain speech."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("start must be >= 0")
        if self.end <= self.start:
            raise ValueError("end must be > start")

    @property
    def duration(self) -> int:
        return self.end - self.start


def segments_from_probabilities(
    probabilities: Sequence[float],
    total_samples: int,
    settings: VadSettings | None = None,
    *,
    window_samples: int = WINDOW_SAMPLES,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[SampleSegment, ...]:
    """Speech sample ranges, sorted, non-overlapping, every `start < end`.

    `probabilities[i]` is the model's speech probability for the window starting at sample
    `i * window_samples`.
    """

    settings = settings or VadSettings()
    threshold = settings.threshold
    neg_threshold = settings.effective_neg_threshold
    min_speech_samples = sample_rate * settings.min_speech_ms / 1000
    min_silence_samples = sample_rate * settings.min_silence_ms / 1000
    pad_samples = int(sample_rate * settings.speech_pad_ms / 1000)

    triggered = False
    start_sample = 0
    temp_end = 0
    raw: list[list[int]] = []

    for index, probability in enumerate(probabilities):
        current = window_samples * index

        if probability >= threshold and temp_end:
            temp_end = 0
        if probability >= threshold and not triggered:
            triggered = True
            start_sample = current
            continue
        if probability < neg_threshold and triggered:
            if not temp_end:
                temp_end = current
            if current - temp_end < min_silence_samples:
                continue
            if temp_end - start_sample > min_speech_samples:
                raw.append([start_sample, temp_end])
            temp_end = 0
            triggered = False

    if triggered and (total_samples - start_sample) > min_speech_samples:
        raw.append([start_sample, total_samples])

    # Padding, applied exactly as Silero does: a gap smaller than two pads is split evenly
    # between its neighbours so padding can never make two segments overlap.
    for index, segment in enumerate(raw):
        if index == 0:
            segment[0] = max(0, segment[0] - pad_samples)
        if index != len(raw) - 1:
            gap = raw[index + 1][0] - segment[1]
            if gap < 2 * pad_samples:
                segment[1] += gap // 2
                raw[index + 1][0] = max(0, raw[index + 1][0] - gap // 2)
            else:
                segment[1] = min(total_samples, segment[1] + pad_samples)
                raw[index + 1][0] = max(0, raw[index + 1][0] - pad_samples)
        else:
            segment[1] = min(total_samples, segment[1] + pad_samples)

    return tuple(SampleSegment(start, end) for start, end in raw if end > start)


__all__ = [
    "SAMPLE_RATE",
    "WINDOW_SAMPLES",
    "SampleSegment",
    "VadSettings",
    "segments_from_probabilities",
]
