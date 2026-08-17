"""Pure planner tests — no Resolve, no ffmpeg, no ONNX, no filesystem.

Every case here is a concrete editing situation written as frames. The numbers use the test
project's setup (60 fps, 15-frame x1 animation, 15-frame x0 animation) because that is the
setup the rules were derived from, but nothing in the planner hardcodes them.
"""

from fractions import Fraction

import pytest

from davinci_auto_zoom.domain.models import FrameRange, SpeechSegment
from davinci_auto_zoom.domain.planner import (
    REASON_RESET_DIRECT,
    REASON_RESET_SNAPPED_BACKWARD,
    REASON_RESET_SNAPPED_FORWARD,
    REASON_X1_HELD_TO_TIMELINE_END,
    AssetTiming,
    PlannerSettings,
    frames_from_ms,
    plan_zooms,
)
from davinci_auto_zoom.domain.transitions import (
    ROLE_FACE_X1_TO_FACE_X2,
    ROLE_FACE_X1_TO_X0,
    ROLE_FACE_X2_TO_FACE_X3,
    ROLE_FACE_X2_TO_X0,
    ROLE_FACE_X3_TO_X0,
    ROLE_X0_TO_FACE_X1,
)

FPS60 = Fraction(60)
NTSC = Fraction(60000, 1001)

#: This user's assets: both animate in 15 frames. X1_TO_X0 is 42 frames long in the
#: Media Pool and that number appears nowhere in the planner, which is the point.
TIMING = AssetTiming({ROLE_X0_TO_FACE_X1: 15, ROLE_FACE_X1_TO_X0: 15})

#: 650 ms at 60 fps = 39 frames.
GATE_FRAMES = 39
TIMELINE = FrameRange(1000, 5000)


def plan(
    *speech: tuple[int, int],
    timeline: FrameRange = TIMELINE,
    cuts: tuple[int, ...] = (),
    frame_rate: Fraction = FPS60,
    settings: PlannerSettings | None = None,
    timing: AssetTiming = TIMING,
):
    return plan_zooms(
        timeline=timeline,
        frame_rate=frame_rate,
        speech_segments=[SpeechSegment(FrameRange(start, end)) for start, end in speech],
        timing=timing,
        hard_cuts=cuts,
        settings=settings or PlannerSettings(),
    )


def ranges(result, role):
    return [(p.start_frame, p.end_frame) for p in result.of_role(role)]


# --- conversion ------------------------------------------------------------------------


def test_the_silence_gate_converts_exactly() -> None:
    assert frames_from_ms(650, FPS60) == GATE_FRAMES
    assert frames_from_ms(0, FPS60) == 0
    # 650 ms of 59.94 fps is 38.96 frames; half-up rounding keeps it at 39.
    assert frames_from_ms(650, NTSC) == 39


# --- bursts ----------------------------------------------------------------------------


def test_no_speech_produces_no_placements() -> None:
    result = plan()
    assert result.placements == ()
    assert result.bursts == ()
    assert result.valid


def test_one_burst_becomes_one_x1_and_one_x0() -> None:
    result = plan((1000, 1200))
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1200)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215)]
    assert result.reset_placements[0].reason == REASON_RESET_DIRECT
    assert result.valid


def test_a_pause_below_the_gate_keeps_one_continuous_x1() -> None:
    """The zoom must not pop out and back in across a breath."""

    result = plan((1000, 1200), (1230, 1400))  # gap of 30 < 39
    assert len(result.bursts) == 1
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1400)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1400, 1415)]
    assert result.merged_gaps == 1


def test_a_gap_exactly_at_the_gate_allows_a_reset() -> None:
    result = plan((1000, 1200), (1200 + GATE_FRAMES, 1400))
    assert len(result.bursts) == 2
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1200), (1239, 1400)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215), (1400, 1415)]
    assert result.merged_gaps == 0


