"""Content islands: the unit that keeps two Shorts on one timeline from contaminating each other.

No fixture here contains anything from `bluescreen 2`. The frame numbers are synthetic and the
words are invented.
"""

from __future__ import annotations

import pytest

from tools.research.phase11a.structure import (
    Clip,
    clip_at,
    content_islands,
    island_of,
)


def test_one_island_when_every_clip_touches_the_next() -> None:
    clips = [Clip(100, 150), Clip(150, 200), Clip(200, 260)]
    islands = content_islands(clips, min_gap_frames=30)
    assert len(islands) == 1
    assert (islands[0].start, islands[0].end) == (100, 260)
    assert islands[0].hard_cuts == (150, 200)


def test_a_gap_at_or_over_the_threshold_starts_a_new_island() -> None:
    clips = [Clip(0, 100), Clip(130, 200)]
    assert len(content_islands(clips, min_gap_frames=30)) == 2
    assert len(content_islands(clips, min_gap_frames=31)) == 1


def test_n_islands_are_supported_and_indexed_in_order() -> None:
    clips = [Clip(i * 1000, i * 1000 + 100) for i in range(5)]
    islands = content_islands(clips, min_gap_frames=60)
    assert [island.index for island in islands] == [0, 1, 2, 3, 4]
    assert all(island.hard_cuts == () for island in islands)


def test_a_small_gap_stays_inside_the_island_but_is_not_a_hard_cut() -> None:
    # 10 frames of empty space is an edit artefact, not a Short boundary — and it is also not
    # a cut, because the definition is exactly `A.end == B.start`.
    islands = content_islands([Clip(0, 100), Clip(110, 200)], min_gap_frames=30)
    assert len(islands) == 1
    assert islands[0].hard_cuts == ()


def test_a_frame_in_the_empty_space_belongs_to_no_island() -> None:
    islands = content_islands([Clip(0, 100), Clip(500, 600)], min_gap_frames=30)
    assert island_of(islands, 50) is islands[0]
    assert island_of(islands, 550) is islands[1]
    assert island_of(islands, 300) is None
    # ...which is what stops a Short-B cut from ever receiving Short-A context.
    assert island_of(islands, 100) is None


def test_last_hard_cut_is_where_a_loop_reset_would_sit() -> None:
    islands = content_islands([Clip(0, 50), Clip(50, 90), Clip(90, 120)], min_gap_frames=30)
    assert islands[0].last_hard_cut == 90
    assert content_islands([Clip(0, 50)], min_gap_frames=30)[0].last_hard_cut is None


def test_clip_at_finds_the_clip_covering_a_frame() -> None:
    island = content_islands([Clip(0, 50, "a"), Clip(50, 90, "b")], min_gap_frames=30)[0]
    assert clip_at(island, 0).name == "a"  # type: ignore[union-attr]
    assert clip_at(island, 49).name == "a"  # type: ignore[union-attr]
    assert clip_at(island, 50).name == "b"  # type: ignore[union-attr]
    assert clip_at(island, 90) is None


def test_unordered_input_is_sorted_rather_than_trusted() -> None:
    islands = content_islands([Clip(150, 200), Clip(100, 150)], min_gap_frames=30)
    assert islands[0].hard_cuts == (150,)


def test_overlapping_clips_raise_instead_of_being_merged() -> None:
    with pytest.raises(ValueError, match="overlap"):
        content_islands([Clip(0, 100), Clip(50, 150)], min_gap_frames=30)


def test_empty_input_and_a_nonsensical_threshold() -> None:
    assert content_islands([], min_gap_frames=30) == ()
    with pytest.raises(ValueError, match="min_gap_frames"):
        content_islands([Clip(0, 10)], min_gap_frames=0)


def test_a_clip_must_have_a_non_empty_range() -> None:
    with pytest.raises(ValueError, match="end must be"):
        Clip(100, 100)
