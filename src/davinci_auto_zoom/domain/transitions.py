"""The visual states DAZ can be in, and the transitions it is allowed to make.

Phases 4-7 hard-wired one editorial idea into the type system: there was a `facecam_x1` role
and a `reset_x0` role, and the planner's job was to alternate them. That model cannot express
"the creator kept talking, so go tighter", and every attempt to bolt x2/x3 onto it turns into
special cases in the planner *and* in the executor.

So the model is inverted here. **A state is what the frame looks like; a transition is the
asset that gets you from one state to another.** The planner reasons in states, emits
transitions, and the executor never learns what a state is — it appends the clip a role names
at the frames the plan gives it.

## The states

    X0        normal framing
    FACE_X1   facecam, first zoom level
    FACE_X2   facecam, tighter
    FACE_X3   facecam, tightest

## The allowed transitions, and only these

    X0       -> FACE_X1     x0_to_face_x1
    FACE_X1  -> FACE_X2     face_x1_to_face_x2
    FACE_X2  -> FACE_X3     face_x2_to_face_x3
    FACE_X1  -> X0          face_x1_to_x0
    FACE_X2  -> X0          face_x2_to_x0
    FACE_X3  -> X0          face_x3_to_x0

The facecam ladder is climbed one rung at a time and never descended: there is no
`FACE_X3 -> FACE_X2`, no `FACE_X2 -> FACE_X1`, and no jump from `X0` straight to `FACE_X2`.
That is an editorial decision (D046), not an oversight — a zoom that steps back out to a wider
facecam level mid-sentence reads as a mistake, while dropping all the way to X0 reads as the
end of a thought.

A `GAMEPLAY` state and its six moves lived here through Phases 9a and 9b. They are **gone**:
two measurement phases failed to find any rule that decides where a gameplay zoom belongs, and
the creator retired the direction from the product (D068). The research is still in Git history
and in `.agent/reports/phase-09*`. Nothing here anticipates its return — a speculative extension
hook is a claim about the future this project has no evidence for.

## The two clip shapes, which are not the same thing

A **promotion** clip (`FACE_X1`, `FACE_X2`, `FACE_X3` in this user's bin) animates into its
destination state over `transition_frames` and then **holds** it for as long as the instance
lasts (D014, generalised). So its placement runs from the moment of the move to whatever comes
next — another promotion, or the reset.

A **reset** clip (`X1_TO_X0`, `X2_TO_X0`, `X3_TO_X0`) does its whole job in
`transition_frames` and leaves the picture at X0. Its placement is exactly that long.

Both are the user's own Generator assets; DAZ never opens the Fusion graph (D007) and is told
the frame counts through `[assets.transition_frames]`.
"""

from __future__ import annotations

from dataclasses import dataclass

STATE_X0 = "x0"
STATE_FACE_X1 = "face_x1"
STATE_FACE_X2 = "face_x2"
STATE_FACE_X3 = "face_x3"

#: Every state this project knows about, wide framing first.
STATES: tuple[str, ...] = (
    STATE_X0,
    STATE_FACE_X1,
    STATE_FACE_X2,
    STATE_FACE_X3,
)

#: The facecam levels in climbing order. `FACECAM_LADDER[0]` is what `X0` enters into; each
#: further entry is reachable only from the one before it.
FACECAM_LADDER: tuple[str, ...] = (STATE_FACE_X1, STATE_FACE_X2, STATE_FACE_X3)

ROLE_X0_TO_FACE_X1 = "x0_to_face_x1"
ROLE_FACE_X1_TO_FACE_X2 = "face_x1_to_face_x2"
ROLE_FACE_X2_TO_FACE_X3 = "face_x2_to_face_x3"
ROLE_FACE_X1_TO_X0 = "face_x1_to_x0"
ROLE_FACE_X2_TO_X0 = "face_x2_to_x0"
ROLE_FACE_X3_TO_X0 = "face_x3_to_x0"


class ForbiddenTransition(ValueError):
    """Asked for a state change that is not in the graph. Never a recoverable condition."""


@dataclass(frozen=True, slots=True)
class Transition:
    """One allowed state change, and the semantic role of the asset that performs it."""

    role: str
    from_state: str
    to_state: str

    @property
    def is_reset(self) -> bool:
        """Does this land back at normal framing? Resets are the only shrinking moves."""

        return self.to_state == STATE_X0

    @property
    def is_promotion(self) -> bool:
        """Does this climb the facecam ladder? Both ends have to be rungs of it."""

        return self.from_state in FACECAM_LADDER and self.to_state in FACECAM_LADDER

    @property
    def is_entry(self) -> bool:
        """Does this open a facecam cycle from normal framing? Exactly one transition does."""

        return self.from_state == STATE_X0 and self.to_state == STATE_FACE_X1


