"""Reconstructing the human edit: resets, loop resets, and the X0 dwell they encode."""

from __future__ import annotations

import pytest

from tools.research.phase11a.manual import (
    RESET_REASON_SEMANTIC_BOUNDARY,
    RESET_REASON_SHORT_LOOP,
    ManualPlacement,
    nearest_cut,
    reconstruct,
)
from tools.research.phase11a.structure import Clip, content_islands

TRANSITION_FRAMES = 15


def _islands(*ranges: tuple[int, int]):  # type: ignore[no-untyped-def]
    return content_islands([Clip(a, b) for a, b in ranges], min_gap_frames=30)


def test_a_plain_cycle_reconstructs_and_measures_both_dwells() -> None:
    islands = _islands((0, 400))
    edit = reconstruct(
        [
            ManualPlacement("x0_to_face_x1", 0, 100),
            ManualPlacement("face_x1_to_face_x2", 100, 200),
            # The reset instance is held: it animates for 15 frames, then sits at X0.
            ManualPlacement("face_x2_to_x0", 200, 260),
            ManualPlacement("x0_to_face_x1", 260, 400),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    assert edit.problems == ()
    assert edit.entries == (0, 260)
    (reset,) = edit.resets
    assert reset.reason == RESET_REASON_SEMANTIC_BOUNDARY
    assert reset.animation_end == 215
    assert reset.anchor_gap == 60
    assert reset.pure_x0_dwell == 45


def test_a_gap_between_the_reset_and_the_next_entry_counts_as_dwell() -> None:
    islands = _islands((0, 400))
    edit = reconstruct(
        [
            ManualPlacement("x0_to_face_x1", 0, 100),
            ManualPlacement("face_x1_to_x0", 100, 140),
            ManualPlacement("x0_to_face_x1", 160, 400),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    (reset,) = edit.resets
    assert reset.anchor_gap == 60
    assert reset.pure_x0_dwell == 45


def test_the_last_reset_of_an_island_is_a_loop_reset_not_a_mystery() -> None:
    islands = _islands((0, 300))
    edit = reconstruct(
        [
            ManualPlacement("x0_to_face_x1", 0, 200),
            ManualPlacement("face_x1_to_x0", 200, 300),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    (reset,) = edit.resets
    assert reset.reason == RESET_REASON_SHORT_LOOP
    assert reset.anchor_gap is None and reset.pure_x0_dwell is None
    assert edit.loop_resets == (reset,) and edit.semantic_resets == ()


def test_a_reset_never_pairs_with_a_face_x1_in_the_next_island() -> None:
    islands = _islands((0, 300), (1000, 1300))
    edit = reconstruct(
        [
            ManualPlacement("x0_to_face_x1", 0, 200),
            ManualPlacement("face_x1_to_x0", 200, 300),
            ManualPlacement("x0_to_face_x1", 1000, 1200),
            ManualPlacement("face_x1_to_x0", 1200, 1300),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    assert edit.problems == ()
    assert [r.reason for r in edit.resets] == [
        RESET_REASON_SHORT_LOOP,
        RESET_REASON_SHORT_LOOP,
    ]
    assert [r.island_index for r in edit.resets] == [0, 1]


def test_each_island_restarts_from_x0_so_a_second_short_may_open_with_face_x1() -> None:
    islands = _islands((0, 300), (1000, 1300))
    edit = reconstruct(
        [
            # Island 0 deliberately ends *without* a reset — an unfinished Short.
            ManualPlacement("x0_to_face_x1", 0, 300),
            ManualPlacement("x0_to_face_x1", 1000, 1300),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    # The second entry is legal only because the island boundary reset the state; the fact
    # that island 0 was left in face_x1 is reported rather than hidden.
    assert any("island 1 starts while still in face_x1" in p for p in edit.problems)
    assert not any("not an allowed transition" in p for p in edit.problems)


def test_an_illegal_move_is_reported_not_normalised_away() -> None:
    islands = _islands((0, 400))
    edit = reconstruct(
        [
            ManualPlacement("x0_to_face_x1", 0, 100),
            # face_x1 -> face_x3 is not in the graph.
            ManualPlacement("face_x2_to_face_x3", 100, 200),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    assert any("not an allowed transition" in p for p in edit.problems)


def test_overlapping_placements_are_reported() -> None:
    islands = _islands((0, 400))
    edit = reconstruct(
        [
            ManualPlacement("x0_to_face_x1", 0, 150),
            ManualPlacement("face_x1_to_x0", 100, 200),
        ],
        islands,
        transition_frames=TRANSITION_FRAMES,
    )
    assert any("overlapping placements" in p for p in edit.problems)


def test_nearest_cut_reports_the_signed_distance_and_prefers_the_closer_one() -> None:
    assert nearest_cut((100, 200), 190) == (200, -10)
    assert nearest_cut((100, 200), 130) == (100, 30)
    assert nearest_cut((100, 200), 200) == (200, 0)
    assert nearest_cut((), 200) is None


def test_a_placement_must_name_a_real_role_and_a_real_range() -> None:
    with pytest.raises(ValueError, match="not a transition role"):
        ManualPlacement("x0_to_gameplay", 0, 10)
    with pytest.raises(ValueError, match="end must be"):
        ManualPlacement("x0_to_face_x1", 10, 10)


def test_transition_frames_must_be_positive() -> None:
    with pytest.raises(ValueError, match="transition_frames"):
        reconstruct([], _islands((0, 100)), transition_frames=0)