def test_a_gap_one_frame_below_the_gate_is_bridged() -> None:
    result = plan((1000, 1200), (1200 + GATE_FRAMES - 1, 1400))
    assert len(result.bursts) == 1
    assert result.merged_gaps == 1


def test_a_gap_above_the_gate_resets() -> None:
    result = plan((1000, 1200), (1500, 1700))
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1200), (1500, 1700)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215), (1700, 1715)]


# --- x1 length -------------------------------------------------------------------------


def test_x1_is_as_long_as_the_burst_needs() -> None:
    """A FACE_X1 instance is entry animation *plus hold*; 900 frames is normal."""

    result = plan((1000, 1900))
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1900)]
    assert result.entry_placements[0].duration_frames == 900


def test_x1_of_exactly_the_animation_length_is_kept() -> None:
    result = plan((1000, 1015))
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1015)]
    assert result.suppressed_cycles == 0


def test_x1_shorter_than_its_animation_drops_the_whole_cycle() -> None:
    result = plan((1000, 1014))
    assert result.placements == ()
    assert result.suppressed_cycles == 1
    assert any("shorter than its own 15-frame animation" in d for d in result.decisions)


def test_x0_lasts_exactly_its_animation_not_the_assets_native_length() -> None:
    """X1_TO_X0 is 42 frames in the Media Pool; the plan uses 15."""

    result = plan((1000, 1200))
    x0 = result.reset_placements[0]
    assert x0.duration_frames == 15
    assert x0.duration_frames != 42


def test_a_different_asset_timing_changes_only_the_lengths() -> None:
    timing = AssetTiming({ROLE_X0_TO_FACE_X1: 8, ROLE_FACE_X1_TO_X0: 25})
    result = plan((1000, 1010), timing=timing)
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1010)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1010, 1035)]


# --- room for the reset ----------------------------------------------------------------


def test_a_pause_with_exactly_enough_room_still_resets() -> None:
    """Reset at 1200, next zoom at 1215: 15 frames is exactly the x0 animation.

    The gate is set to 0 so the pause qualifies for a reset at all; the question under test
    is the *room* rule, not the gate.
    """

    result = plan(
        (1000, 1200), (1215, 1400), settings=PlannerSettings(reset_after_silence_ms=0)
    )
    assert len(result.bursts) == 2
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215), (1400, 1415)]
    assert result.suppressed_resets == 0


def test_a_pause_one_frame_too_short_holds_the_zoom_instead() -> None:
    result = plan(
        (1000, 1200),
        (1214, 1400),
        settings=PlannerSettings(reset_after_silence_ms=0),
    )
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1400)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1400, 1415)]
    assert result.suppressed_resets == 1
    assert any("x1 stays active" in decision for decision in result.decisions)


# --- hard cuts -------------------------------------------------------------------------


def test_a_hard_cut_in_the_window_moves_the_reset() -> None:
    result = plan((1000, 1200), cuts=(1210,))
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1210, 1225)]
    assert result.reset_placements[0].reason == REASON_RESET_SNAPPED_FORWARD
    assert result.reset_placements[0].cut_frame == 1210
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1210)]


def test_the_nearest_valid_cut_in_the_window_wins_not_the_last() -> None:
    """The Phase 6 rule. The old planner took 1215 here; proximity to the anchor decides."""

    result = plan((1000, 1200), cuts=(1205, 1210, 1215))
    assert result.reset_placements[0].cut_frame == 1205
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1205, 1220)]


def test_a_cut_outside_the_window_is_ignored() -> None:
    # 350 ms at 60 fps = 21 frames, so 1222 is out of reach of a reset at 1200.
    result = plan((1000, 1200), cuts=(1222,))
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215)]
    assert result.reset_placements[0].reason == REASON_RESET_DIRECT


def test_a_cut_leaving_only_fourteen_frames_is_rejected() -> None:
    """1210 + 15 > 1224, so that cut cannot fit the reset before the next zoom."""

    result = plan((1000, 1200), (1224, 1400), cuts=(1210,),
                  settings=PlannerSettings(reset_after_silence_ms=0))
    assert result.rejected_cuts == 1
    assert result.reset_placements[0].reason == REASON_RESET_DIRECT
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215), (1400, 1415)]


