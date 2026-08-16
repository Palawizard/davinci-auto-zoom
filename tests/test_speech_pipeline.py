from fractions import Fraction

import pytest

from davinci_auto_zoom.speech.pipeline import verify_duration


def test_an_exact_render_passes():
    # 3555 frames at 60 fps = 59.25 s = 948000 samples at 16 kHz.
    check = verify_duration(3555, 948000, Fraction(60))
    assert check.expected_seconds == pytest.approx(59.25)
    assert check.rendered_seconds == pytest.approx(59.25)
    assert check.delta_frames == pytest.approx(0.0)
    assert check.ok


def test_sub_frame_rounding_is_tolerated():
    # Half a frame short: the kind of difference sample/frame rounding produces.
    check = verify_duration(3555, 948000 - 133, Fraction(60))
    assert abs(check.delta_frames) < 1
    assert check.ok


def test_a_truncated_render_fails_the_probe():
    # One second missing: every speech frame after it would be wrong.
    check = verify_duration(3555, 948000 - 16000, Fraction(60))
    assert check.delta_frames == pytest.approx(-60.0, abs=0.01)
    assert not check.ok


def test_a_padded_render_fails_the_probe():
    check = verify_duration(3555, 948000 + 16000, Fraction(60))
    assert check.delta_frames == pytest.approx(60.0, abs=0.01)
    assert not check.ok


def test_fractional_frame_rates_are_compared_exactly():
    frame_rate = Fraction(30000, 1001)
    frames = 30000
    samples = round(float(Fraction(frames) / frame_rate) * 16000)
    check = verify_duration(frames, samples, frame_rate)
    assert check.ok
    assert abs(check.delta_frames) < 1


def test_the_tolerance_is_configurable_and_reported():
    check = verify_duration(3555, 948000 - 16000, Fraction(60), tolerance_frames=120)
    assert check.ok
    assert check.tolerance_frames == 120
    assert check.to_dict()["tolerance_frames"] == 120
