"""Reconstruct the creator's manual edit from the adjustment clips they placed.

The reference is read-only: a track of DAZ generator instances whose names map to the six
roles of `domain/transitions.py`. This module turns that track back into the state sequence
it encodes, and measures the two quantities Phase 11a is about:

    anchor_gap      = next FACE_X1 start - reset start
    pure_x0_dwell   = anchor_gap - the reset asset's own animation length

A reset instance in this reference is **held**: it starts at the moment of the move, animates
for `transition_frames`, and its instance runs on until the next FACE_X1. So the placed
length carries the dwell, and the two measures above are not the same number.

The last reset of an island has no next FACE_X1. The creator says that one is deliberate and
structural — it makes the Short loop — so it is classified `RESET_REASON_SHORT_LOOP` and kept
out of any semantic evaluation rather than counted as a mystery.
"""

from __future__ import annotations

from dataclasses import dataclass

from davinci_auto_zoom.domain.transitions import (
    BY_ROLE,
    ROLE_X0_TO_FACE_X1,
    STATE_X0,
    Transition,
    transition,
)
from tools.research.phase11a.structure import ContentIsland, island_of

#: Why the creator came back to X0. The loop reason is a rule the creator stated outright;
#: everything else is what this phase is trying to explain.
RESET_REASON_SEMANTIC_BOUNDARY = "RESET_REASON_SEMANTIC_BOUNDARY"
RESET_REASON_SHORT_LOOP = "RESET_REASON_SHORT_LOOP"


@dataclass(frozen=True, slots=True)
class ManualPlacement:
    """One adjustment clip the creator placed, already resolved to a transition role."""

    role: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"placement end must be > start ({self.start}, {self.end})")
        if self.role not in BY_ROLE:
            raise ValueError(f"{self.role!r} is not a transition role")

    @property
    def transition(self) -> Transition:
        return BY_ROLE[self.role]


@dataclass(frozen=True, slots=True)
class ManualReset:
    """One manual return to X0, with everything measured about how it restarts."""

    island_index: int
    role: str
    from_state: str
    #: Where the reset instance begins — the editorial moment of the decision.
    start: int
    #: Where the placed instance ends. Not the same as where the animation ends.
    end: int
    #: `start + transition_frames`: where the picture has actually reached X0.
    animation_end: int
    #: Start of the next `x0_to_face_x1` inside the same island, if there is one.
    next_face_x1_start: int | None
    reason: str

    @property
    def is_loop(self) -> bool:
        return self.reason == RESET_REASON_SHORT_LOOP

    @property
    def anchor_gap(self) -> int | None:
        """Reset moment -> next FACE_X1. What the creator experiences as "time at X0"."""

        if self.next_face_x1_start is None:
            return None
        return self.next_face_x1_start - self.start

    @property
    def pure_x0_dwell(self) -> int | None:
        """Frames actually spent at X0, once the reset animation has finished."""

        if self.next_face_x1_start is None:
            return None
        return self.next_face_x1_start - self.animation_end


@dataclass(frozen=True, slots=True)
class ManualEdit:
    """The whole reconstructed reference: its resets, its entries, and what did not add up."""

    placements: tuple[ManualPlacement, ...]
    resets: tuple[ManualReset, ...]
    entries: tuple[int, ...]
    problems: tuple[str, ...]

    @property
    def semantic_resets(self) -> tuple[ManualReset, ...]:
        return tuple(r for r in self.resets if not r.is_loop)

    @property
    def loop_resets(self) -> tuple[ManualReset, ...]:
        return tuple(r for r in self.resets if r.is_loop)


def reconstruct(
    placements: list[ManualPlacement],
    islands: tuple[ContentIsland, ...],
    *,
    transition_frames: int,
) -> ManualEdit:
    """Walk the placed clips as a state machine and report resets, entries and problems.

    Every state change is checked against `domain/transitions.py`, so a reference that does
    something the product's graph forbids is *reported*, never silently normalised away.
    """

    if transition_frames <= 0:
        raise ValueError("transition_frames must be > 0")

    ordered = sorted(placements, key=lambda p: (p.start, p.end))
    problems: list[str] = []
    resets: list[ManualReset] = []
    entries: list[int] = []

    state = STATE_X0
    previous: ManualPlacement | None = None
    for placement in ordered:
        if previous is not None and placement.start < previous.end:
            problems.append(
                f"overlapping placements: {previous.role} [{previous.start},{previous.end}) "
                f"and {placement.role} [{placement.start},{placement.end})"
            )
        island = island_of(islands, placement.start)
        if island is None:
            problems.append(
                f"{placement.role} at {placement.start} falls outside every content island"
            )
        elif previous is not None and island_of(islands, previous.start) is not island:
            # A new island always starts from normal framing, whatever the previous one left
            # behind. Carrying a face state across the empty space between two Shorts would
            # invent a transition the creator never made.
            if state != STATE_X0:
                problems.append(
                    f"island {island.index} starts while still in {state}; state was not "
                    "reset at the island boundary"
                )
            state = STATE_X0

        try:
            moved = transition(state, placement.transition.to_state)
        except Exception as exc:
            problems.append(f"{placement.role} at {placement.start}: {exc}")
            state = placement.transition.to_state
            previous = placement
            continue
        if moved.role != placement.role:
            problems.append(
                f"{placement.role} at {placement.start} performs {state} -> "
                f"{placement.transition.to_state}, which is role {moved.role}"
            )
        if placement.role == ROLE_X0_TO_FACE_X1:
            entries.append(placement.start)
        state = placement.transition.to_state
        previous = placement

    # Resets are collected in a second pass: "the next FACE_X1" is only knowable once the
    # whole track has been read, and it must not leak across an island boundary.
    entry_starts = sorted(entries)
    for placement in ordered:
        if not placement.transition.is_reset:
            continue
        island = island_of(islands, placement.start)
        island_index = island.index if island is not None else -1
        next_entry = next(
            (
                start
                for start in entry_starts
                if start >= placement.end
                and island is not None
                and island_of(islands, start) is island
            ),
            None,
        )
        resets.append(
            ManualReset(
                island_index=island_index,
                role=placement.role,
                from_state=placement.transition.from_state,
                start=placement.start,
                end=placement.end,
                animation_end=placement.start + transition_frames,
                next_face_x1_start=next_entry,
                reason=(
                    RESET_REASON_SHORT_LOOP
                    if next_entry is None
                    else RESET_REASON_SEMANTIC_BOUNDARY
                ),
            )
        )

    return ManualEdit(
        placements=tuple(ordered),
        resets=tuple(resets),
        entries=tuple(entry_starts),
        problems=tuple(problems),
    )


def nearest_cut(cuts: tuple[int, ...], frame: int) -> tuple[int, int] | None:
    """`(cut, frame - cut)` for the cut closest to `frame`, or None when there are no cuts."""

    if not cuts:
        return None
    best = min(cuts, key=lambda cut: (abs(frame - cut), cut))
    return best, frame - best


__all__ = [
    "RESET_REASON_SEMANTIC_BOUNDARY",
    "RESET_REASON_SHORT_LOOP",
    "ManualEdit",
    "ManualPlacement",
    "ManualReset",
    "nearest_cut",
    "reconstruct",
]