def test_a_cut_that_does_not_fit_is_rejected_and_the_nearest_fitting_one_wins() -> None:
    result = plan((1000, 1200), (1224, 1400), cuts=(1205, 1210),
                  settings=PlannerSettings(reset_after_silence_ms=0))
    assert result.reset_placements[0].cut_frame == 1205
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1205, 1220), (1400, 1415)]
    assert result.rejected_cuts == 1


def test_the_second_nearest_cut_wins_when_the_nearest_leaves_no_room() -> None:
    """1204 is nearer (tie broken forward) but 1204 + 15 > 1218; 1196 fits and is used."""

    result = plan((1000, 1200), (1218, 1400), cuts=(1196, 1204),
                  settings=PlannerSettings(reset_after_silence_ms=0))
    assert result.reset_placements[0].cut_frame == 1196
    assert result.reset_placements[0].reason == REASON_RESET_SNAPPED_BACKWARD
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1196, 1211), (1400, 1415)]
    assert result.rejected_cuts == 1


def test_a_cut_beyond_the_lookback_is_never_snapped_to() -> None:
    """120 ms at 60 fps = 7 frames, so 1192 and 1100 are both out of reach of a reset at 1200."""

    result = plan((1000, 1200), cuts=(1100, 1192))
    assert result.reset_placements[0].cut_frame is None
    assert result.reset_placements[0].reason == REASON_RESET_DIRECT
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215)]


# --- cut snap ranking: nearest to the anchor wins, forward on a tie (Phase 6) ------------
#
# The reset anchor is the burst end (lead_out = 0). Default window is [-7f, +21f] at 60 fps.


def snapped_cut(*cuts: int, speech: tuple[int, int] = (1000, 1200)) -> int | None:
    """The cut a single-burst plan snapped its reset to, or None for a direct reset."""

    return plan(speech, cuts=cuts).reset_placements[0].cut_frame


def test_a_cut_one_frame_after_the_anchor_is_snapped_to() -> None:
    assert snapped_cut(1201) == 1201


def test_the_nearer_of_two_forward_cuts_wins() -> None:
    assert snapped_cut(1205, 1212) == 1205


def test_a_cut_one_frame_before_the_anchor_is_snapped_to() -> None:
    result = plan((1000, 1200), cuts=(1199,))
    assert result.reset_placements[0].cut_frame == 1199
    assert result.reset_placements[0].reason == REASON_RESET_SNAPPED_BACKWARD
    assert result.backward_snapped_resets == 1
    assert result.forward_snapped_resets == 0
    assert result.snapped_resets == 1


def test_a_cut_exactly_on_the_lookback_boundary_is_snapped_to() -> None:
    """7 frames back from 1200 is 1193, the last frame still inside the tolerance."""

    assert snapped_cut(1193) == 1193


def test_a_cut_one_frame_past_the_lookback_boundary_is_ignored() -> None:
    assert snapped_cut(1192) is None


def test_a_near_backward_cut_beats_a_far_forward_one() -> None:
    assert snapped_cut(1198, 1210) == 1198


def test_a_near_forward_cut_beats_a_far_backward_one() -> None:
    assert snapped_cut(1194, 1202) == 1202


def test_an_exact_tie_is_broken_towards_the_later_cut() -> None:
    result = plan((1000, 1200), cuts=(1195, 1205))
    assert result.reset_placements[0].cut_frame == 1205
    assert result.reset_placements[0].reason == REASON_RESET_SNAPPED_FORWARD


def test_the_lookback_never_moves_a_reset_without_a_cut() -> None:
    """The tolerance is a snap mechanism, never `burst.end - lookback` on its own."""

    result = plan((1000, 1200), settings=PlannerSettings(cut_snap_lookback_ms=5000))
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1200, 1215)]
    assert result.reset_placements[0].reason == REASON_RESET_DIRECT


