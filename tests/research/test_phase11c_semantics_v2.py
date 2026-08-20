"""Rubric V2: it may only ADD boundary cuts, and it must keep category and decision apart."""

from __future__ import annotations

from tools.research.phase11b.policy import LIST_CONTINUES, SUBORDINATE_CLAUSE
from tools.research.phase11c import semantics_v2


def test_v2_only_adds_boundaries_and_never_removes_one() -> None:
    v1 = frozenset({100, 200, 300})
    v2 = semantics_v2.boundary_frames(v1)
    assert v1 <= v2
    assert v2 - v1 == set(semantics_v2.V2_DELTA)


def test_the_extended_variant_is_reported_separately() -> None:
    v1 = frozenset({100})
    assert semantics_v2.boundary_frames(v1, extended=True) - semantics_v2.boundary_frames(v1) == {
        218735
    }


def test_a_category_is_not_a_decision() -> None:
    assert semantics_v2.RESET_CANDIDATE[semantics_v2.CONTRAST_OR_CONCESSION] is True
    assert semantics_v2.RESET_CANDIDATE[SUBORDINATE_CLAUSE] is False
    assert semantics_v2.RESET_CANDIDATE[LIST_CONTINUES] is False


def test_every_marker_occurrence_is_recorded_with_its_outcome() -> None:
    summary = semantics_v2.marker_summary()
    assert summary["clause_initial"] == summary["clause_initial_with_reset"]
    assert summary["mid_clause_with_reset"] == 0
    assert summary["clause_initial"] + summary["mid_clause"] == len(
        semantics_v2.MARKER_OCCURRENCES
    )


def test_position_is_what_separates_a_pivot_from_a_word_that_merely_occurs() -> None:
    initial = [m for m in semantics_v2.MARKER_OCCURRENCES if m.clause_initial]
    mid = [m for m in semantics_v2.MARKER_OCCURRENCES if not m.clause_initial]
    assert max(m.frames_after_cut for m in initial) <= semantics_v2.CLAUSE_INITIAL_MAX_FRAMES
    assert min(m.frames_after_cut for m in mid) > semantics_v2.CLAUSE_INITIAL_MAX_FRAMES


def test_no_marker_record_carries_a_word() -> None:
    for marker in semantics_v2.MARKER_OCCURRENCES:
        assert marker.note.isascii()
        assert "meme si" not in marker.note.lower()
