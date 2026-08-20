"""Zoom-ladder headroom, and the KEEP-versus-RESET counterfactual it is measured with.

The creator's answer for 219784 is the reason this module exists: he returned to X0 **so that
he would have the room to do X1 -> X2 -> X3 again** over the long phrase that follows. That is
a PROSPECTIVE reason. "I have been at X3 for too long" is retrospective and, measured across
three Shorts in Phase 11b, it does not separate the resets from the non-resets.

So the quantity to measure is not how long a state has been held. It is:

    how much visual progression is still available if I keep this state,
    against how much becomes available if I reset now and climb again.

Everything here is computable from the island geometry, the energy envelope's qualifying voice
recoveries and a discourse horizon. No manual zoom is read: the CURRENT state is the only input
that may come from a label, and the study reports the oracle and the label-free view separately
(task section 12).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from davinci_auto_zoom.domain.transitions import (
    STATE_FACE_X1,
    STATE_FACE_X2,
    STATE_FACE_X3,
    STATE_X0,
    promotion_from,
)

#: The ladder as a number, so "how many rungs are left" is arithmetic rather than a table.
RUNG: dict[str, int] = {STATE_X0: 0, STATE_FACE_X1: 1, STATE_FACE_X2: 2, STATE_FACE_X3: 3}
TOP_RUNG = RUNG[STATE_FACE_X3]

#: The creator's asset length and the product's configured default. A cue arriving before the
#: previous move has finished animating is refused, exactly as `simulate.py` refuses it.
TRANSITION_FRAMES = 15


@dataclass(frozen=True, slots=True)
class Branch:
    """What one counterfactual branch does between a candidate frame and the horizon."""

    label: str
    #: The state the branch starts from, so a climb out of it counts as progression.
    start_state: str
    #: One `(frame, state)` pair per visible move the branch makes, in time order.
    steps: tuple[tuple[int, str], ...]
    end_state: str
    #: Frames spent sitting at FACE_X3 with nowhere left to climb, before the horizon.
    frames_saturated_at_x3: int
    #: How many distinct states the viewer sees in the window, the reset's X0 included.
    state_diversity: int

    @property
    def visible_transitions(self) -> int:
        return len(self.steps)

    @property
    def promotions(self) -> int:
        """Rung climbs only — the X0 return and the FACE_X1 re-entry are not progression.

        The first climb is counted against `start_state`, so a branch that promotes straight
        out of the state it began in is not silently credited with zero.
        """

        climbs = 0
        previous = self.start_state
        for _, state in self.steps:
            if previous != STATE_X0 and RUNG[state] > RUNG[previous]:
                climbs += 1
            previous = state
        return climbs


@dataclass(frozen=True, slots=True)
class ZoomHeadroomFeatures:
    """Everything measured at one candidate frame, before any rule is applied to it."""

    frame: int
    current_state: str
    current_rung: int

    time_since_last_reset: int | None
    time_since_last_x1: int | None
    time_in_current_state: int

    #: Horizon: the next strong discourse boundary, or the island end. Never a fixed duration.
    horizon: int
    future_discourse_duration: int
    future_speech_duration: int

    future_qualifying_recoveries: int
    promotions_available_without_reset: int
    promotions_available_after_reset: int

    keep: Branch
    reset: Branch

    @property
    def headroom_gain(self) -> int:
        """Progression unlocked by resetting: promotions after a reset minus promotions kept."""

        return self.promotions_available_after_reset - self.promotions_available_without_reset

    @property
    def transition_gain(self) -> int:
        """The same comparison counting every visible move, not only rung climbs."""

        return self.reset.visible_transitions - self.keep.visible_transitions


def _climb(
    state: str,
    start: int,
    recoveries: Sequence[int],
    horizon: int,
    *,
    transition_frames: int = TRANSITION_FRAMES,
) -> tuple[tuple[tuple[int, str], ...], str, int]:
    """Consume recoveries from `start` and return `(steps, end state, last move frame)`."""

    steps: list[tuple[int, str]] = []
    last = start
    for recovery in recoveries:
        if recovery >= horizon:
            break
        if recovery < last + transition_frames:
            continue
        step = promotion_from(state)
        if step is None:
            break
        steps.append((recovery, step.to_state))
        state = step.to_state
        last = recovery
    return tuple(steps), state, last


def _saturation(steps: Sequence[tuple[int, str]], state: str, start: int, horizon: int) -> int:
    """Frames the branch spends at FACE_X3 inside the window, where no rung is left."""

    if state != STATE_FACE_X3:
        return 0
    entered = next((frame for frame, s in steps if s == STATE_FACE_X3), start)
    return max(0, horizon - entered)


def branches(
    frame: int,
    state: str,
    recoveries: Sequence[int],
    horizon: int,
    *,
    reentry_gap: int,
    transition_frames: int = TRANSITION_FRAMES,
) -> tuple[Branch, Branch]:
    """The two futures: hold the current state, or reset now and climb from FACE_X1 again."""

    if reentry_gap <= 0:
        raise ValueError("reentry_gap must be > 0")
    ahead = tuple(sorted(r for r in recoveries if frame <= r < horizon))

    keep_steps, keep_state, _ = _climb(
        state, frame, ahead, horizon, transition_frames=transition_frames
    )
    keep = Branch(
        label="KEEP",
        start_state=state,
        steps=keep_steps,
        end_state=keep_state,
        frames_saturated_at_x3=_saturation(keep_steps, keep_state, frame, horizon),
        state_diversity=len({state, *(s for _, s in keep_steps)}),
    )

    entry = frame + reentry_gap
    if entry >= horizon:
        reset_steps: tuple[tuple[int, str], ...] = ((frame, STATE_X0),)
        reset_state = STATE_X0
    else:
        climbed, reset_state, _ = _climb(
            STATE_FACE_X1,
            entry,
            tuple(r for r in ahead if r >= entry),
            horizon,
            transition_frames=transition_frames,
        )
        reset_steps = ((frame, STATE_X0), (entry, STATE_FACE_X1), *climbed)
    reset = Branch(
        label="RESET",
        start_state=state,
        steps=reset_steps,
        end_state=reset_state,
        frames_saturated_at_x3=_saturation(reset_steps, reset_state, frame, horizon),
        state_diversity=len({state, *(s for _, s in reset_steps)}),
    )
    return keep, reset


def features(
    frame: int,
    state: str,
    *,
    recoveries: Sequence[int],
    horizon: int,
    speech_ranges: Sequence[tuple[int, int]],
    time_since_last_reset: int | None,
    time_since_last_x1: int | None,
    time_in_current_state: int,
    reentry_gap: int,
) -> ZoomHeadroomFeatures:
    """Measure one candidate frame. Pure arithmetic on inputs the caller has already chosen."""

    if horizon <= frame:
        raise ValueError(f"horizon {horizon} must be after the candidate frame {frame}")
    keep, reset = branches(frame, state, recoveries, horizon, reentry_gap=reentry_gap)
    speech = sum(max(0, min(end, horizon) - max(start, frame)) for start, end in speech_ranges)
    return ZoomHeadroomFeatures(
        frame=frame,
        current_state=state,
        current_rung=RUNG[state],
        time_since_last_reset=time_since_last_reset,
        time_since_last_x1=time_since_last_x1,
        time_in_current_state=time_in_current_state,
        horizon=horizon,
        future_discourse_duration=horizon - frame,
        future_speech_duration=speech,
        future_qualifying_recoveries=sum(1 for r in recoveries if frame <= r < horizon),
        promotions_available_without_reset=keep.promotions,
        promotions_available_after_reset=reset.promotions,
        keep=keep,
        reset=reset,
    )


__all__ = [
    "RUNG",
    "TOP_RUNG",
    "TRANSITION_FRAMES",
    "Branch",
    "ZoomHeadroomFeatures",
    "branches",
    "features",
]
