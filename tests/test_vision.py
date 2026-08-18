"""The visual-activity metric, on frames a test builds by hand.

No video file, no ffmpeg process, no Resolve. `motion_from_frames` is split out of
`motion_envelope` precisely so the *measurement* can be checked against pictures whose
content is known exactly — a still, a moving square, a cut, a brightness ramp.
"""

from fractions import Fraction

import numpy as np
import pytest

from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.vision import VisionSettings, motion_from_frames

TIMEBASE = Timebase(Fraction(60), 216000)
SETTINGS = VisionSettings(width=16, height=16, sample_rate=10)


def _blank(count: int, level: float = 0.5) -> np.ndarray:
    return np.full((count, 16, 16), level, dtype=np.float32)


def test_identical_frames_have_exactly_zero_motion() -> None:
    envelope = motion_from_frames(_blank(10), TIMEBASE, SETTINGS)
    assert len(envelope.points) == 9
    assert all(point.motion == 0.0 for point in envelope.points)


def test_a_single_frame_produces_no_envelope_rather_than_a_crash() -> None:
    assert motion_from_frames(_blank(1), TIMEBASE, SETTINGS).points == ()
    assert not motion_from_frames(_blank(0), TIMEBASE, SETTINGS)


def test_a_moving_square_registers_motion_only_while_it_moves() -> None:
    frames = _blank(6, 0.0)
    for index in range(6):
        column = 2 if index < 3 else 9  # the square jumps between samples 2 and 3
        frames[index, 4:8, column : column + 4] = 1.0
    envelope = motion_from_frames(frames, TIMEBASE, SETTINGS)
    motions = [point.motion for point in envelope.points]
    assert motions[2] > 0.0
    assert all(m == 0.0 for i, m in enumerate(motions) if i != 2)


def test_a_bigger_move_reads_as_more_motion() -> None:
    def moved(offset: int) -> float:
        frames = _blank(2, 0.0)
        frames[0, 4:8, 0:4] = 1.0
        frames[1, 4:8, offset : offset + 4] = 1.0
        return motion_from_frames(frames, TIMEBASE, SETTINGS).points[0].motion

    assert moved(2) < moved(4) <= moved(8)


def test_a_hard_cut_between_two_different_pictures_is_large_motion() -> None:
    frames = _blank(3, 0.0)
    frames[0][:, :8] = 1.0  # left half lit
    frames[1][:, :8] = 1.0
    frames[2][:, 8:] = 1.0  # cut to the right half lit
    motions = [point.motion for point in motion_from_frames(frames, TIMEBASE, SETTINGS).points]
    assert motions[0] == 0.0
    assert motions[1] > 0.4


def test_a_uniform_brightness_change_is_not_motion() -> None:
    """A fade or a flash moves every pixel equally while nothing in the picture moved."""

    frames = _blank(2, 0.2)
    frames[0][4:8, 4:8] = 0.9
    frames[1] = frames[0] + 0.3  # the whole frame, structure untouched
    assert motion_from_frames(frames, TIMEBASE, SETTINGS).points[0].motion == pytest.approx(
        0.0, abs=1e-6
    )


def test_a_structural_change_survives_a_brightness_change_on_top_of_it() -> None:
    """Invariance to brightness must not become blindness to what actually moved."""

    frames = _blank(2, 0.2)
    frames[0][4:8, 0:4] = 0.9
    frames[1][4:8, 8:12] = 0.9
    frames[1] += 0.3
    assert motion_from_frames(frames, TIMEBASE, SETTINGS).points[0].motion > 0.05


def test_samples_are_stamped_at_the_midpoint_between_the_frames_they_compare() -> None:
    """Sample 0 compares frames at 0.0 s and 0.1 s, so it belongs at 0.05 s = 3 frames."""

    envelope = motion_from_frames(_blank(4), TIMEBASE, SETTINGS)
    assert [point.frame for point in envelope.points] == [216003, 216009, 216015]


def test_the_envelope_can_be_sliced_by_half_open_frame_range() -> None:
    envelope = motion_from_frames(_blank(4), TIMEBASE, SETTINGS)
    assert len(envelope.between(216003, 216015)) == 2
    assert envelope.between(216100, 216200) == ()


def test_the_metric_is_deterministic() -> None:
    rng = np.random.default_rng(1234)
    frames = rng.random((12, 16, 16), dtype=np.float32)
    first = motion_from_frames(frames, TIMEBASE, SETTINGS)
    second = motion_from_frames(frames, TIMEBASE, SETTINGS)
    assert first == second


def test_the_sampling_settings_refuse_nonsense_rather_than_producing_it() -> None:
    with pytest.raises(ValueError, match="width and height"):
        VisionSettings(width=1)
    with pytest.raises(ValueError, match="sample_rate"):
        VisionSettings(sample_rate=0)
