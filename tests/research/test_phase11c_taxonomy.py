"""The creator-grounded taxonomy, on synthetic islands and synthetic placements only."""

from __future__ import annotations

from tools.research.phase11a.manual import ManualPlacement, reconstruct
from tools.research.phase11a.structure import Clip, content_islands
from tools.research.phase11b.evaluate import (
    LOOP_RESET,
    RHYTHM_REFRESH_RESET,
    SEMANTIC_RESET,
    VISUAL_PRESENTATION_RESET,
)
from tools.research.phase11c.taxonomy import (
    SOURCE_CREATOR,
    SOURCE_RUBRIC_V1,
    SOURCE_STRUCTURAL,
    counts,
    taxonomy,
)

#: Three clips, so the island has hard cuts at 200 and 400 and ends at 600.
CLIPS = [Clip(0, 200), Clip(200, 400), Clip(400, 600)]


def build(placements: list[tuple[str, int, int]]):
    islands = content_islands(CLIPS, min_gap_frames=30)
    manual = reconstruct(
        [ManualPlacement(role, start, end) for role, start, end in placements],
        islands,
        transition_frames=15,
    )
    return manual, islands


def test_a_reset_on_a_boundary_cut_is_semantic() -> None:
    manual, islands = build(
        [
            ("x0_to_face_x1", 0, 100),
            ("face_x1_to_x0", 200, 240),
            ("x0_to_face_x1", 240, 300),
        ]
    )
    rows = taxonomy(manual.resets, islands, boundary_cuts=frozenset({200}), creator_answers={})
    assert rows[0].reason == SEMANTIC_RESET
    assert rows[0].source == SOURCE_RUBRIC_V1
    assert rows[0].on_cut is True


def test_a_reset_on_a_continuation_cut_is_rhythm() -> None:
    manual, islands = build(
        [
            ("x0_to_face_x1", 0, 100),
            ("face_x1_to_x0", 200, 240),
            ("x0_to_face_x1", 240, 300),
        ]
    )
    rows = taxonomy(manual.resets, islands, boundary_cuts=frozenset(), creator_answers={})
    assert rows[0].reason == RHYTHM_REFRESH_RESET


def test_an_early_reset_is_anchored_to_the_cut_its_entry_lands_on() -> None:
    """The creator's entry-first placement: reset at 160, FACE_X1 exactly on the cut."""

    manual, islands = build(
        [
            ("x0_to_face_x1", 0, 100),
            ("face_x1_to_x0", 160, 200),
            ("x0_to_face_x1", 200, 300),
        ]
    )
    rows = taxonomy(manual.resets, islands, boundary_cuts=frozenset({200}), creator_answers={})
    assert rows[0].on_cut is False
    assert rows[0].anchor_cut == 200
    assert rows[0].entry_to_anchor == 0
    assert rows[0].entry_first is True
    assert rows[0].reason == SEMANTIC_RESET


def test_an_early_reset_whose_entry_lands_nowhere_near_a_cut_is_not_anchored() -> None:
    manual, islands = build(
        [
            ("x0_to_face_x1", 0, 100),
            ("face_x1_to_x0", 260, 300),
            ("x0_to_face_x1", 300, 380),
        ]
    )
    rows = taxonomy(manual.resets, islands, boundary_cuts=frozenset({200}), creator_answers={})
    assert rows[0].anchor_cut is None
    assert rows[0].entry_first is False
    assert rows[0].reason == RHYTHM_REFRESH_RESET


def test_the_last_hard_cut_of_the_island_is_the_loop_reset() -> None:
    manual, islands = build([("x0_to_face_x1", 0, 100), ("face_x1_to_x0", 400, 600)])
    rows = taxonomy(manual.resets, islands, boundary_cuts=frozenset({400}), creator_answers={})
    assert rows[0].reason == LOOP_RESET
    assert rows[0].source == SOURCE_STRUCTURAL


def test_a_creator_answer_outranks_every_other_rule() -> None:
    manual, islands = build(
        [
            ("x0_to_face_x1", 0, 100),
            ("face_x1_to_x0", 200, 240),
            ("x0_to_face_x1", 240, 300),
        ]
    )
    rows = taxonomy(
        manual.resets,
        islands,
        boundary_cuts=frozenset({200}),
        creator_answers={200: VISUAL_PRESENTATION_RESET},
    )
    assert rows[0].reason == VISUAL_PRESENTATION_RESET
    assert rows[0].source == SOURCE_CREATOR


def test_counts_tallies_one_reason_per_reset() -> None:
    manual, islands = build(
        [
            ("x0_to_face_x1", 0, 100),
            ("face_x1_to_x0", 200, 240),
            ("x0_to_face_x1", 240, 300),
            ("face_x1_to_x0", 400, 600),
        ]
    )
    rows = taxonomy(manual.resets, islands, boundary_cuts=frozenset({200}), creator_answers={})
    assert counts(rows) == {SEMANTIC_RESET: 1, LOOP_RESET: 1}
