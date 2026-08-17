"""The PCM -> envelope half of Phase 8c. Synthetic waveforms only; no file is committed."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from davinci_auto_zoom.domain.dynamics import EnergySettings, voice_valleys
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.speech.energy import energy_envelope

SR = 16000
TB = Timebase(Fraction(60), 216000, SR)


def tone(seconds: float, amplitude: float = 0.25, frequency: float = 200.0) -> np.ndarray:
    samples = np.arange(int(SR * seconds), dtype=np.float64)
    return (amplitude * np.sin(2 * np.pi * frequency * samples / SR)).astype(np.float32)


def test_a_steady_tone_is_a_flat_envelope_at_its_own_level() -> None:
    envelope = energy_envelope(tone(1.0), TB)
    levels = [point.db for point in envelope.points]
    # 0.25 amplitude sine -> RMS 0.1768 -> about -15 dBFS.
    assert min(levels) == pytest.approx(-15.05, abs=0.6)
    assert max(levels) - min(levels) < 0.5


def test_digital_silence_lands_on_the_floor_instead_of_negative_infinity() -> None:
    envelope = energy_envelope(np.zeros(SR, dtype=np.float32), TB)
    assert all(point.db == pytest.approx(-140.0) for point in envelope.points)


def test_points_are_stamped_on_absolute_timeline_frames_from_the_window_centre() -> None:
    envelope = energy_envelope(tone(1.0), TB)
    assert envelope.points[0].frame == TB.start_frame  # 15 ms in, still frame 0
    # One second of audio at 60 fps is 60 frames; the last window centre is just short of it.
    assert envelope.points[-1].frame == pytest.approx(TB.start_frame + 59, abs=1)
    assert [p.frame for p in envelope.points] == sorted(p.frame for p in envelope.points)


def test_the_hop_sets_the_point_count() -> None:
    dense = energy_envelope(tone(1.0), TB, EnergySettings(hop_ms=5))
    default = energy_envelope(tone(1.0), TB)
    assert len(dense) == pytest.approx(2 * len(default), rel=0.02)


def test_audio_shorter_than_one_window_produces_no_points_rather_than_a_guess() -> None:
    assert energy_envelope(tone(0.01), TB).points == ()


def test_a_gap_in_the_tone_becomes_a_valley_the_domain_can_read() -> None:
    samples = np.concatenate([tone(0.5), np.zeros(int(SR * 0.15), np.float32), tone(0.5)])
    envelope = energy_envelope(samples, TB)
    valleys = voice_valleys(
        envelope,
        FrameRange(TB.start_frame, TB.start_frame + 70),
        burst_index=0,
        min_drop_db=20.0,
        recovery_within_db=6.0,
        min_valley_ms=30,
        max_valley_ms=650,
    )
    assert len(valleys) == 1
    assert valleys[0].qualified
    # The gap starts 0.5 s = 30 frames in and lasts 0.15 s = 9 frames.
    assert valleys[0].low.start == pytest.approx(TB.start_frame + 30, abs=2)
    assert valleys[0].recovery_frame == pytest.approx(TB.start_frame + 39, abs=2)


def test_scaling_the_whole_waveform_shifts_the_envelope_by_exactly_that_gain() -> None:
    quiet = energy_envelope(tone(0.5, amplitude=0.05), TB)
    loud = energy_envelope(tone(0.5, amplitude=0.4), TB)
    expected = 20 * np.log10(0.4 / 0.05)
    deltas = [b.db - a.db for a, b in zip(quiet.points, loud.points, strict=True)]
    assert all(delta == pytest.approx(expected, abs=0.01) for delta in deltas)


def test_the_envelope_summary_never_carries_the_samples() -> None:
    payload = energy_envelope(tone(0.5), TB).to_dict()
    assert set(payload) == {
        "settings",
        "point_count",
        "start_frame",
        "end_frame",
        "min_db",
        "max_db",
    }
