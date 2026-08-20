"""The D071 overlay must ADD evidence without rewriting the blind experiment.

No fixture here contains a transcript, a word, or anything the creator said.
"""

from __future__ import annotations

import pytest

from tools.research.phase11b.annotations import BLIND_SHORT_3
from tools.research.phase11b.evaluate import (
    RHYTHM_REFRESH_RESET,
    SEMANTIC_RESET,
    VISUAL_PRESENTATION_RESET,
)
from tools.research.phase11b.measured import SHORT_3_RESETS
from tools.research.phase11b.policy import SAME_THOUGHT_CONTINUES, SUBORDINATE_CLAUSE
from tools.research.phase11c.creator_feedback import (
    CREATOR_ANSWERS,
    addressable,
    creator_grounded_short_3,
    reclassified,
)


def test_the_historical_phase_11b_taxonomy_is_not_mutated() -> None:
    creator_grounded_short_3()
    assert SHORT_3_RESETS[219354] == RHYTHM_REFRESH_RESET
    assert SHORT_3_RESETS[219525] == RHYTHM_REFRESH_RESET
    assert len(SHORT_3_RESETS) == 11


def test_the_frozen_blind_predictions_are_not_mutated() -> None:
    """219525 was annotated a continuation, and that judgement stays wrong in the record."""

    row = next(a for a in BLIND_SHORT_3 if a.frame == 219525)
    assert row.semantic_class == SAME_THOUGHT_CONTINUES
    assert row.subtype == SUBORDINATE_CLAUSE
    assert len(BLIND_SHORT_3) == 14


def test_the_creator_answers_supersede_the_interpretation() -> None:
    grounded = creator_grounded_short_3()
    assert grounded[219354] == VISUAL_PRESENTATION_RESET
    assert grounded[219525] == SEMANTIC_RESET
    assert grounded[219784] == RHYTHM_REFRESH_RESET
    assert grounded[220442] == RHYTHM_REFRESH_RESET
    # Everything the creator did not comment on keeps its Phase 11b reason.
    untouched = {f: r for f, r in grounded.items() if f not in CREATOR_ANSWERS}
    assert untouched == {f: r for f, r in SHORT_3_RESETS.items() if f not in CREATOR_ANSWERS}


def test_only_two_of_the_four_answers_actually_changed_a_category() -> None:
    assert set(reclassified()) == {219354, 219525}


def test_the_visual_reset_is_the_only_thing_ever_excluded() -> None:
    grounded = creator_grounded_short_3()
    remaining = addressable(grounded)
    assert 219354 not in remaining
    assert len(remaining) == len(grounded) - 1
    assert VISUAL_PRESENTATION_RESET not in remaining.values()


def test_an_answer_about_a_frame_that_is_not_a_reset_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.research.phase11c import creator_feedback

    monkeypatch.setattr(
        creator_feedback, "CREATOR_ANSWERS", {**CREATOR_ANSWERS, 1: SEMANTIC_RESET}
    )
    with pytest.raises(ValueError, match="not a manual reset"):
        creator_feedback.creator_grounded_short_3()
