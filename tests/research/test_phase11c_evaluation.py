"""Evaluation helpers: strict versus addressable, and the leave-one-Short-out threshold."""

from __future__ import annotations

from tools.research.phase11b.evaluate import (
    LOOP_RESET,
    RHYTHM_REFRESH_RESET,
    SEMANTIC_RESET,
    VISUAL_PRESENTATION_RESET,
)
from tools.research.phase11c.creator_feedback import addressable
from tools.research.phase11c.study import choose_threshold


def test_strict_counts_every_reset_and_addressable_drops_only_the_visual_one() -> None:
    edit = {
        10: SEMANTIC_RESET,
        20: RHYTHM_REFRESH_RESET,
        30: VISUAL_PRESENTATION_RESET,
        40: LOOP_RESET,
    }
    assert len(edit) == 4
    assert set(addressable(edit)) == {10, 20, 40}


def test_a_reset_a_model_merely_missed_is_never_excluded() -> None:
    """Only the creator-confirmed visual category is removable (task section 28)."""

    edit = {10: SEMANTIC_RESET, 20: RHYTHM_REFRESH_RESET}
    assert addressable(edit) == edit


def test_the_threshold_is_the_smallest_one_that_fires_on_no_negative() -> None:
    assert choose_threshold([1, 2, 0, -1], [0, 0, 0]) == 1
    assert choose_threshold([2, 3], [1, 0, -1]) == 2


def test_a_threshold_that_cannot_separate_the_training_shorts_is_reported_as_none() -> None:
    assert choose_threshold([0, 1], [2, 3]) is None
    assert choose_threshold([], [0]) is None
