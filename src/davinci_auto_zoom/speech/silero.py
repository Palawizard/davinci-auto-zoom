"""Silero VAD speech provider: ONNX Runtime on CPU, no PyTorch, no Resolve.

This module is the whole speech engine. It is usable on a plain WAV file with no DaVinci
Resolve session anywhere in sight, which is what makes the provider testable, tunable and
portable independently of the Resolve integration.

The model is vendored and checksummed — see `models/PROVENANCE.md` for its version, origin
and license. The inference loop is a direct implementation of the graph's documented
contract (64 context samples + one 512-sample window, plus a carried 2x1x128 state); the
segmentation that follows lives in `domain.vad` and runs without onnxruntime at all.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from davinci_auto_zoom.domain.models import SpeechSegment, normalize_speech_segments
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.vad import (
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    SampleSegment,
    VadSettings,
    segments_from_probabilities,
)
from davinci_auto_zoom.speech.audio import NormalizedAudio, load_pcm16_mono

MODEL_DIRECTORY = Path(__file__).parent / "models"
MODEL_FILENAME = "silero_vad.onnx"
#: Pinned upstream release. Any change here is a deliberate behaviour change.
MODEL_VERSION = "silero-vad v6.2.1 (opset 16)"
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

CONTEXT_SAMPLES = 64
STATE_SHAPE = (2, 1, 128)


class SpeechEngineError(RuntimeError):
    """The VAD could not run. The message is meant to be shown to a user as-is."""


def model_path() -> Path:
    path = MODEL_DIRECTORY / MODEL_FILENAME
    if not path.is_file():
        raise SpeechEngineError(
            f"the vendored Silero VAD model is missing at {path}. Reinstall the package; "
            "the model ships inside it and is never downloaded at runtime."
        )
    return path


def model_checksum(path: Path | None = None) -> str:
    return hashlib.sha256((path or model_path()).read_bytes()).hexdigest()


def verify_model(path: Path | None = None) -> str:
    """Confirm the vendored model is byte-identical to the pinned release."""

    path = path or model_path()
    digest = model_checksum(path)
    if digest != MODEL_SHA256:
        raise SpeechEngineError(
            f"Silero VAD model checksum mismatch at {path}:\n"
            f"  expected {MODEL_SHA256}\n  found    {digest}\n"
            "The model file has been modified or replaced. Speech results from a different "
            "model are not comparable with anything recorded in this project."
        )
    return digest


class SileroVad:
    """One loaded ONNX session. Reusable across files; not thread-safe."""

    def __init__(self, path: Path | None = None, *, verify: bool = True) -> None:
        self.path = path or model_path()
        self.checksum = verify_model(self.path) if verify else model_checksum(self.path)
        try:
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise SpeechEngineError(
                "onnxruntime is required for speech detection but is not installed. "
                "Install it with: pip install 'davinci-auto-zoom[speech]'"
            ) from exc

        options = onnxruntime.SessionOptions()
        # One thread each: the workload is a few thousand tiny sequential inferences, so
        # thread pools cost more in scheduling than they save, and single-threaded CPU
        # inference is bit-reproducible.
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        started = time.perf_counter()
        self.session: Any = onnxruntime.InferenceSession(
            str(self.path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.load_seconds = time.perf_counter() - started
        self.runtime_version = str(onnxruntime.__version__)

    def probabilities(
        self, samples: np.ndarray, *, sample_rate: int = SAMPLE_RATE
    ) -> list[float]:
        """One speech probability per 512-sample window, in order.

        A trailing partial window is zero-padded rather than dropped, so the last fraction of
        a second is analysed like any other.
        """

        if sample_rate != SAMPLE_RATE:
            raise SpeechEngineError(
                f"the vendored model is driven at {SAMPLE_RATE} Hz only, got {sample_rate} Hz. "
                "Normalize the audio with speech.audio.normalize_to_pcm16_mono_16k() first."
            )
        if samples.ndim != 1:
            raise SpeechEngineError(
                f"expected mono audio (1 dimension), got shape {samples.shape}"
            )
        if samples.shape[0] == 0:
            raise SpeechEngineError("audio is empty; nothing to analyse")

        audio = np.ascontiguousarray(samples, dtype=np.float32)
        state = np.zeros(STATE_SHAPE, dtype=np.float32)
        context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        rate = np.array(sample_rate, dtype=np.int64)

        results: list[float] = []
        for start in range(0, audio.shape[0], WINDOW_SAMPLES):
            window = audio[start : start + WINDOW_SAMPLES]
            if window.shape[0] < WINDOW_SAMPLES:
                window = np.pad(window, (0, WINDOW_SAMPLES - window.shape[0]))
            payload = np.concatenate((context, window))[np.newaxis, :]
            output, state = self.session.run(
                None, {"input": payload, "state": state, "sr": rate}
            )
            results.append(float(output[0][0]))
            context = window[-CONTEXT_SAMPLES:]
        return results


@dataclass(frozen=True, slots=True)
class SpeechAnalysis:
    """Everything one VAD run produced, in sample space. Resolve-free and picklable."""

    settings: VadSettings
    sample_rate: int
    sample_count: int
    segments: tuple[SampleSegment, ...]
    probabilities: tuple[float, ...] = field(repr=False, default=())
    model_version: str = MODEL_VERSION
    model_checksum: str = MODEL_SHA256
    onnxruntime_version: str = ""
    model_load_seconds: float = 0.0
    inference_seconds: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / self.sample_rate

    @property
    def speech_samples(self) -> int:
        return sum(segment.duration for segment in self.segments)

    @property
    def speech_ratio(self) -> float:
        return self.speech_samples / self.sample_count if self.sample_count else 0.0

    def to_segments(self, timebase: Timebase) -> tuple[SpeechSegment, ...]:
        """Absolute-timeline-frame segments. This is the boundary the planner consumes."""

        return normalize_speech_segments(
            SpeechSegment(timebase.frame_range(segment.start, segment.end))
            for segment in self.segments
        )


def analyze_samples(
    samples: np.ndarray,
    settings: VadSettings | None = None,
    *,
    engine: SileroVad | None = None,
    sample_rate: int = SAMPLE_RATE,
) -> SpeechAnalysis:
    """Run the VAD over an in-memory mono 16 kHz waveform."""

    settings = settings or VadSettings()
    engine = engine or SileroVad()
    started = time.perf_counter()
    probabilities = engine.probabilities(samples, sample_rate=sample_rate)
    inference_seconds = time.perf_counter() - started
    sample_count = int(samples.shape[0])
    return SpeechAnalysis(
        settings=settings,
        sample_rate=sample_rate,
        sample_count=sample_count,
        segments=segments_from_probabilities(probabilities, sample_count, settings),
        probabilities=tuple(probabilities),
        onnxruntime_version=engine.runtime_version,
        model_checksum=engine.checksum,
        model_load_seconds=engine.load_seconds,
        inference_seconds=inference_seconds,
    )


def analyze_wav(
    path: Path,
    settings: VadSettings | None = None,
    *,
    engine: SileroVad | None = None,
) -> tuple[NormalizedAudio, SpeechAnalysis]:
    """Analyse an already-normalized 16 kHz mono WAV. No Resolve, no ffmpeg."""

    audio = load_pcm16_mono(path)
    if audio.sample_rate != SAMPLE_RATE:
        raise SpeechEngineError(
            f"{path} is {audio.sample_rate} Hz; the model needs {SAMPLE_RATE} Hz. Convert it "
            "first (speech.audio.normalize_to_pcm16_mono_16k)."
        )
    return audio, analyze_samples(audio.samples, settings, engine=engine)


@dataclass(frozen=True, slots=True)
class SileroSpeechProvider:
    """`SpeechProvider` over one normalized WAV plus the timeline it was rendered from."""

    wav_path: Path
    timebase: Timebase
    settings: VadSettings = field(default_factory=VadSettings)

    def detect(self) -> Sequence[SpeechSegment]:
        _, analysis = analyze_wav(self.wav_path, self.settings)
        return analysis.to_segments(self.timebase)


__all__ = [
    "MODEL_SHA256",
    "MODEL_VERSION",
    "SileroSpeechProvider",
    "SileroVad",
    "SpeechAnalysis",
    "SpeechEngineError",
    "analyze_samples",
    "analyze_wav",
    "model_path",
    "verify_model",
]
