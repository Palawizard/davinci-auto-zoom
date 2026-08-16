from fractions import Fraction

import pytest

from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.timebase import (
    Timebase,
    frame_rate_fraction,
    frames_to_timecode,
)


def test_integer_frame_rates_are_exact():
    for value in (24, 25, 30, "50", 60.0, "60.0"):
        assert frame_rate_fraction(value).denominator == 1


def test_ntsc_rates_resolve_to_their_true_value():
    assert frame_rate_fraction("23.976") == Fraction(24000, 1001)
    assert frame_rate_fraction("29.97") == Fraction(30000, 1001)
    assert frame_rate_fraction(59.94) == Fraction(60000, 1001)
    # The rounded decimal is *not* used: that is the whole point.
    assert frame_rate_fraction("59.94") != Fraction("59.94")


def test_frame_rate_must_be_positive():
    with pytest.raises(ValueError):
        frame_rate_fraction("0")
    with pytest.raises(ValueError):
        frame_rate_fraction("-25")


def test_sample_zero_is_the_timeline_start_frame():
    timebase = Timebase.from_timeline(60, 216000)
    assert timebase.start_frame_of(0) == 216000
    assert timebase.end_frame_of(0) == 216000


def test_whole_frames_land_exactly_at_integer_rates():
    timebase = Timebase.from_timeline(60, 216000)
    # One frame at 60 fps is 16000/60 samples; 800 samples is exactly 3 frames.
    assert timebase.start_frame_of(800) == 216003
    assert timebase.end_frame_of(800) == 216003


def test_rounding_policy_is_floor_start_ceil_end():
    timebase = Timebase.from_timeline(60, 216000)
    # 1000 samples = 3.75 frames.
    assert timebase.sample_to_frame_offset(1000) == Fraction(15, 4)
    assert timebase.start_frame_of(1000) == 216003
    assert timebase.end_frame_of(1000) == 216004


def test_frame_range_covers_every_speech_sample():
    timebase = Timebase.from_timeline(60, 216000)
    frames = timebase.frame_range(1000, 5000)
    assert frames == FrameRange(216003, 216019)  # floor(3.75), ceil(18.75)
    # The covered frames start no later than the audio and end no earlier.
    assert timebase.frame_to_sample(frames.start) <= 1000
    assert timebase.frame_to_sample(frames.end) >= 5000


def test_frame_range_never_collapses_to_zero_length():
    timebase = Timebase.from_timeline(60, 216000)
    # Less than one frame of audio, entirely inside frame 216000.
    assert timebase.frame_range(1, 2) == FrameRange(216000, 216001)


def test_frame_range_rejects_empty_and_negative_input():
    timebase = Timebase.from_timeline(60, 0)
    with pytest.raises(ValueError):
        timebase.frame_range(100, 100)
    with pytest.raises(ValueError):
        timebase.frame_range(200, 100)
    with pytest.raises(ValueError):
        timebase.frame_range(-1, 100)


def test_fractional_frame_rate_does_not_drift_over_an_hour():
    timebase = Timebase.from_timeline("59.94", 0)
    one_hour_samples = 16000 * 3600
    # 3600 s at 60000/1001 fps is exactly 215784.215... frames; a float 59.94 would give
    # 215784.0, i.e. ~0.2 frames of drift after one hour and growing.
    exact = timebase.sample_to_frame_offset(one_hour_samples)
    assert exact == Fraction(3600 * 60000, 1001)
    assert timebase.start_frame_of(one_hour_samples) == 215784


def test_frame_to_sample_is_the_inverse_within_half_a_frame():
    timebase = Timebase.from_timeline("23.976", 86400)
    for frame in (86400, 86401, 90000, 123456):
        sample = timebase.frame_to_sample(frame)
        assert abs(timebase.sample_to_frame_offset(sample) - (frame - 86400)) < Fraction(1, 2)


def test_frame_to_sample_at_the_start_frame_is_zero():
    assert Timebase.from_timeline(60, 216000).frame_to_sample(216000) == 0


def test_timebase_rejects_nonsense():
    with pytest.raises(ValueError):
        Timebase(Fraction(60), -1)
    with pytest.raises(ValueError):
        Timebase(Fraction(60), 0, sample_rate=0)


def test_timecode_matches_resolve_for_the_test_project():
    # DAZ_INPUT starts at frame 216000 @ 60 fps and Resolve reports 01:00:00:00.
    assert frames_to_timecode(216000, Fraction(60)) == "01:00:00:00"
    assert frames_to_timecode(216061, Fraction(60)) == "01:00:01:01"


def test_timecode_uses_whole_frames_per_second_for_ntsc():
    assert frames_to_timecode(30, Fraction(30000, 1001)) == "00:00:01:00"
    assert frames_to_timecode(29, Fraction(30000, 1001)) == "00:00:00:29"


def test_timecode_rejects_negative_frames():
    with pytest.raises(ValueError):
        frames_to_timecode(-1, Fraction(60))
