"""Audio normalization: whatever Resolve rendered -> canonical 16 kHz mono PCM.

`ffmpeg` is used for exactly one thing — resampling and downmixing to the single format the
Silero model accepts. It is deliberately *not* used for speech detection: `silencedetect` is
an amplitude gate, not a voice model, and this project needs the difference.

The conversion must be lossless with respect to *time*: no trimming, no silence removal, no
loudness normalisation. Sample 0 of the output has to be the first frame of the rendered
timeline range, or every frame mapping downstream is wrong.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
#: 16-bit signed little-endian PCM. Deterministic, universally readable by `wave`, and the
#: precision floor is far below anything a VAD reacts to.
TARGET_CODEC = "pcm_s16le"


class FfmpegError(RuntimeError):
    """ffmpeg is missing or failed. The message is meant to be shown to a user as-is."""


def ffmpeg_executable() -> str:
    """Absolute path to ffmpeg, or a clear error naming what to install."""

    found = shutil.which("ffmpeg")
    if found is None:
        raise FfmpegError(
            "ffmpeg was not found on PATH. davinci-auto-zoom uses it to convert the "
            "rendered voice track to 16 kHz mono PCM. Install it from "
            "https://ffmpeg.org/download.html (or your package manager) and make sure "
            "`ffmpeg -version` works in a terminal."
        )
    return found


def ffmpeg_version() -> str:
    """First line of `ffmpeg -version`, for the report. Raises `FfmpegError` if unusable."""

    executable = ffmpeg_executable()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        raise FfmpegError(f"could not execute {executable!r}: {exc}") from exc
    if completed.returncode != 0:
        raise FfmpegError(
            f"{executable} -version exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout.splitlines()[0].strip() if completed.stdout else "unknown"


def normalize_to_pcm16_mono_16k(source: Path, destination: Path) -> None:
    """Convert `source` to 16 kHz mono 16-bit PCM WAV at `destination`.

    Only `-ac`/`-ar`/`-c:a` are passed: no filters, no `-af loudnorm`, no `-ss`/`-t`. The
    output therefore has the same start instant and (bar sub-sample resampling rounding) the
    same duration as the input.
    """

    executable = ffmpeg_executable()
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-vn",
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_SAMPLE_RATE),
        "-c:a", TARGET_CODEC,
        str(destination),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, timeout=1800, check=False
        )
    except OSError as exc:
        raise FfmpegError(f"could not execute ffmpeg: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"ffmpeg timed out converting {source}") from exc
    if completed.returncode != 0:
        raise FfmpegError(
            f"ffmpeg failed with exit code {completed.returncode} converting {source}:\n"
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise FfmpegError(f"ffmpeg reported success but produced no audio at {destination}")


@dataclass(frozen=True, slots=True)
class NormalizedAudio:
    """A decoded mono 16 kHz waveform in `[-1.0, 1.0]`, plus what it came from."""

    path: Path
    sample_rate: int
    samples: np.ndarray

    @property
    def sample_count(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / self.sample_rate


def load_pcm16_mono(path: Path) -> NormalizedAudio:
    """Read a 16-bit mono WAV into float32. Refuses anything else rather than guessing."""

    if not path.is_file():
        raise FfmpegError(f"audio file not found: {path}")
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        raise FfmpegError(f"{path} is not a readable WAV file: {exc}") from exc

    if channels != TARGET_CHANNELS or width != 2:
        raise FfmpegError(
            f"{path} is {channels}-channel {width * 8}-bit; expected mono 16-bit PCM. "
            "It should have been produced by normalize_to_pcm16_mono_16k()."
        )
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return NormalizedAudio(path=path, sample_rate=sample_rate, samples=samples)


def normalized_audio(source: Path, destination: Path) -> NormalizedAudio:
    """Convert then load, verifying the sample rate actually came out at 16 kHz."""

    normalize_to_pcm16_mono_16k(source, destination)
    audio = load_pcm16_mono(destination)
    if audio.sample_rate != TARGET_SAMPLE_RATE:
        raise FfmpegError(
            f"normalized audio is {audio.sample_rate} Hz, expected {TARGET_SAMPLE_RATE} Hz"
        )
    return audio


__all__ = [
    "TARGET_CHANNELS",
    "TARGET_CODEC",
    "TARGET_SAMPLE_RATE",
    "FfmpegError",
    "NormalizedAudio",
    "ffmpeg_executable",
    "ffmpeg_version",
    "load_pcm16_mono",
    "normalize_to_pcm16_mono_16k",
    "normalized_audio",
]
