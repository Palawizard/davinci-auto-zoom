"""The Phase 11b state simulator, on synthetic islands only.

No fixture here contains a transcript, a word, or anything the creator said. Frames are chosen
to make one rule visible at a time.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.domain.transitions import (
    STATE_FACE_X1,
    STATE_FACE_X2,
    STATE_FACE_X3,
    STATE_X0,
)
from tools.research.phase11a.structure import Clip, content_islands
from tools.research.phase11b.policy import fixed_gap_reentry
from tools.research.phase11b.simulate import (
    KIND_ENTRY,
    KIND_PROMOTION,
    KIND_RESET,
    RhythmContext,
    simulate_island,
)


def island(*bounds: tuple[int, int], index: int = 0):
    clips = [Clip(start, end) for start, end in bounds]
    islands = content_islands(clips, min_gap_frames=30)
    return islands[index]


def never(_: RhythmContext) -> str | None:
    return None


def always(_: RhythmContext) -> str | None:
    return "ALWAYS"


def at_x3(context: RhythmContext) -> str | None:
    return "RHYTHM" if context.state == STATE_FACE_X3 else None


NO_REENTRY = lambda *_: None  # noqa: E731 - a one-expression policy stub


def test_island_opens_at_face_x1_on_its_first_frame() -> None:
    trace = simulate_island(
        island((0, 600)), (), reset_policy=never, reentry_policy=NO_REENTRY
    )
    assert [(s.frame, s.kind, s.state) for s in trace.steps] == [(0, KIND_ENTRY, STATE_FACE_X1)]


def test_recoveries_climb_the_ladder_one_rung_at_a_time() -> None:
    trace = simulate_island(
        island((0, 600)), (100, 200, 300), reset_policy=never, reentry_policy=NO_REENTRY
    )
    assert [s.state for s in trace.steps] == [
        STATE_FACE_X1,
        STATE_FACE_X2,
        STATE_FACE_X3,
    ]
    # The fourth cue has no rung left and is dropped, not stretched.
    assert all(step.kind != KIND_RESET for step in trace.steps)


def test_a_recovery_inside_the_previous_animation_is_refused() -> None:
    trace = simulate_island(
        island((0, 600)), (10,), reset_policy=never, reentry_policy=NO_REENTRY
    )
    assert [s.kind for s in trace.steps] == [KIND_ENTRY]


def test_promotion_snaps_to_a_hard_cut_inside_the_window() -> None:
    trace = simulate_island(
        island((0, 100), (100, 600)), (104,), reset_policy=never, reentry_policy=NO_REENTRY
    )
    promotion = next(s for s in trace.steps if s.kind == KIND_PROMOTION)
    assert (promotion.frame, promotion.anchor, promotion.on_cut) == (100, 104, True)


def test_a_cut_outside_the_snap_window_leaves_the_promotion_on_its_anchor() -> None:
    trace = simulate_island(
        island((0, 100), (100, 600)), (120,), reset_policy=never, reentry_policy=NO_REENTRY
    )
    promotion = next(s for s in trace.steps if s.kind == KIND_PROMOTION)
    assert (promotion.frame, promotion.on_cut) == (120, False)


def test_x0_can_never_reset_again() -> None:
    """The state gate is the transition graph, not a tuned rule: X0 has no reset to make."""

    trace = simulate_island(
        island((0, 100), (100, 200), (200, 600)),
        (),
        reset_policy=always,
        reentry_policy=NO_REENTRY,
    )
    assert [s.frame for s in trace.steps if s.kind == KIND_RESET] == [100]
    assert trace.context_at(200) is not None
    assert trace.context_at(200).state == STATE_X0


def test_reset_restarts_the_cycle_and_its_counters() -> None:
    trace = simulate_island(
        island((0, 100), (100, 300), (300, 600)),
        (),
        reset_policy=always,
        reentry_policy=fixed_gap_reentry(36),
    )
    assert [(s.frame, s.kind) for s in trace.steps] == [
        (0, KIND_ENTRY),
        (100, KIND_RESET),
        (136, KIND_ENTRY),
        (300, KIND_RESET),
        (336, KIND_ENTRY),
    ]
    second = trace.context_at(300)
    assert second is not None
    assert second.frames_in_cycle == 300 - 136
    assert second.frames_since_reset == 300 - 100
    assert second.cuts_in_cycle == 1


def test_rhythm_rule_fires_at_the_first_cut_held_at_face_x3_and_never_at_face_x2() -> None:
    """The development set found no time threshold: the rule is the STATE, not a duration.

    So the boundary case that matters is "the very first cut after reaching FACE_X3", and the
    negative case is "a long hold at FACE_X2 still does not fire".
    """

    trace = simulate_island(
        island((0, 250), (250, 600)),
        (100, 200),
        reset_policy=at_x3,
        reentry_policy=NO_REENTRY,
    )
    reset = next(s for s in trace.steps if s.kind == KIND_RESET)
    assert reset.frame == 250

    held_at_x2 = simulate_island(
        island((0, 500), (500, 900)),
        (100,),
        reset_policy=at_x3,
        reentry_policy=NO_REENTRY,
    )
    context = held_at_x2.context_at(500)
    assert context is not None
    assert (context.state, context.frames_in_state) == (STATE_FACE_X2, 400)
    assert not [s for s in held_at_x2.steps if s.kind == KIND_RESET]


def test_repeated_cuts_before_the_state_is_reached_do_not_fire() -> None:
    trace = simulate_island(
        island((0, 50), (50, 90), (90, 130), (130, 600)),
        (200,),
        reset_policy=at_x3,
        reentry_policy=NO_REENTRY,
    )
    assert not [s for s in trace.steps if s.kind == KIND_RESET]


def test_the_last_cut_of_the_island_is_flagged_for_the_loop_rule() -> None:
    trace = simulate_island(
        island((0, 100), (100, 200), (200, 600)),
        (),
        reset_policy=never,
        reentry_policy=NO_REENTRY,
    )
    flags = {c.frame: c.is_last_cut_of_island for c in trace.contexts}
    assert flags == {100: False, 200: True}


def test_a_cut_receiving_the_reentry_is_evaluated_while_still_at_x0() -> None:
    """Phase 11a saw this exact case four times; the event order is what removes it."""

    trace = simulate_island(
        island((0, 100), (100, 200), (200, 600)),
        (),
        reset_policy=always,
        reentry_policy=lambda frame, *_: frame + 100,
    )
    context = trace.context_at(200)
    assert context is not None and context.state == STATE_X0
    assert [s.frame for s in trace.steps if s.kind == KIND_ENTRY] == [0, 200]


def test_reentry_is_deterministic_and_needs_no_label() -> None:
    choose = fixed_gap_reentry(36)
    target = island((0, 600))
    assert choose(100, target, ()) == 136
    assert choose(100, target, (1, 2, 3)) == 136
    assert choose(590, target, ()) is None


def test_simulation_is_deterministic() -> None:
    args = dict(reset_policy=at_x3, reentry_policy=fixed_gap_reentry(36))
    target = island((0, 100), (100, 250), (250, 600))
    first = simulate_island(target, (60, 160), **args)
    second = simulate_island(target, (60, 160), **args)
    assert first == second


def test_transition_frames_must_be_positive() -> None:
    with pytest.raises(ValueError):
        simulate_island(
            island((0, 600)),
            (),
            reset_policy=never,
            reentry_policy=NO_REENTRY,
            transition_frames=0,
        )
