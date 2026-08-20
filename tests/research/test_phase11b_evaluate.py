"""Scoring: strict first, category recall, exact-frame deltas, and guarded exclusions."""

from __future__ import annotations

import pytest

from tools.research.phase11a.ablation import evaluate
from tools.research.phase11b.evaluate import (
    LOOP_RESET,
    SEMANTIC_RESET,
    VISUAL_PRESENTATION_RESET,
    frame_deltas,
    match_pairs,
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


def test_match_pairs_never_lets_two_predictions_claim_one_manual_reset() -> None:
    pairs = match_pairs((100, 104, 500), (100, 300))
    assert [(p.predicted, p.manual, p.delta) for p in pairs] == [
        (100, 100, 0),
        (104, 300, -196),
        (500, None, None),
    ]


def test_match_pairs_is_symmetric_in_its_tie_break_and_deterministic() -> None:
    assert match_pairs((90, 110), (100,)) == match_pairs((110, 90), (100,))
    first = match_pairs((90, 110), (100,))
    assert [(p.predicted, p.manual) for p in first] == [(90, 100), (110, None)]


def test_the_measured_short_3_taxonomy_is_internally_consistent() -> None:
    from tools.research.phase11b.annotations import BLIND_SHORT_3
    from tools.research.phase11b.measured import (
        SHORT_3_OFF_CUT,
        SHORT_3_ON_CUT,
        SHORT_3_RESETS,
    )

    assert set(SHORT_3_ON_CUT) | set(SHORT_3_OFF_CUT) == set(SHORT_3_RESETS)
    assert not set(SHORT_3_ON_CUT) & set(SHORT_3_OFF_CUT)
    cuts = {a.frame for a in BLIND_SHORT_3}
    assert set(SHORT_3_ON_CUT) <= cuts
    assert not set(SHORT_3_OFF_CUT) & cuts
    assert sorted(SHORT_3_RESETS)[-1] == SHORT_3_ON_CUT[-1] == max(cuts)