TRANSITIONS: tuple[Transition, ...] = (
    Transition(ROLE_X0_TO_FACE_X1, STATE_X0, STATE_FACE_X1),
    Transition(ROLE_FACE_X1_TO_FACE_X2, STATE_FACE_X1, STATE_FACE_X2),
    Transition(ROLE_FACE_X2_TO_FACE_X3, STATE_FACE_X2, STATE_FACE_X3),
    Transition(ROLE_FACE_X1_TO_X0, STATE_FACE_X1, STATE_X0),
    Transition(ROLE_FACE_X2_TO_X0, STATE_FACE_X2, STATE_X0),
    Transition(ROLE_FACE_X3_TO_X0, STATE_FACE_X3, STATE_X0),
)

#: Role -> transition. The lookup the executor and the ownership classifier use.
BY_ROLE: dict[str, Transition] = {t.role: t for t in TRANSITIONS}
#: (from, to) -> transition. The lookup the planner uses.
BY_PAIR: dict[tuple[str, str], Transition] = {(t.from_state, t.to_state): t for t in TRANSITIONS}

#: Every configurable role, in graph order. This is the exact key set `[assets]` may use.
ROLES: tuple[str, ...] = tuple(t.role for t in TRANSITIONS)

#: Without these two there is no zoom at all, so a config missing either is an error. The
#: x2/x3 roles are optional: a user who has not built those assets simply never promotes.
REQUIRED_ROLES: tuple[str, ...] = (ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_X0)

#: Roles that this project used to accept and deliberately no longer does (D068). A config
#: carrying one is a clear error naming the retirement, never a silently ignored key: a user
#: who still lists a gameplay asset believes it will be placed, and it will not.
RETIRED_ROLES: tuple[str, ...] = (
    "x0_to_gameplay",
    "face_x1_to_gameplay",
    "face_x2_to_gameplay",
    "face_x3_to_gameplay",
    "gameplay_to_x0",
    "gameplay_to_face_x1",
)


def transition(from_state: str, to_state: str) -> Transition:
    """The transition between two states, or `ForbiddenTransition` naming what is allowed."""

    found = BY_PAIR.get((from_state, to_state))
    if found is None:
        for state in (from_state, to_state):
            if state not in STATES:
                raise ForbiddenTransition(
                    f"{state!r} is not a visual state; known states: {', '.join(STATES)}"
                )
        allowed = ", ".join(
            f"{t.from_state}->{t.to_state}" for t in TRANSITIONS if t.from_state == from_state
        )
        raise ForbiddenTransition(
            f"{from_state} -> {to_state} is not an allowed transition; "
            f"from {from_state} you may only go to: {allowed or '(nowhere)'}"
        )
    return found


def is_allowed(from_state: str, to_state: str) -> bool:
    return (from_state, to_state) in BY_PAIR


def promotion_from(state: str) -> Transition | None:
    """The one rung up from `state`, or None at the top of the ladder (and from `X0`, which
    has an *entry*, not a promotion)."""

    if state not in FACECAM_LADDER:
        return None
    index = FACECAM_LADDER.index(state)
    if index + 1 >= len(FACECAM_LADDER):
        return None
    return BY_PAIR[(state, FACECAM_LADDER[index + 1])]


def reset_from(state: str) -> Transition:
    """The transition back to normal framing. Which asset that is depends on where you are."""

    return transition(state, STATE_X0)


def state_after(role: str) -> str:
    """Where a role leaves the picture. Raises on a role that is not in the graph."""

    found = BY_ROLE.get(role)
    if found is None:
        raise ForbiddenTransition(
            f"{role!r} is not a transition role; known roles: {', '.join(ROLES)}"
        )
    return found.to_state


__all__ = [
    "BY_PAIR",
    "BY_ROLE",
    "FACECAM_LADDER",
    "REQUIRED_ROLES",
    "RETIRED_ROLES",
    "ROLES",
    "ROLE_FACE_X1_TO_FACE_X2",
    "ROLE_FACE_X1_TO_X0",
    "ROLE_FACE_X2_TO_FACE_X3",
    "ROLE_FACE_X2_TO_X0",
    "ROLE_FACE_X3_TO_X0",
    "ROLE_X0_TO_FACE_X1",
    "STATES",
    "STATE_FACE_X1",
    "STATE_FACE_X2",
    "STATE_FACE_X3",
    "STATE_X0",
    "TRANSITIONS",
    "ForbiddenTransition",
    "Transition",
    "is_allowed",
    "promotion_from",
    "reset_from",
    "state_after",
    "transition",
]
