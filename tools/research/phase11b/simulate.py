"""Causal simulation of the facecam state inside one Short, with no access to any label.

Phase 11a could ask "what state was the picture in at this cut?" by reading the creator's own
adjustment track. Phase 11b cannot: on the blind Short that track does not exist. So the state
has to be *simulated* from the audio and the cuts alone, and a reset policy is then a function
of that simulated state.

What is reused unchanged, because Phase 11b tests the reset policy and nothing else:

    the four states and six transitions of `domain/transitions.py`
    the valley/recovery detector of `domain/dynamics.py`
    the promotion thresholds (20 / 6 / 30 / 650 dB-ms) and the 15-frame animations
    the zoom cut-snap window (120 ms => 7 frames at 60 fps)

What is deliberately different, and it is a *deviation*, not a tuning:

    the shipped `_zoom_chain` refuses a promotion that cannot be held for
    `promotion_min_hold_ms` before **the reset that is already known**. A causal simulator does
    not know where the next reset will be — that is the very thing under study — so that check
    is dropped. Every other promotion condition is kept.

    one burst = one content island. Silero returns exactly one speech segment per Short on this
    material (Phase 11a §9, re-measured in 11b), so this is what the shipped pipeline would do
    here anyway.

The simulator never sees a manual placement, a manual reset, or a semantic label.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from davinci_auto_zoom.domain.transitions import (
    ROLE_X0_TO_FACE_X1,
    STATE_FACE_X1,
    STATE_FACE_X2,
    STATE_FACE_X3,
    STATE_X0,
    promotion_from,
    reset_from,
)
from tools.research.phase11a.structure import ContentIsland

#: 15 frames at 60 fps — the creator's own asset length, and the product's configured default.
TRANSITION_FRAMES = 15
#: `zoom_cut_snap_window_ms` / `zoom_cut_snap_lookback_ms` = 120 ms, symmetric.
ZOOM_CUT_SNAP_FRAMES = 7

KIND_ENTRY = "entry"
KIND_PROMOTION = "promotion"
KIND_RESET = "reset"


@dataclass(frozen=True, slots=True)
class SimStep:
    """One simulated placement: where it starts, what role it plays, what it leaves behind."""

    frame: int
    role: str
    kind: str
    state: str
    #: The raw audio/decision anchor before any cut snapping. Equal to `frame` when no snap.
    anchor: int
    on_cut: bool


@dataclass(frozen=True, slots=True)
class RhythmContext:
    """Everything a reset policy may look at, at one hard cut.

    Strictly causal and strictly label-free: every field is derived from the island geometry,
    the energy envelope and the policy's own earlier decisions. Nothing here can see the
    creator's zoom track, and nothing here can see a later frame.
    """

    island_index: int
    cut_index: int
    frame: int
    state: str
    #: Frames the current visual state has been held, counted from the step that opened it.
    frames_in_state: int
    #: Frames since FACE_X3 was entered, or None when the state is not FACE_X3.
    frames_at_x3: int | None
    #: Frames since the entry that opened the current zoom cycle. None while at X0.
    frames_in_cycle: int | None
    #: Frames since the last simulated reset, or None when there has not been one yet.
    frames_since_reset: int | None
    #: Hard cuts already passed inside the current cycle, this one included.
    cuts_in_cycle: int
    #: Transitions placed inside the current cycle (the entry counts as one).
    transitions_in_cycle: int
    is_last_cut_of_island: bool


#: A reset policy returns a reason token when it wants a reset at this cut, else None.
ResetPolicy = Callable[[RhythmContext], str | None]
#: A re-entry policy chooses where FACE_X1 restarts after a reset, or None to stay at X0.
ReentryPolicy = Callable[[int, ContentIsland, Sequence[int]], int | None]


@dataclass(frozen=True, slots=True)
class SimTrace:
    """The whole simulated cycle sequence of one island, plus the context of every cut."""

    island_index: int
    steps: tuple[SimStep, ...]
    contexts: tuple[RhythmContext, ...]
    #: Cut frame -> the reason the policy gave for resetting there.
    reset_reasons: dict[int, str]

    @property
    def reset_frames(self) -> tuple[int, ...]:
        return tuple(step.frame for step in self.steps if step.kind == KIND_RESET)

    @property
    def entry_frames(self) -> tuple[int, ...]:
        return tuple(step.frame for step in self.steps if step.kind == KIND_ENTRY)

    def context_at(self, cut: int) -> RhythmContext | None:
        return next((c for c in self.contexts if c.frame == cut), None)


def _snap(anchor: int, cuts: Sequence[int], *, window: int) -> int:
    """Nearest hard cut within `window` frames of `anchor`, else `anchor`. Later cut wins a tie.

    Same ranking as the product's `planner.snap_to_cut`, without the chain-usability callback:
    the simulator has no asset budget to protect.
    """

    candidates = [c for c in cuts if abs(c - anchor) <= window]
    if not candidates:
        return anchor
    return min(candidates, key=lambda c: (abs(c - anchor), -c))


def simulate_island(
    island: ContentIsland,
    recoveries: Sequence[int],
    *,
    reset_policy: ResetPolicy,
    reentry_policy: ReentryPolicy,
    transition_frames: int = TRANSITION_FRAMES,
    cut_snap_frames: int = ZOOM_CUT_SNAP_FRAMES,
) -> SimTrace:
    """Walk one island chronologically and return everything the policy decided.

    `recoveries` are the qualifying voice-recovery frames of the island, in time order, exactly
    as `domain/dynamics.voice_valleys` produced them. They are the only audio input.

    Event order at an equal frame is **cut, then entry, then recovery**. That order is a real
    decision: it means a cut which is itself receiving the re-entry is evaluated while the state
    is still X0, so the state gate refuses a reset there. Phase 11a measured that exact case
    four times (§11), and the alternative order would re-create those false positives.
    """

    if transition_frames <= 0:
        raise ValueError("transition_frames must be > 0")
    if cut_snap_frames < 0:
        raise ValueError("cut_snap_frames must be >= 0")

    cuts = tuple(sorted(island.hard_cuts))
    ordered_recoveries = tuple(sorted(recoveries))
    last_cut = cuts[-1] if cuts else None

    steps: list[SimStep] = []
    contexts: list[RhythmContext] = []
    reasons: dict[int, str] = {}

    state = STATE_X0
    state_since = island.start
    cycle_start: int | None = None
    x3_since: int | None = None
    last_step_frame = island.start
    last_reset: int | None = None
    cuts_in_cycle = 0
    transitions_in_cycle = 0
    #: Every Short opens at X0 and enters FACE_X1 at its very first frame — measured, not
    #: assumed: both development islands place `x0_to_face_x1` exactly at the island start.
    pending_entry: int | None = island.start

    cut_index = 0
    recovery_index = 0
    while True:
        events: list[tuple[int, int, str]] = []
        if cut_index < len(cuts):
            events.append((cuts[cut_index], 0, "cut"))
        if pending_entry is not None:
            events.append((pending_entry, 1, "entry"))
        if recovery_index < len(ordered_recoveries):
            events.append((ordered_recoveries[recovery_index], 2, "recovery"))
        if not events:
            break
        frame, _, kind = min(events)

        if kind == "cut":
            cut_index += 1
            if cycle_start is not None:
                cuts_in_cycle += 1
            context = RhythmContext(
                island_index=island.index,
                cut_index=cuts.index(frame),
                frame=frame,
                state=state,
                frames_in_state=frame - state_since,
                frames_at_x3=None if x3_since is None else frame - x3_since,
                frames_in_cycle=None if cycle_start is None else frame - cycle_start,
                frames_since_reset=None if last_reset is None else frame - last_reset,
                cuts_in_cycle=cuts_in_cycle,
                transitions_in_cycle=transitions_in_cycle,
                is_last_cut_of_island=frame == last_cut,
            )
            contexts.append(context)
            reason = reset_policy(context)
            if reason is not None and state != STATE_X0:
                steps.append(
                    SimStep(
                        frame=frame,
                        role=reset_from(state).role,
                        kind=KIND_RESET,
                        state=STATE_X0,
                        anchor=frame,
                        on_cut=True,
                    )
                )
                reasons[frame] = reason
                state = STATE_X0
                state_since = frame
                x3_since = None
                cycle_start = None
                last_reset = frame
                last_step_frame = frame
                cuts_in_cycle = 0
                transitions_in_cycle = 0
                pending_entry = reentry_policy(frame, island, ordered_recoveries)
            continue

        if kind == "entry":
            pending_entry = None
            if frame >= island.end:
                continue
            steps.append(
                SimStep(
                    frame=frame,
                    role=ROLE_X0_TO_FACE_X1,
                    kind=KIND_ENTRY,
                    state=STATE_FACE_X1,
                    anchor=frame,
                    on_cut=frame in cuts,
                )
            )
            state = STATE_FACE_X1
            state_since = frame
            cycle_start = frame
            last_step_frame = frame
            cuts_in_cycle = 0
            transitions_in_cycle = 1
            continue

        recovery_index += 1
        if state not in (STATE_FACE_X1, STATE_FACE_X2):
            continue
        if frame - last_step_frame < transition_frames:
            # The previous transition has not finished animating. The shipped planner refuses
            # the cue here rather than moving it, and so does this.
            continue
        step = promotion_from(state)
        if step is None:
            continue
        start = _snap(frame, cuts, window=cut_snap_frames)
        if start - last_step_frame < transition_frames:
            start = frame
        steps.append(
            SimStep(
                frame=start,
                role=step.role,
                kind=KIND_PROMOTION,
                state=step.to_state,
                anchor=frame,
                on_cut=start in cuts,
            )
        )
        state = step.to_state
        state_since = start
        last_step_frame = start
        transitions_in_cycle += 1
        if state == STATE_FACE_X3:
            x3_since = start

    return SimTrace(
        island_index=island.index,
        steps=tuple(steps),
        contexts=tuple(contexts),
        reset_reasons=reasons,
    )


__all__ = [
    "KIND_ENTRY",
    "KIND_PROMOTION",
    "KIND_RESET",
    "TRANSITION_FRAMES",
    "ZOOM_CUT_SNAP_FRAMES",
    "ReentryPolicy",
    "ResetPolicy",
    "RhythmContext",
    "SimStep",
    "SimTrace",
    "simulate_island",
]
