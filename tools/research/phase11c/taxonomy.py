"""The creator-grounded taxonomy of every manual reset in Shorts 1, 2 and 3.

Phase 11b assigned a reason to Short 3's resets from the frozen rubric alone. Phase 11c has
one more source — the creator's four answers (D071) — and one more structural observation:
some resets are placed EARLY so that the next FACE_X1 lands on a cut, and their editorial
anchor is that entry, not the reset frame. A taxonomy that reads only the reset frame calls
those resets rhythmic; a taxonomy that reads the entry calls them what they are.

Precedence, and each reset gets exactly one reason:

    1. CREATOR       an answer the creator gave. Only Short 3's four frames (D071).
    2. LOOP          the reset sits on the last hard cut of its content island.
    3. SEMANTIC      its ANCHORING cut is a `DISCOURSE_BOUNDARY` of the frozen V1 rubric.
                     The anchoring cut is the reset's own cut when it is on one, otherwise the
                     cut the next FACE_X1 entry lands on within `ENTRY_ANCHOR_WINDOW`.
    4. RHYTHM        anything else: the reset happened while the discourse was still running.

Nothing here reclassifies a reset as visual. Only the creator may do that (task §28).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tools.research.phase11a.manual import ManualReset, nearest_cut
from tools.research.phase11a.structure import ContentIsland
from tools.research.phase11b.evaluate import LOOP_RESET, RHYTHM_REFRESH_RESET, SEMANTIC_RESET

#: How close the next FACE_X1 has to land to a hard cut for that cut to be the reset's anchor.
#: MEASURED, not chosen: across three Shorts the four early-placed resets put their entry 0,
#: 0, 11 and 14 frames from a cut, and every other off-cut reset's entry is 79 frames or more
#: from the nearest one. The gap between 14 and 79 is the whole plateau.
ENTRY_ANCHOR_WINDOW = 15

SOURCE_CREATOR = "CREATOR"
SOURCE_STRUCTURAL = "STRUCTURAL"
SOURCE_RUBRIC_V1 = "RUBRIC_V1"


@dataclass(frozen=True, slots=True)
class ResetRow:
    """One manual reset, with everything the taxonomy decided and what it decided it from."""

    island_index: int
    frame: int
    from_state: str
    on_cut: bool
    nearest_cut: int
    nearest_cut_delta: int
    next_x1: int | None
    #: The cut the reset is editorially anchored to, which may be the one its ENTRY lands on.
    anchor_cut: int | None
    #: `entry - anchor_cut`, so an entry-first placement is visible as a small number.
    entry_to_anchor: int | None
    reason: str
    source: str

    @property
    def entry_first(self) -> bool:
        """The reset is off its anchoring cut but its entry is on it."""

        return not self.on_cut and self.anchor_cut is not None


def anchor_of(
    reset: ManualReset, island: ContentIsland, *, window: int = ENTRY_ANCHOR_WINDOW
) -> tuple[int | None, int | None]:
    """`(anchoring cut, entry - cut)`. The reset's own cut, or the one its entry lands on."""

    near = nearest_cut(island.hard_cuts, reset.start)
    if near is not None and near[1] == 0:
        return near[0], None
    if reset.next_face_x1_start is None:
        return None, None
    entry_near = nearest_cut(island.hard_cuts, reset.next_face_x1_start)
    if entry_near is None or abs(entry_near[1]) > window:
        return None, None
    return entry_near[0], entry_near[1]


def classify(
    reset: ManualReset,
    island: ContentIsland,
    *,
    boundary_cuts: frozenset[int],
    creator_answers: dict[int, str],
    window: int = ENTRY_ANCHOR_WINDOW,
) -> ResetRow:
    """One reset -> one row. Deterministic, and the precedence is the docstring's."""

    near = nearest_cut(island.hard_cuts, reset.start)
    if near is None:
        raise ValueError(f"island {island.index} has no hard cut to measure {reset.start} against")
    anchor, entry_delta = anchor_of(reset, island, window=window)

    if reset.start in creator_answers:
        reason, source = creator_answers[reset.start], SOURCE_CREATOR
    elif reset.start == island.last_hard_cut:
        reason, source = LOOP_RESET, SOURCE_STRUCTURAL
    elif anchor is not None and anchor in boundary_cuts:
        reason, source = SEMANTIC_RESET, SOURCE_RUBRIC_V1
    else:
        reason, source = RHYTHM_REFRESH_RESET, SOURCE_RUBRIC_V1

    return ResetRow(
        island_index=island.index,
        frame=reset.start,
        from_state=reset.from_state,
        on_cut=near[1] == 0,
        nearest_cut=near[0],
        nearest_cut_delta=near[1],
        next_x1=reset.next_face_x1_start,
        anchor_cut=anchor,
        entry_to_anchor=entry_delta,
        reason=reason,
        source=source,
    )


def taxonomy(
    resets: Sequence[ManualReset],
    islands: Sequence[ContentIsland],
    *,
    boundary_cuts: frozenset[int],
    creator_answers: dict[int, str],
) -> tuple[ResetRow, ...]:
    by_index = {island.index: island for island in islands}
    return tuple(
        classify(
            reset,
            by_index[reset.island_index],
            boundary_cuts=boundary_cuts,
            creator_answers=creator_answers,
        )
        for reset in sorted(resets, key=lambda r: r.start)
    )


def counts(rows: Sequence[ResetRow]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.reason] = tally.get(row.reason, 0) + 1
    return tally


__all__ = [
    "ENTRY_ANCHOR_WINDOW",
    "SOURCE_CREATOR",
    "SOURCE_RUBRIC_V1",
    "SOURCE_STRUCTURAL",
    "ResetRow",
    "anchor_of",
    "classify",
    "counts",
    "taxonomy",
]
