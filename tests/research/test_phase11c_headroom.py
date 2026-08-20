"""Zoom-ladder headroom and the KEEP/RESET counterfactual, on synthetic cues only.

Every recovery frame here is invented. No audio, no transcript, no label.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.domain.transitions import (
    STATE_FACE_X1,
    STATE_FACE_X2,
    STATE_FACE_X3,
    STATE_X0,
)
from tools.research.phase11c.headroom import branches, features

GAP = 36


def measure(frame: int, state: str, recoveries: list[int], horizon: int):
    return features(
        frame,
        state,
        recoveries=recoveries,
        horizon=horizon,
        speech_ranges=[(0, 10_000)],
        time_since_last_reset=None,
        time_since_last_x1=None,
        time_in_current_state=0,
        reentry_gap=GAP,
    )


def test_at_x3_with_two_future_cues_a_reset_unlocks_two_promotions() -> None:
    result = measure(1000, STATE_FACE_X3, [1100, 1200], 1400)
    assert result.promotions_available_without_reset == 0
    assert result.promotions_available_after_reset == 2
    assert result.headroom_gain == 2


def test_at_x1_with_one_cue_the_reset_buys_nothing() -> None:
    result = measure(1000, STATE_FACE_X1, [1100], 1400)
    assert result.promotions_available_without_reset == 1
    assert result.promotions_available_after_reset == 1
    assert result.headroom_gain == 0


def test_with_no_future_cue_there_is_no_gain_at_all() -> None:
    result = measure(1000, STATE_FACE_X3, [], 1400)
    assert result.future_qualifying_recoveries == 0
    assert result.headroom_gain == 0
    assert result.reset.end_state == STATE_FACE_X1


def test_a_reset_restarts_cue_consumption_from_face_x1() -> None:
    keep, reset = branches(1000, STATE_FACE_X2, [1100, 1200, 1300], 1400, reentry_gap=GAP)
    assert [state for _, state in keep.steps] == [STATE_FACE_X3]
    assert [state for _, state in reset.steps] == [
        STATE_X0,
        STATE_FACE_X1,
        STATE_FACE_X2,
        STATE_FACE_X3,
    ]
    assert reset.steps[1][0] == 1000 + GAP


def test_the_horizon_stops_the_climb_and_cues_past_it_are_ignored() -> None:
    result = measure(1000, STATE_FACE_X1, [1100, 1500, 1600], 1200)
    assert result.future_qualifying_recoveries == 1
    assert result.promotions_available_without_reset == 1
    assert result.promotions_available_after_reset == 1
    assert result.headroom_gain == 0


def test_a_reentry_that_falls_past_the_horizon_makes_the_reset_branch_stay_at_x0() -> None:
    result = measure(1000, STATE_FACE_X3, [1010], 1020)
    assert result.reset.end_state == STATE_X0
    assert result.promotions_available_after_reset == 0


def test_a_cue_inside_the_animation_of_the_previous_move_is_refused() -> None:
    keep, _ = branches(1000, STATE_FACE_X1, [1005, 1100], 1400, reentry_gap=GAP)
    assert [frame for frame, _ in keep.steps] == [1100]


def test_saturation_is_the_time_spent_at_x3_with_nothing_left_to_climb() -> None:
    result = measure(1000, STATE_FACE_X3, [1100, 1200], 1400)
    assert result.keep.frames_saturated_at_x3 == 400
    assert result.reset.frames_saturated_at_x3 == 1400 - 1200


def test_a_horizon_that_is_not_in_the_future_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="horizon"):
        measure(1000, STATE_FACE_X1, [], 1000)


def test_the_counterfactual_is_deterministic_and_needs_no_label() -> None:
    first = branches(1000, STATE_FACE_X2, [1100, 1200], 1400, reentry_gap=GAP)
    second = branches(1000, STATE_FACE_X2, [1200, 1100], 1400, reentry_gap=GAP)
    assert first == second
