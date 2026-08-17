"""Voice-dynamics tests: envelope -> valleys -> cues. Pure, no numpy, no audio file.

The envelopes here are written by hand as dB values on a 10 ms hop, which is the same shape
`speech/energy.py` produces. That separation is the point: the detector is testable without
ever decoding audio, and `test_energy.py` checks the other half.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.domain.dynamics import (
    CUE_NO_RECOVERY,
    CUE_QUALIFIED,
    CUE_VALLEY_TOO_LONG,
    CUE_VALLEY_TOO_SHORT,
    EnergyEnvelope,
    EnergyPoint,
    EnergySettings,
    percentile,
    voice_valleys,
)
from davinci_auto_zoom.domain.models import FrameRange

SPEAKING = -20.0
QUIET = -60.0
BURST = FrameRange(0, 600)


def build(levels: list[float], settings: EnergySettings | None = None) -> EnergyEnvelope:
    """One point per frame, which keeps the arithmetic in these tests visible."""

    settings = settings or EnergySettings(window_ms=30, hop_ms=10, smoothing_ms=10)
    return EnergyEnvelope(
        tuple(EnergyPoint(index, level) for index, level in enumerate(levels)), settings
    )


def speech(length: int = 200, *dips: tuple[int, int], quiet: float = QUIET) -> EnergyEnvelope:
    levels = [SPEAKING] * length
    for start, end in dips:
        for index in range(start, end):
            levels[index] = quiet
    return build(levels)


def find(envelope: EnergyEnvelope, **overrides: object) -> tuple:
    settings: dict = {
        "min_drop_db": 20.0,
        "recovery_within_db": 6.0,
        "min_valley_ms": 30,
        "max_valley_ms": 650,
    }
    settings.update(overrides)
    return voice_valleys(envelope, BURST, burst_index=0, **settings)  # type: ignore[arg-type]


def test_silence_produces_no_cue() -> None:
    """A flat curve has no 75th percentile above itself, so nothing is ever below the line."""

    assert find(build([-90.0] * 200)) == ()


def test_a_constant_speaking_level_produces_no_cue() -> None:
    assert find(speech()) == ()


def test_one_dip_and_recovery_is_one_qualified_cue() -> None:
    valleys = find(speech(200, (100, 110)))
    assert len(valleys) == 1
    valley = valleys[0]
    assert valley.status == CUE_QUALIFIED
    assert valley.qualified
    assert valley.low == FrameRange(100, 110)
    assert valley.recovery_frame == 110
    assert valley.drop_db == pytest.approx(40.0)
    assert valley.recovery_db == pytest.approx(40.0)
    assert valley.valley_ms == 100


def test_the_anchor_is_the_recovery_not_the_floor_and_not_the_start() -> None:
    levels = [SPEAKING] * 200
    for index in range(100, 120):
        levels[index] = -50.0
    levels[110] = -80.0  # the floor, deliberately not in the middle of the recovery
    valleys = find(build(levels))
    assert (valleys[0].low.start, valleys[0].low_frame, valleys[0].recovery_frame) == (
        100,
        110,
        120,
    )


def test_two_dips_are_two_cues_in_time_order() -> None:
    valleys = find(speech(200, (60, 70), (120, 130)))
    assert [v.recovery_frame for v in valleys] == [70, 130]
    assert all(v.qualified for v in valleys)


def test_three_dips_are_three_cues_and_the_ladder_is_not_this_layer_s_business() -> None:
    """The signal reports what it sees; only the planner knows the ladder tops out."""

    valleys = find(speech(200, (40, 50), (90, 100), (140, 150)))
    assert len(valleys) == 3
    assert all(v.qualified for v in valleys)


def test_a_dip_that_never_comes_back_is_not_a_cue() -> None:
    valleys = find(speech(200, (100, 200)))
    assert [v.status for v in valleys] == [CUE_NO_RECOVERY]
    assert valleys[0].recovery_frame is None
    assert valleys[0].recovery_db == 0.0


def test_a_drop_shallower_than_the_threshold_is_not_a_valley_at_all() -> None:
    assert find(speech(200, (100, 110), quiet=SPEAKING - 10.0)) == ()


def test_a_recovery_that_stalls_below_the_line_is_not_a_recovery() -> None:
    """The voice comes back, but only halfway: still inside the dip as far as this is
    concerned, so the cue only fires when it reaches speaking level again."""

    levels = [SPEAKING] * 200
    for index in range(100, 110):
        levels[index] = QUIET
    for index in range(110, 140):
        levels[index] = SPEAKING - 10.0
    valleys = find(build(levels))
    assert valleys[0].recovery_frame == 140


def test_a_dip_shorter_than_the_minimum_is_rejected_with_its_reason() -> None:
    valleys = find(speech(200, (100, 102)), min_valley_ms=50)
    assert [v.status for v in valleys] == [CUE_VALLEY_TOO_SHORT]
    assert not valleys[0].qualified


def test_a_dip_longer_than_the_maximum_is_rejected_with_its_reason() -> None:
    valleys = find(speech(200, (100, 140)), max_valley_ms=200)
    assert [v.status for v in valleys] == [CUE_VALLEY_TOO_LONG]


def test_the_decision_survives_a_uniform_gain_change() -> None:
    """The whole reason the detector works in dB against a relative reference (D051).

    A gain change of +/- 12 dB is a different microphone, and it must not be a different edit.
    """

    def decision(valleys: tuple) -> list[tuple]:
        # Absolute levels move with the gain, by definition. Everything the planner reads —
        # where the cue is, how deep it was relative to the voice, whether it qualified —
        # must not.
        return [
            (v.low, v.low_frame, v.recovery_frame, v.drop_db, v.recovery_db, v.status)
            for v in valleys
        ]

    quiet_take = find(speech(200, (60, 70), (120, 130)))
    for gain in (-12.0, -6.0, 6.0, 12.0):
        levels = [SPEAKING + gain] * 200
        for start, end in ((60, 70), (120, 130)):
            for index in range(start, end):
                levels[index] = QUIET + gain
        assert decision(find(build(levels))) == decision(quiet_take)


def test_cues_are_deterministic_and_sorted() -> None:
    envelope = speech(200, (40, 50), (90, 100), (140, 150))
    first, second = find(envelope), find(envelope)
    assert [v.to_dict() for v in first] == [v.to_dict() for v in second]
    assert [v.low.start for v in first] == sorted(v.low.start for v in first)


def test_a_burst_with_almost_no_envelope_in_it_is_not_analysed() -> None:
    """Two points cannot describe a dip and a recovery. Refusing beats inventing."""

    assert voice_valleys(
        build([SPEAKING, QUIET]),
        FrameRange(0, 2),
        burst_index=0,
        min_drop_db=20.0,
        recovery_within_db=6.0,
        min_valley_ms=30,
        max_valley_ms=650,
    ) == ()


def test_valleys_outside_the_burst_are_not_seen() -> None:
    valleys = voice_valleys(
        speech(200, (10, 20), (100, 110)),
        FrameRange(50, 200),
        burst_index=3,
        min_drop_db=20.0,
        recovery_within_db=6.0,
        min_valley_ms=30,
        max_valley_ms=650,
    )
    assert [v.recovery_frame for v in valleys] == [110]
    assert valleys[0].burst_index == 3


@pytest.mark.parametrize(
    ("values", "fraction", "expected"),
    [
        ((1.0,), 0.75, 1.0),
        ((0.0, 1.0), 0.5, 0.5),
        ((0.0, 1.0, 2.0, 3.0), 0.75, 2.25),
        ((3.0, 0.0, 2.0, 1.0), 0.75, 2.25),
    ],
)
def test_the_percentile_matches_numpys_linear_method(
    values: tuple[float, ...], fraction: float, expected: float
) -> None:
    """Same numbers as `numpy.percentile`, without importing it into `domain/`."""

    assert percentile(values, fraction) == pytest.approx(expected)


def test_energy_settings_refuse_a_hop_longer_than_the_window() -> None:
    with pytest.raises(ValueError, match="skips audio"):
        EnergySettings(window_ms=20, hop_ms=30)
