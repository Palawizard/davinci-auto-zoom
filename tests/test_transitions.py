"""The Phase 8 state graph, on its own — no planner, no Resolve, no config.

These tests exist because the graph is a *specification*, not an implementation detail: the
whole point of Phase 8 is that "which moves are legal" stopped being scattered through the
planner and became one table somebody can read. A table nobody checks is a comment.
"""

import pytest

from davinci_auto_zoom.domain.transitions import (
    BY_ROLE,
    FACECAM_LADDER,
    REQUIRED_ROLES,
    ROLE_FACE_X1_TO_FACE_X2,
    ROLE_FACE_X1_TO_X0,
    ROLE_FACE_X2_TO_FACE_X3,
    ROLE_FACE_X2_TO_X0,
    ROLE_FACE_X3_TO_X0,
    ROLE_X0_TO_FACE_X1,
    ROLES,
    STATE_FACE_X1,
    STATE_FACE_X2,
    STATE_FACE_X3,
    STATE_X0,
    TRANSITIONS,
    ForbiddenTransition,
    is_allowed,
    promotion_from,
    reset_from,
    state_after,
    transition,
)


def test_the_graph_is_exactly_the_six_documented_transitions() -> None:
    assert {(t.from_state, t.to_state) for t in TRANSITIONS} == {
        (STATE_X0, STATE_FACE_X1),
        (STATE_FACE_X1, STATE_FACE_X2),
        (STATE_FACE_X2, STATE_FACE_X3),
        (STATE_FACE_X1, STATE_X0),
        (STATE_FACE_X2, STATE_X0),
        (STATE_FACE_X3, STATE_X0),
    }
    assert len(ROLES) == len(set(ROLES)) == 6


@pytest.mark.parametrize(
    ("from_state", "to_state", "role"),
    [
        (STATE_X0, STATE_FACE_X1, ROLE_X0_TO_FACE_X1),
        (STATE_FACE_X1, STATE_FACE_X2, ROLE_FACE_X1_TO_FACE_X2),
        (STATE_FACE_X2, STATE_FACE_X3, ROLE_FACE_X2_TO_FACE_X3),
        (STATE_FACE_X1, STATE_X0, ROLE_FACE_X1_TO_X0),
        (STATE_FACE_X2, STATE_X0, ROLE_FACE_X2_TO_X0),
        (STATE_FACE_X3, STATE_X0, ROLE_FACE_X3_TO_X0),
    ],
)
def test_each_allowed_move_resolves_to_its_role(
    from_state: str, to_state: str, role: str
) -> None:
    assert is_allowed(from_state, to_state)
    assert transition(from_state, to_state).role == role
    assert state_after(role) == to_state


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        # No jumping up the ladder from normal framing.
        (STATE_X0, STATE_FACE_X2),
        (STATE_X0, STATE_FACE_X3),
        # No climbing down it: the only way out is all the way out.
        (STATE_FACE_X3, STATE_FACE_X2),
        (STATE_FACE_X2, STATE_FACE_X1),
        (STATE_FACE_X3, STATE_FACE_X1),
        # No skipping a rung on the way up either.
        (STATE_FACE_X1, STATE_FACE_X3),
        # And no staying put.
        (STATE_X0, STATE_X0),
        (STATE_FACE_X2, STATE_FACE_X2),
    ],
)
def test_forbidden_moves_raise_and_say_what_is_allowed(
    from_state: str, to_state: str
) -> None:
    assert not is_allowed(from_state, to_state)
    with pytest.raises(ForbiddenTransition) as caught:
        transition(from_state, to_state)
    assert to_state in str(caught.value)


def test_an_unknown_state_is_named_as_the_problem() -> None:
    with pytest.raises(ForbiddenTransition, match="not a visual state"):
        transition(STATE_X0, "gameplay")


def test_an_unknown_role_is_not_silently_a_no_op() -> None:
    """Gameplay is Phase 9+. Until then, asking for it must fail loudly (D048)."""

    with pytest.raises(ForbiddenTransition, match="not a transition role"):
        state_after("x0_to_gameplay")


def test_the_ladder_is_climbed_one_rung_at_a_time_and_ends() -> None:
    assert FACECAM_LADDER == (STATE_FACE_X1, STATE_FACE_X2, STATE_FACE_X3)
    assert promotion_from(STATE_FACE_X1) is not None
    assert promotion_from(STATE_FACE_X1).to_state == STATE_FACE_X2  # type: ignore[union-attr]
    assert promotion_from(STATE_FACE_X2).to_state == STATE_FACE_X3  # type: ignore[union-attr]
    # The top of the ladder, and X0 which has an entry rather than a promotion.
    assert promotion_from(STATE_FACE_X3) is None
    assert promotion_from(STATE_X0) is None


@pytest.mark.parametrize(
    ("state", "role"),
    [
        (STATE_FACE_X1, ROLE_FACE_X1_TO_X0),
        (STATE_FACE_X2, ROLE_FACE_X2_TO_X0),
        (STATE_FACE_X3, ROLE_FACE_X3_TO_X0),
    ],
)
def test_every_facecam_level_has_its_own_way_back(state: str, role: str) -> None:
    assert reset_from(state).role == role


def test_the_classification_helpers_partition_the_graph() -> None:
    assert [t.role for t in TRANSITIONS if t.is_entry] == [ROLE_X0_TO_FACE_X1]
    assert [t.role for t in TRANSITIONS if t.is_promotion] == [
        ROLE_FACE_X1_TO_FACE_X2,
        ROLE_FACE_X2_TO_FACE_X3,
    ]
    assert [t.role for t in TRANSITIONS if t.is_reset] == [
        ROLE_FACE_X1_TO_X0,
        ROLE_FACE_X2_TO_X0,
        ROLE_FACE_X3_TO_X0,
    ]
    # Every transition is exactly one of the three; nothing falls between the categories.
    for t in TRANSITIONS:
        assert sum((t.is_entry, t.is_promotion, t.is_reset)) == 1


def test_only_the_two_moves_that_make_a_zoom_possible_are_required() -> None:
    assert REQUIRED_ROLES == (ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_X0)
    assert all(role in BY_ROLE for role in REQUIRED_ROLES)