def test_a_backward_snap_makes_x1_end_exactly_where_x0_starts() -> None:
    result = plan((1000, 1200), cuts=(1196,))
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(1000, 1196)]
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(1196, 1211)]
    assert result.valid


def test_a_backward_snap_can_never_reach_back_past_the_zoom_it_ends() -> None:
    """A cut at or before the x1 start is not a candidate: the zoom-in needs its own room."""

    result = plan((1000, 1004), settings=PlannerSettings(cut_snap_lookback_ms=200), cuts=(1000,))
    assert result.reset_placements == ()
    assert result.suppressed_cycles == 1


def test_the_backward_snap_trace_names_the_cut_the_delta_and_the_window() -> None:
    trace = "\n".join(plan((1000, 1200), cuts=(1196, 1215)).decisions)
    assert "snapped backward from 1200 to hard cut 1196" in trace
    assert "delta=-4f" in trace
    assert "[-7f, +21f]" in trace
    assert "farther candidate(s) 1215 (+15f)" in trace


def test_the_regression_case_observed_in_the_phase_5_preview() -> None:
    """The bug, as measured on DAZ_INPUT burst 3: cut 4 frames before the VAD end.

    The old forward-only planner produced `reset_direct` at the burst end, leaving the x0 a
    few frames after a cut the human editor had aligned to exactly. There is deliberately no
    usable cut after the burst end here, so only the lookback can find this one.
    """

    result = plan((216679, 216797), timeline=FrameRange(216000, 219555), cuts=(216793, 216851))
    x0 = result.reset_placements[0]
    assert x0.reason == REASON_RESET_SNAPPED_BACKWARD
    assert x0.cut_frame == 216793
    assert x0.start_frame == 216793
    assert result.entry_placements[0].end_frame == 216793
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(216793, 216808)]


def test_a_boundary_with_empty_space_is_not_a_hard_cut() -> None:
    """`TimelineSnapshot.hard_cuts` is what feeds this; the planner takes it at its word."""

    from davinci_auto_zoom.domain.snapshot import (
        TimelineItemSnapshot,
        TimelineSnapshot,
        TrackSnapshot,
    )

    timeline = TimelineSnapshot(
        name="T",
        unique_id=None,
        frame_rate=60.0,
        start_frame=1000,
        end_frame=5000,
        start_timecode="",
        is_current=False,
        tracks=(
            TrackSnapshot(
                track_type="video",
                index=1,
                name="Video 1",
                items=(
                    TimelineItemSnapshot(name="a", start=1000, end=1210),
                    # A gap: 1210 -> 1300 is empty, so neither frame is a hard cut.
                    TimelineItemSnapshot(name="b", start=1300, end=1500),
                    TimelineItemSnapshot(name="c", start=1500, end=1900),
                ),
            ),
        ),
    )
    assert timeline.hard_cuts(1) == (1500,)
    assert 1210 in timeline.edit_boundaries(1)

    result = plan((1000, 1200), cuts=timeline.hard_cuts(1))
    assert result.reset_placements[0].reason == REASON_RESET_DIRECT


# --- timeline edges --------------------------------------------------------------------


def test_speech_at_the_very_start_is_clamped_to_the_range() -> None:
    result = plan(
        (1000, 1200), settings=PlannerSettings(zoom_lead_in_ms=200)  # 12 frames before 1000
    )
    assert result.entry_placements[0].start_frame == TIMELINE.start


def test_the_last_burst_resets_when_the_animation_fits() -> None:
    result = plan((4900, 4985), timeline=FrameRange(1000, 5000))
    assert ranges(result, ROLE_FACE_X1_TO_X0) == [(4985, 5000)]


