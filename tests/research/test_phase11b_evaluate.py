"""Scoring: strict first, category recall, exact-frame deltas, and guarded exclusions."""

from __future__ import annotations

import pytest

from tools.research.phase11a.ablation import evaluate
from tools.research.phase11b.evaluate import (
    LOOP_RESET,
    SEMANTIC_RESET,
    VISUAL_PRESENTATION_RESET,
    frame_deltas,
    recall_by_category,
    subset_evaluation,
)

UNIVERSE = (100, 200, 300, 400, 500)


def test_strict_metrics_count_every_manual_reset() -> None:
    result = evaluate("P3", UNIVERSE, (100, 200, 400), (100, 300, 400))
    confusion = result.confusion
    assert (confusion.true_positives, confusion.false_positives) == (2, 1)
    assert (confusion.false_negatives, confusion.true_negatives) == (1, 1)
    assert result.false_positive_frames == (200,)
    assert result.false_negative_frames == (300,)
    assert confusion.precision == pytest.approx(2 / 3)
    assert confusion.recall == pytest.approx(2 / 3)


def test_category_recall_attributes_each_miss() -> None:
    categories = {
        100: SEMANTIC_RESET,
        300: SEMANTIC_RESET,
        400: VISUAL_PRESENTATION_RESET,
        500: LOOP_RESET,
    }
    hits = recall_by_category(categories, (100, 500))
    assert hits == {
        SEMANTIC_RESET: (1, 2),
        VISUAL_PRESENTATION_RESET: (0, 1),
        LOOP_RESET: (1, 1),
    }


def test_a_category_with_no_manual_example_is_absent_not_zero() -> None:
    assert recall_by_category({100: LOOP_RESET}, ()) == {LOOP_RESET: (0, 1)}


def test_the_subset_evaluation_must_name_real_manual_resets() -> None:
    with pytest.raises(ValueError):
        subset_evaluation("P3", UNIVERSE, (200,), (100, 300), excluded=(200,))


def test_excluding_a_visual_reset_removes_it_from_both_sides() -> None:
    strict = evaluate("P3", UNIVERSE, (100,), (100, 400))
    subset = subset_evaluation("P3", UNIVERSE, (100,), (100, 400), excluded=(400,))
    assert strict.confusion.recall == pytest.approx(0.5)
    assert subset.confusion.recall == pytest.approx(1.0)
    assert subset.confusion.false_negatives == 0
    # The excluded cut is gone from the universe, so it cannot become a true negative either.
    assert sum(
        (
            subset.confusion.true_positives,
            subset.confusion.false_positives,
            subset.confusion.false_negatives,
            subset.confusion.true_negatives,
        )
    ) == len(UNIVERSE) - 1


def test_frame_deltas_separate_a_wrong_frame_from_a_wrong_decision() -> None:
    deltas = frame_deltas((100, 207, 900), (100, 200))
    assert [(d.predicted, d.manual, d.delta) for d in deltas] == [
        (100, 100, 0),
        (207, 200, 7),
        (900, 200, 700),
    ]
    assert deltas[0].exact and not deltas[1].exact


def test_frame_deltas_report_none_rather_than_zero_when_there_is_no_manual_frame() -> None:
    assert frame_deltas((100,), ()) == (frame_deltas((100,), ())[0],)
    only = frame_deltas((100,), ())[0]
    assert (only.manual, only.delta) == (None, None)
