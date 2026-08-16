"""Silero engine tests.

No audio is committed to the repository. The fixtures are generated: digital silence, and a
synthetic voiced signal (harmonic stack shaped by three formants, modulated at a syllabic
rate) which the model does react to. Assertions are deliberately loose about *where exactly*
the boundaries fall, because that is the part a model update may legitimately change; they
are strict about the properties that must hold for any version.
"""

import numpy as np
import pytest

from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.vad import SAMPLE_RATE, VadSettings
from davinci_auto_zoom.speech.silero import (
    MODEL_SHA256,
    MODEL_VERSION,
    SileroVad,
    SpeechEngineError,
    analyze_samples,
    analyze_wav,
    model_path,
    verify_model,
)

SR = SAMPLE_RATE


@pytest.fixture(scope="module")
def engine():
    return SileroVad()


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def voiced(seconds: float, f0: float = 120.0, seed: int = 0) -> np.ndarray:
    """A crude but reliably speech-like waveform: formant-shaped harmonics + syllabic AM."""

    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    signal = np.zeros_like(t)
    for harmonic in range(1, 40):
        amplitude = 1.0 / harmonic
        for centre, width, gain in ((700, 130, 1.0), (1220, 70, 0.6), (2600, 160, 0.35)):
            amplitude += gain * np.exp(-((harmonic * f0 - centre) ** 2) / (2 * width**2))
        signal += amplitude * np.sin(
            2 * np.pi * harmonic * f0 * t + rng.uniform(0, 2 * np.pi)
        )
    signal *= 0.55 + 0.45 * np.sin(2 * np.pi * 4.5 * t)  # ~4.5 syllables per second
    signal += 0.005 * rng.standard_normal(t.shape)
    return (signal / np.abs(signal).max() * 0.4).astype(np.float32)


def test_the_vendored_model_is_the_pinned_one():
    assert model_path().is_file()
    assert verify_model() == MODEL_SHA256
    assert "v6.2.1" in MODEL_VERSION


def test_a_modified_model_is_refused_loudly(tmp_path):
    tampered = tmp_path / "silero_vad.onnx"
    tampered.write_bytes(model_path().read_bytes() + b"\x00")
    with pytest.raises(SpeechEngineError, match="checksum mismatch"):
        verify_model(tampered)


def test_complete_silence_yields_zero_segments(engine):
    analysis = analyze_samples(silence(3.0), engine=engine)
    assert analysis.segments == ()
    assert analysis.speech_ratio == 0.0
    assert max(analysis.probabilities) < 0.1


def test_a_voiced_region_is_detected_and_stays_inside_the_voiced_part(engine):
    audio = np.concatenate([silence(1.5), voiced(3.0), silence(1.5)])
    analysis = analyze_samples(audio, engine=engine)

    assert analysis.segments, "the synthetic voiced fixture should trigger the VAD"
    for segment in analysis.segments:
        # Generous window: exactly where the model decides speech starts is its business.
        assert segment.start >= int(1.0 * SR)
        assert segment.end <= int(5.0 * SR)


def test_segments_are_sorted_non_overlapping_and_non_empty(engine):
    audio = np.concatenate(
        [silence(1.0), voiced(2.0), silence(1.0), voiced(2.0, f0=155, seed=3), silence(1.0)]
    )
    analysis = analyze_samples(audio, engine=engine)
    for segment in analysis.segments:
        assert 0 <= segment.start < segment.end <= analysis.sample_count
    for earlier, later in zip(analysis.segments, analysis.segments[1:], strict=False):
        assert earlier.end <= later.start


def test_one_probability_per_512_sample_window(engine):
    audio = silence(1.0)  # 16000 samples -> ceil(16000 / 512) = 32 windows
    analysis = analyze_samples(audio, engine=engine)
    assert len(analysis.probabilities) == 32


def test_inference_is_deterministic(engine):
    audio = np.concatenate([silence(0.5), voiced(1.5), silence(0.5)])
    first = analyze_samples(audio, engine=engine)
    second = analyze_samples(audio, engine=engine)
    assert first.probabilities == second.probabilities
    assert first.segments == second.segments


def test_empty_audio_is_a_clear_error(engine):
    with pytest.raises(SpeechEngineError, match="empty"):
        engine.probabilities(np.zeros(0, dtype=np.float32))


def test_stereo_audio_is_refused_rather_than_silently_mixed(engine):
    with pytest.raises(SpeechEngineError, match="mono"):
        engine.probabilities(np.zeros((2, 1000), dtype=np.float32))


def test_a_wrong_sample_rate_is_refused_not_resampled(engine):
    with pytest.raises(SpeechEngineError, match="16000 Hz only"):
        engine.probabilities(silence(1.0), sample_rate=44100)


def test_analyze_wav_refuses_a_file_that_is_not_16k(tmp_path, engine):
    import wave

    path = tmp_path / "wrong.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 44100)
    with pytest.raises(SpeechEngineError, match="44100 Hz"):
        analyze_wav(path, engine=engine)


def test_segments_are_mapped_onto_absolute_timeline_frames(engine):
    audio = np.concatenate([silence(1.0), voiced(2.0), silence(1.0)])
    analysis = analyze_samples(audio, engine=engine)
    timebase = Timebase.from_timeline(60, 216000)
    segments = analysis.to_segments(timebase)

    assert segments
    for segment in segments:
        # Absolute frames, in the same space as TimelineItem.GetStart().
        assert segment.start_frame >= 216000
        assert segment.end_frame > segment.start_frame
    # ...and they still line up with the samples they came from.
    assert segments[0].start_frame == timebase.start_frame_of(analysis.segments[0].start)


def test_the_settings_actually_reach_the_segmentation(engine):
    audio = np.concatenate([silence(1.0), voiced(2.0), silence(1.0)])
    lenient = analyze_samples(audio, VadSettings(threshold=0.2), engine=engine)
    strict = analyze_samples(audio, VadSettings(threshold=0.9), engine=engine)
    assert lenient.speech_samples >= strict.speech_samples