def test_the_last_burst_holds_the_zoom_when_only_fourteen_frames_remain() -> None:
    result = plan((4900, 4986), timeline=FrameRange(1000, 5000))
    assert ranges(result, ROLE_FACE_X1_TO_X0) == []
    assert ranges(result, ROLE_X0_TO_FACE_X1) == [(4900, 5000)]
    assert result.entry_placements[0].reason == REASON_X1_HELD_TO_TIMELINE_END
    assert result.suppressed_resets == 1


def test_speech_outside_the_range_is_dropped() -> None:
    result = plan((100, 200), timeline=FrameRange(1000, 5000))
    assert result.placements == ()
    assert any("outside the timeline range" in d for d in result.decisions)


# --- invariants ------------------------------------------------------------------------


def test_placements_never_overlap_and_stay_sorted() -> None:
    result = plan(
        (1000, 1100),
        (1300, 1500),
        (1600, 1605),
        (2000, 2400),
        (4900, 4990),
        cuts=(1310, 1520, 2410),
    )
    assert result.overlaps == ()
    assert result.valid
    starts = [p.start_frame for p in result.placements]
    assert starts == sorted(starts)


def test_the_same_inputs_always_produce_the_same_plan() -> None:
    speech = ((1000, 1100), (1300, 1500), (2000, 2400))
    first = plan(*speech, cuts=(1310, 1520))
    second = plan(*speech, cuts=(1310, 1520))
    assert first.to_dict() == second.to_dict()


def test_the_plan_records_every_decision() -> None:
    result = plan((1000, 1200), (1500, 1700), cuts=(1210,))
    trace = "\n".join(result.decisions)
    assert "editorial burst" in trace
    assert "snapped forward" in trace
    assert "hard cut" in trace


@pytest.mark.parametrize("frame_rate", [FPS60, NTSC, Fraction(24000, 1001), Fraction(25)])
def test_any_frame_rate_produces_a_valid_plan(frame_rate: Fraction) -> None:
    result = plan((1000, 1200), (1500, 1700), frame_rate=frame_rate, cuts=(1210,))
    assert result.valid
    assert result.entry_placements
    assert all(p.duration_frames >= 1 for p in result.placements)


def test_a_fractional_rate_changes_the_gate_not_the_frame_arithmetic() -> None:
    """The same 16-frame pause is a reset at 23.976 fps and a bridged breath at 60 fps."""

    # 650 ms is 15.58 frames at 24000/1001 (gate 16) and 39 frames at 60.
    speech = ((1000, 1200), (1216, 1400))
    ntsc24 = plan(*speech, frame_rate=Fraction(24000, 1001))
    assert len(ntsc24.bursts) == 2
    assert ranges(ntsc24, ROLE_FACE_X1_TO_X0) == [(1200, 1215), (1400, 1415)]

    sixty = plan(*speech)
    assert len(sixty.bursts) == 1
    assert ranges(sixty, ROLE_X0_TO_FACE_X1) == [(1000, 1400)]


# ---------------------------------------------------------------------------------------
# Phase 8: multi-level facecam.
#
# The tests above use TIMING, which configures only the two required transitions — that is
# still a valid setup and must keep behaving exactly as it did in Phase 7. Everything below
# uses FULL_TIMING, the six-asset bin this project actually has.
#
# At 60 fps the defaults land on: x2 at +60 frames needing 21 left (so a zoom shorter than 81
# frames never promotes), x3 at +108 needing 30 left (so shorter than 138 never reaches x3).
# ---------------------------------------------------------------------------------------

FULL_TIMING = AssetTiming(
    {
        ROLE_X0_TO_FACE_X1: 15,
        ROLE_FACE_X1_TO_FACE_X2: 15,
        ROLE_FACE_X2_TO_FACE_X3: 15,
        ROLE_FACE_X1_TO_X0: 15,
        ROLE_FACE_X2_TO_X0: 15,
        ROLE_FACE_X3_TO_X0: 15,
    }
)

X2_AT = 1060
X3_AT = 1108


def chain(result):
    """The plan as (role, start, end), which is the whole observable contract."""

    return [(p.asset_role, p.start_frame, p.end_frame) for p in result.placements]


