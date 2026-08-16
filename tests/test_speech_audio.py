"""ffmpeg boundary tests. The failure paths are mocked so the suite never needs ffmpeg."""

import shutil
import subprocess
import wave

import numpy as np
import pytest

from davinci_auto_zoom.speech import audio as audio_module
from davinci_auto_zoom.speech.audio import (
    FfmpegError,
    ffmpeg_executable,
    ffmpeg_version,
    load_pcm16_mono,
    normalize_to_pcm16_mono_16k,
    normalized_audio,
)

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def write_wav(path, *, rate=48000, channels=2, seconds=1.0, width=2):
    count = int(rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * (count * channels * width))
    return path


def test_missing_ffmpeg_gives_an_actionable_message(monkeypatch):
    monkeypatch.setattr(audio_module.shutil, "which", lambda _: None)
    with pytest.raises(FfmpegError) as excinfo:
        ffmpeg_executable()
    message = str(excinfo.value)
    assert "ffmpeg" in message
    assert "PATH" in message
    # Actionable, not a traceback: it says what to install and how to check it.
    assert "ffmpeg -version" in message


def test_ffmpeg_version_failure_is_reported_not_raised_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        audio_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"),
    )
    with pytest.raises(FfmpegError, match="boom"):
        ffmpeg_version()


def test_conversion_failure_surfaces_ffmpegs_own_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        audio_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 69, "", "Invalid data found"),
    )
    with pytest.raises(FfmpegError, match="Invalid data found"):
        normalize_to_pcm16_mono_16k(tmp_path / "in.mov", tmp_path / "out.wav")


def test_silent_success_with_no_output_file_is_still_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        audio_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""),
    )
    with pytest.raises(FfmpegError, match="produced no audio"):
        normalize_to_pcm16_mono_16k(tmp_path / "in.mov", tmp_path / "out.wav")


def test_conversion_is_never_asked_to_trim_or_normalize_loudness(monkeypatch, tmp_path):
    """The argv is the contract: any -ss/-t/-af would silently shift every speech frame."""

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        (tmp_path / "out.wav").write_bytes(b"RIFF" + b"\x00" * 100)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(audio_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_module.subprocess, "run", fake_run)
    normalize_to_pcm16_mono_16k(tmp_path / "in.mov", tmp_path / "out.wav")

    command = captured["command"]
    assert "-ac" in command and command[command.index("-ac") + 1] == "1"
    assert "-ar" in command and command[command.index("-ar") + 1] == "16000"
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "pcm_s16le"
    for forbidden in ("-ss", "-t", "-af", "-filter:a", "-to"):
        assert forbidden not in command


def test_loading_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FfmpegError, match="not found"):
        load_pcm16_mono(tmp_path / "nope.wav")


def test_loading_a_non_wav_is_a_clear_error(tmp_path):
    path = tmp_path / "not.wav"
    path.write_bytes(b"this is not a wav file")
    with pytest.raises(FfmpegError, match="not a readable WAV"):
        load_pcm16_mono(path)


def test_loading_stereo_is_refused_rather_than_guessed(tmp_path):
    path = write_wav(tmp_path / "stereo.wav", rate=16000, channels=2)
    with pytest.raises(FfmpegError, match="expected mono"):
        load_pcm16_mono(path)


def test_loading_mono_16bit_gives_float_samples_in_range(tmp_path):
    path = tmp_path / "mono.wav"
    values = np.array([0, 16384, -16384, 32767, -32768], dtype="<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(values.tobytes())
    loaded = load_pcm16_mono(path)
    assert loaded.sample_rate == 16000
    assert loaded.sample_count == 5
    assert loaded.samples.dtype == np.float32
    assert loaded.samples.max() <= 1.0
    assert loaded.samples.min() >= -1.0
    assert loaded.samples[1] == pytest.approx(0.5)


def test_normalized_audio_rejects_a_wrong_sample_rate(monkeypatch, tmp_path):
    source, destination = tmp_path / "in.wav", tmp_path / "out.wav"
    write_wav(source, rate=8000, channels=1)
    monkeypatch.setattr(
        audio_module, "normalize_to_pcm16_mono_16k", lambda s, d: shutil.copy(source, d)
    )
    with pytest.raises(FfmpegError, match="expected 16000"):
        normalized_audio(source, destination)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg is not installed")
def test_real_ffmpeg_downmixes_and_resamples_without_changing_duration(tmp_path):
    source = write_wav(tmp_path / "in.wav", rate=48000, channels=2, seconds=2.0)
    result = normalized_audio(source, tmp_path / "out.wav")
    assert result.sample_rate == 16000
    assert result.duration_seconds == pytest.approx(2.0, abs=0.01)