def test_a_short_burst_only_enters_and_leaves() -> None:
    """80 frames is one frame short of earning x2, so nothing above x1 appears."""

    result = plan((1000, 1080), timing=FULL_TIMING)
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 1080),
        (ROLE_FACE_X1_TO_X0, 1080, 1095),
    ]


def test_a_sustained_burst_is_promoted_to_x2_and_resets_from_x2() -> None:
    result = plan((1000, 1100), timing=FULL_TIMING)
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, X2_AT),
        (ROLE_FACE_X1_TO_FACE_X2, X2_AT, 1100),
        (ROLE_FACE_X2_TO_X0, 1100, 1115),
    ]


def test_a_very_long_burst_climbs_the_whole_ladder_and_resets_from_x3() -> None:
    result = plan((1000, 1200), timing=FULL_TIMING)
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, X2_AT),
        (ROLE_FACE_X1_TO_FACE_X2, X2_AT, X3_AT),
        (ROLE_FACE_X2_TO_FACE_X3, X3_AT, 1200),
        (ROLE_FACE_X3_TO_X0, 1200, 1215),
    ]


@pytest.mark.parametrize(
    ("end", "expected_top"),
    [
        # One frame below each threshold, then exactly on it. The rule is `>=`, and a
        # boundary nobody pins down is a boundary that drifts.
        (1080, ROLE_X0_TO_FACE_X1),
        (1081, ROLE_FACE_X1_TO_FACE_X2),
        (1137, ROLE_FACE_X1_TO_FACE_X2),
        (1138, ROLE_FACE_X2_TO_FACE_X3),
    ],
)
def test_a_promotion_needs_enough_burst_left_to_be_worth_it(
    end: int, expected_top: str
) -> None:
    result = plan((1000, end), timing=FULL_TIMING)
    assert result.zoom_placements[-1].asset_role == expected_top


def test_x3_is_never_reached_without_passing_through_x2() -> None:
    """Even on a burst long enough for x3, with no x2 asset the chain stops at x1."""

    no_x2 = AssetTiming(
        {
            ROLE_X0_TO_FACE_X1: 15,
            ROLE_FACE_X2_TO_FACE_X3: 15,
            ROLE_FACE_X1_TO_X0: 15,
            ROLE_FACE_X3_TO_X0: 15,
        }
    )
    result = plan((1000, 1500), timing=no_x2)
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 1500),
        (ROLE_FACE_X1_TO_X0, 1500, 1515),
    ]


def test_unconfigured_levels_are_simply_never_planned() -> None:
    """A user with no x2/x3 assets gets the Phase 7 edit, however long they talk."""

    result = plan((1000, 4000), timing=TIMING)
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 4000),
        (ROLE_FACE_X1_TO_X0, 4000, 4015),
    ]
    assert result.promotion_placements == ()
    assert "no configured asset animation" in " ".join(result.decisions)


def test_a_promotion_is_not_snapped_to_a_nearby_hard_cut() -> None:
    """Resets snap, promotions do not (D047). Measured, not assumed."""

    result = plan((1000, 1200), cuts=(1055, 1112), timing=FULL_TIMING)
    assert [p.start_frame for p in result.promotion_placements] == [X2_AT, X3_AT]
    assert all(p.cut_frame is None for p in result.promotion_placements)


def test_a_reset_still_snaps_to_a_cut_from_any_level() -> None:
    """The reset asset follows the level; the snapping rule does not change with it."""

    # A cut 4 frames before the burst end, inside the 7-frame lookback.
    result = plan((1000, 1200), cuts=(1196,), timing=FULL_TIMING)
    reset = result.reset_placements[0]
    assert (reset.asset_role, reset.start_frame, reset.cut_frame) == (
        ROLE_FACE_X3_TO_X0,
        1196,
        1196,
    )
    assert reset.reason == REASON_RESET_SNAPPED_BACKWARD


def test_the_level_a_burst_reaches_chooses_the_reset_asset() -> None:
    result = plan((1000, 1050), (1200, 1300), (1500, 1700), timing=FULL_TIMING)
    assert [p.asset_role for p in result.reset_placements] == [
        ROLE_FACE_X1_TO_X0,  # 50 frames: never promoted
        ROLE_FACE_X2_TO_X0,  # 100 frames: x2 only
        ROLE_FACE_X3_TO_X0,  # 200 frames: the whole ladder
    ]
    assert result.top_state_counts == {"face_x1": 1, "face_x2": 1, "face_x3": 1}


def test_the_clips_of_a_cycle_are_contiguous_sorted_and_never_overlap() -> None:
    result = plan((1000, 1200), (1500, 1900), (2400, 2450), timing=FULL_TIMING)
    assert result.valid
    assert result.overlaps == ()
    starts = [p.start_frame for p in result.placements]
    assert starts == sorted(starts)
    # Within a cycle every clip hands over on the exact frame the next one starts: a
    # promotion has no end frame of its own, it is ended by whatever follows.
    for earlier, later in zip(result.placements, result.placements[1:], strict=False):
        assert later.start_frame >= earlier.end_frame
        if earlier.asset_role in (ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_FACE_X2):
            assert later.start_frame == earlier.end_frame


def test_a_burst_held_to_the_timeline_end_still_earns_its_levels() -> None:
    result = plan((4800, 5000), timeline=FrameRange(1000, 5000), timing=FULL_TIMING)
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 4800, 4860),
        (ROLE_FACE_X1_TO_FACE_X2, 4860, 4908),
        (ROLE_FACE_X2_TO_FACE_X3, 4908, 5000),
    ]
    assert result.reset_placements == ()
    assert result.placements[-1].end_frame == 5000


def test_thresholds_are_configurable_and_expressed_in_milliseconds() -> None:
    """The defaults are a calibration, not a law: a user may move them."""

    eager = PlannerSettings(
        promote_to_face_x2_after_ms=300,
        min_remaining_after_face_x2_ms=100,
        promote_to_face_x3_after_ms=600,
        min_remaining_after_face_x3_ms=100,
    )
    result = plan((1000, 1100), settings=eager, timing=FULL_TIMING)
    # 300 ms = 18 frames, 600 ms = 36 frames at 60 fps.
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 1018),
        (ROLE_FACE_X1_TO_FACE_X2, 1018, 1036),
        (ROLE_FACE_X2_TO_FACE_X3, 1036, 1100),
        (ROLE_FACE_X3_TO_X0, 1100, 1115),
    ]


def test_x3_cannot_be_configured_to_arrive_before_x2() -> None:
    with pytest.raises(ValueError, match="one rung at a time"):
        PlannerSettings(
            promote_to_face_x2_after_ms=1000, promote_to_face_x3_after_ms=1000
        )


def test_a_promotion_never_truncates_the_clip_it_replaces() -> None:
    """Two thresholds 5 frames apart cannot both fire when the assets need 15."""

    tight = PlannerSettings(
        promote_to_face_x2_after_ms=300,  # 18 frames
        min_remaining_after_face_x2_ms=0,
        promote_to_face_x3_after_ms=383,  # 23 frames — only 5 after x2
        min_remaining_after_face_x3_ms=0,
    )
    result = plan((1000, 1400), settings=tight, timing=FULL_TIMING)
    assert [p.asset_role for p in result.zoom_placements] == [
        ROLE_X0_TO_FACE_X1,
        ROLE_FACE_X1_TO_FACE_X2,
    ]
    assert "shorter than its own" in " ".join(result.decisions)


def test_planning_the_same_material_twice_gives_the_identical_plan() -> None:
    speech = ((1000, 1200), (1500, 1900), (2400, 2450))
    first = plan(*speech, cuts=(1196, 1905), timing=FULL_TIMING)
    second = plan(*speech, cuts=(1196, 1905), timing=FULL_TIMING)
    assert first.to_dict() == second.to_dict()
