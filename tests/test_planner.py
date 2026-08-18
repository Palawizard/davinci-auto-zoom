"""Pure planner tests — no Resolve, no ffmpeg, no ONNX, no filesystem.

Every case here is a concrete editing situation written as frames. The numbers use the test
project's setup (60 fps, 15-frame x1 animation, 15-frame x0 animation) because that is the
setup the rules were derived from, but nothing in the planner hardcodes them.
"""

from fractions import Fraction

import pytest

from davinci_auto_zoom.domain.dynamics import (
    CUE_NO_RECOVERY,
    CUE_VALLEY_TOO_LONG,
    CUE_VALLEY_TOO_SHORT,
    EnergyEnvelope,
    EnergyPoint,
    EnergySettings,
)
from davinci_auto_zoom.domain.models import FrameRange, SpeechSegment
from davinci_auto_zoom.domain.planner import (
    CUE_REJECTED_ANIMATION,
    CUE_REJECTED_AT_TOP,
    CUE_REJECTED_NO_ROOM,
    REASON_ENTRY_DIRECT,
    REASON_ENTRY_SNAPPED_BACKWARD,
    REASON_ENTRY_SNAPPED_FORWARD,
    REASON_PROMOTED_SNAPPED_BACKWARD,
    REASON_PROMOTED_SNAPPED_FORWARD,
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
    FACECAM_LADDER,
    GAMEPLAY_ROLES,
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
    energy=None,
):
    return plan_zooms(
        timeline=timeline,
        frame_rate=frame_rate,
        speech_segments=[SpeechSegment(FrameRange(start, end)) for start, end in speech],
        timing=timing,
        hard_cuts=cuts,
        settings=settings or PlannerSettings(),
        energy=energy,
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
# Phase 8c: the facecam ladder is climbed on voice dynamics.
#
# The tests above use TIMING, which configures only the two required transitions — that is
# still a valid setup and must keep behaving exactly as it did in Phase 7. Everything below
# uses FULL_TIMING, the six-asset bin this project actually has, and feeds the planner a
# synthetic energy envelope: constant speaking level with holes punched in it.
#
# Nothing here promotes because a burst is long. A burst of any length with a flat envelope
# stays at x1, and a short burst with two clean breaks climbs to x3 — which is exactly the
# behaviour Phase 8's duration rule could not produce (D049).
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

SPEAKING_DB = -20.0
QUIET_DB = -60.0


def envelope(
    *valleys: tuple[int, int],
    span: FrameRange = TIMELINE,
    frame_rate: Fraction = FPS60,
    speaking: float = SPEAKING_DB,
    quiet: float = QUIET_DB,
    settings: EnergySettings | None = None,
) -> EnergyEnvelope:
    """A constant speaking level over `span`, with `valleys` punched out of it.

    Written as frames rather than samples on purpose: these tests are about the planner's
    reading of an envelope, and `speech/energy.py` is what turns PCM into one.
    """

    settings = settings or EnergySettings()
    points: list[EnergyPoint] = []
    step = Fraction(settings.hop_ms, 1000) * frame_rate
    index = 0
    while True:
        frame = span.start + int(index * step)
        if frame >= span.end:
            break
        inside = any(start <= frame < end for start, end in valleys)
        points.append(EnergyPoint(frame, quiet if inside else speaking))
        index += 1
    return EnergyEnvelope(tuple(points), settings)


def chain(result):
    """The plan as (role, start, end), which is the whole observable contract."""

    return [(p.asset_role, p.start_frame, p.end_frame) for p in result.placements]


def test_a_burst_with_no_break_in_the_voice_never_promotes_however_long_it_is() -> None:
    """The Phase 8 rule would have reached x3 here. Duration alone now earns nothing."""

    result = plan((1000, 4000), timing=FULL_TIMING, energy=envelope())
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 4000),
        (ROLE_FACE_X1_TO_X0, 4000, 4015),
    ]
    assert result.promotion_placements == ()


def test_no_envelope_at_all_is_a_one_level_edit_and_says_so() -> None:
    result = plan((1000, 4000), timing=FULL_TIMING)
    assert result.promotion_placements == ()
    assert "no energy envelope supplied" in " ".join(result.decisions)


def test_one_valley_and_recovery_promotes_to_x2_on_the_recovery() -> None:
    result = plan((1000, 1200), timing=FULL_TIMING, energy=envelope((1100, 1110)))
    roles = [p.asset_role for p in result.placements]
    assert roles == [ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_FACE_X2, ROLE_FACE_X2_TO_X0]
    # The promotion is anchored on the RECOVERY, never on the floor or the start of the dip.
    promotion = result.promotion_placements[0]
    assert promotion.start_frame >= 1110
    assert promotion.start_frame <= 1111
    assert result.placements[0].end_frame == promotion.start_frame


def test_two_valleys_climb_the_whole_ladder() -> None:
    result = plan(
        (1000, 1300), timing=FULL_TIMING, energy=envelope((1100, 1110), (1200, 1210))
    )
    assert [p.asset_role for p in result.placements] == [
        ROLE_X0_TO_FACE_X1,
        ROLE_FACE_X1_TO_FACE_X2,
        ROLE_FACE_X2_TO_FACE_X3,
        ROLE_FACE_X3_TO_X0,
    ]


def test_a_short_burst_with_two_breaks_reaches_x3_where_a_long_flat_one_stays_at_x1() -> None:
    """The whole point of Phase 8c, as one comparison."""

    short = plan(
        (1000, 1160), timing=FULL_TIMING, energy=envelope((1050, 1060), (1100, 1110))
    )
    long_flat = plan((1000, 2000), timing=FULL_TIMING, energy=envelope())
    assert short.top_state_counts["face_x3"] == 1
    assert long_flat.top_state_counts["face_x1"] == 1


def test_a_fourth_valley_changes_nothing_because_the_ladder_tops_out() -> None:
    result = plan(
        (1000, 1500),
        timing=FULL_TIMING,
        energy=envelope((1100, 1110), (1200, 1210), (1300, 1310), (1400, 1410)),
    )
    assert [p.asset_role for p in result.zoom_placements] == [
        ROLE_X0_TO_FACE_X1,
        ROLE_FACE_X1_TO_FACE_X2,
        ROLE_FACE_X2_TO_FACE_X3,
    ]
    assert result.cue_outcomes.count(CUE_REJECTED_AT_TOP) == 2


def test_a_valley_that_never_recovers_is_not_a_promotion() -> None:
    """The creator stopped talking. That is a reset's business, not a promotion's."""

    result = plan((1000, 1200), timing=FULL_TIMING, energy=envelope((1100, 1200)))
    assert result.promotion_placements == ()
    assert CUE_NO_RECOVERY in result.cue_outcomes


def test_a_dip_too_shallow_to_be_a_break_is_ignored() -> None:
    shallow = envelope((1100, 1110), quiet=SPEAKING_DB - 5.0)
    result = plan((1000, 1200), timing=FULL_TIMING, energy=shallow)
    assert result.promotion_placements == ()
    assert result.valleys == ()


def test_a_dip_too_brief_to_be_a_break_is_rejected_with_its_reason() -> None:
    result = plan(
        (1000, 1200),
        timing=FULL_TIMING,
        energy=envelope((1100, 1101)),
        settings=PlannerSettings(promotion_min_valley_ms=100),
    )
    assert result.promotion_placements == ()
    assert CUE_VALLEY_TOO_SHORT in result.cue_outcomes


def test_a_dip_longer_than_the_upper_bound_is_rejected_with_its_reason() -> None:
    result = plan(
        (1000, 1300),
        timing=FULL_TIMING,
        energy=envelope((1100, 1160)),
        settings=PlannerSettings(promotion_max_valley_ms=200),
    )
    assert result.promotion_placements == ()
    assert CUE_VALLEY_TOO_LONG in result.cue_outcomes


def test_a_cue_inside_the_previous_animation_is_refused_and_the_next_one_used() -> None:
    """15 frames of animation are 15 frames of animation; the cue is skipped, not moved."""

    result = plan(
        (1000, 1200), timing=FULL_TIMING, energy=envelope((1004, 1008), (1100, 1110))
    )
    assert [p.asset_role for p in result.zoom_placements] == [
        ROLE_X0_TO_FACE_X1,
        ROLE_FACE_X1_TO_FACE_X2,
    ]
    assert CUE_REJECTED_ANIMATION in result.cue_outcomes
    assert result.promotion_placements[0].start_frame >= 1110


def test_a_second_cue_too_close_to_the_first_promotion_cannot_reach_x3() -> None:
    result = plan(
        (1000, 1200), timing=FULL_TIMING, energy=envelope((1100, 1110), (1114, 1118))
    )
    assert result.top_state_counts["face_x2"] == 1
    assert CUE_REJECTED_ANIMATION in result.cue_outcomes


def test_a_promotion_with_no_room_to_be_held_before_the_reset_is_refused() -> None:
    """The level would flash for a few frames. `promotion_min_hold_ms` says no."""

    result = plan((1000, 1200), timing=FULL_TIMING, energy=envelope((1170, 1180)))
    assert result.promotion_placements == ()
    assert CUE_REJECTED_NO_ROOM in result.cue_outcomes
    assert result.reset_placements[0].asset_role == ROLE_FACE_X1_TO_X0


def test_x3_is_never_reached_without_passing_through_x2() -> None:
    """Even with two clean cues, with no x2 asset the chain stops at x1."""

    no_x2 = AssetTiming(
        {
            ROLE_X0_TO_FACE_X1: 15,
            ROLE_FACE_X2_TO_FACE_X3: 15,
            ROLE_FACE_X1_TO_X0: 15,
            ROLE_FACE_X3_TO_X0: 15,
        }
    )
    result = plan(
        (1000, 1300), timing=no_x2, energy=envelope((1100, 1110), (1200, 1210))
    )
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 1300),
        (ROLE_FACE_X1_TO_X0, 1300, 1315),
    ]


def test_unconfigured_levels_are_simply_never_planned() -> None:
    """A user with no x2/x3 assets gets the Phase 7 edit, however clean their delivery."""

    result = plan((1000, 4000), timing=TIMING, energy=envelope((1100, 1110)))
    assert chain(result) == [
        (ROLE_X0_TO_FACE_X1, 1000, 4000),
        (ROLE_FACE_X1_TO_X0, 4000, 4015),
    ]
    assert "no configured asset animation" in " ".join(result.decisions)


def test_the_level_a_burst_reaches_chooses_the_reset_asset() -> None:
    result = plan(
        (1000, 1050),
        (1200, 1400),
        (1600, 1900),
        timing=FULL_TIMING,
        energy=envelope((1300, 1310), (1700, 1710), (1800, 1810)),
    )
    assert [p.asset_role for p in result.reset_placements] == [
        ROLE_FACE_X1_TO_X0,  # no cue at all
        ROLE_FACE_X2_TO_X0,  # one cue
        ROLE_FACE_X3_TO_X0,  # two cues
    ]
    assert result.top_state_counts == {"face_x1": 1, "face_x2": 1, "face_x3": 1}


def test_the_clips_of_a_cycle_are_contiguous_sorted_and_never_overlap() -> None:
    result = plan(
        (1000, 1300),
        (1500, 1900),
        (2400, 2450),
        timing=FULL_TIMING,
        energy=envelope((1100, 1110), (1200, 1210), (1600, 1610)),
    )
    assert result.valid
    assert result.overlaps == ()
    starts = [p.start_frame for p in result.placements]
    assert starts == sorted(starts)
    for earlier, later in zip(result.placements, result.placements[1:], strict=False):
        assert later.start_frame >= earlier.end_frame
        if earlier.asset_role in (ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_FACE_X2):
            assert later.start_frame == earlier.end_frame


def test_a_burst_held_to_the_timeline_end_still_earns_its_levels() -> None:
    result = plan(
        (4700, 5000),
        timeline=FrameRange(1000, 5000),
        timing=FULL_TIMING,
        energy=envelope((4800, 4810), (4900, 4910)),
    )
    assert [p.asset_role for p in result.zoom_placements] == [
        ROLE_X0_TO_FACE_X1,
        ROLE_FACE_X1_TO_FACE_X2,
        ROLE_FACE_X2_TO_FACE_X3,
    ]
    assert result.reset_placements == ()
    assert result.placements[-1].end_frame == 5000


def test_the_detector_thresholds_are_configurable() -> None:
    """The defaults are a calibration, not a law."""

    shallow = envelope((1100, 1110), quiet=SPEAKING_DB - 8.0)
    assert plan((1000, 1200), timing=FULL_TIMING, energy=shallow).promotion_placements == ()
    eager = PlannerSettings(promotion_min_drop_db=5, promotion_recovery_within_db=2)
    promoted = plan((1000, 1200), timing=FULL_TIMING, energy=shallow, settings=eager)
    assert promoted.top_state_counts["face_x2"] == 1


def test_a_recovery_line_at_or_above_the_drop_line_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="climb back above"):
        PlannerSettings(promotion_min_drop_db=6, promotion_recovery_within_db=6)


def test_the_plan_reports_every_valley_it_saw_including_the_ones_it_refused() -> None:
    result = plan(
        (1000, 1200), timing=FULL_TIMING, energy=envelope((1004, 1008), (1100, 1110))
    )
    assert len(result.valleys) == len(result.cue_outcomes) == 2
    payload = result.to_dict()["voice_valleys"]
    assert [row["outcome"] for row in payload] == [CUE_REJECTED_ANIMATION, "face_x2"]
    assert payload[1]["drop_db"] == pytest.approx(40.0, abs=0.5)
    assert payload[1]["recovery_frame"] == result.promotion_placements[0].start_frame


# --- cut snapping, now for every facecam transition (D052) -------------------------------


def test_an_entry_snaps_backward_to_a_cut_just_before_the_burst() -> None:
    """Allowed on purpose: the cut just before the first word is often the right edit (D052)."""

    result = plan((1100, 1300), cuts=(1096,), timing=FULL_TIMING, energy=envelope())
    entry = result.entry_placements[0]
    assert (entry.start_frame, entry.cut_frame) == (1096, 1096)
    assert entry.reason == REASON_ENTRY_SNAPPED_BACKWARD


def test_an_entry_snaps_forward_to_a_cut_just_after_the_burst_start() -> None:
    result = plan((1000, 1200), cuts=(1005,), timing=FULL_TIMING, energy=envelope())
    entry = result.entry_placements[0]
    assert (entry.start_frame, entry.cut_frame) == (1005, 1005)
    assert entry.reason == REASON_ENTRY_SNAPPED_FORWARD


def test_the_entry_takes_the_nearest_cut_and_prefers_the_later_one_on_a_tie() -> None:
    nearest = plan((1000, 1200), cuts=(996, 1003), timing=FULL_TIMING, energy=envelope())
    assert nearest.entry_placements[0].start_frame == 1003
    tie = plan((1000, 1200), cuts=(997, 1003), timing=FULL_TIMING, energy=envelope())
    assert tie.entry_placements[0].start_frame == 1003


def test_a_cut_outside_the_zoom_window_never_moves_the_entry() -> None:
    """7 frames at 60 fps. A cut 8 frames out is not this transition's cut."""

    result = plan((1000, 1200), cuts=(992,), timing=FULL_TIMING, energy=envelope())
    entry = result.entry_placements[0]
    assert (entry.start_frame, entry.cut_frame) == (1000, None)
    assert entry.reason == REASON_ENTRY_DIRECT


def test_no_cut_at_all_leaves_every_anchor_exactly_where_the_audio_put_it() -> None:
    with_cuts = plan(
        (1000, 1200), cuts=(1104,), timing=FULL_TIMING, energy=envelope((1100, 1110))
    )
    without = plan((1000, 1200), timing=FULL_TIMING, energy=envelope((1100, 1110)))
    assert with_cuts.promotion_placements[0].start_frame != 0
    assert without.entry_placements[0].start_frame == 1000
    assert all(p.cut_frame is None for p in without.placements)


def test_a_promotion_snaps_to_a_cut_near_its_recovery_and_not_near_the_burst_start() -> None:
    result = plan(
        (1000, 1200),
        cuts=(1002, 1113),
        timing=FULL_TIMING,
        energy=envelope((1100, 1110)),
    )
    promotion = result.promotion_placements[0]
    assert (promotion.start_frame, promotion.cut_frame) == (1113, 1113)
    assert promotion.reason == REASON_PROMOTED_SNAPPED_FORWARD


def test_a_promotion_snaps_backward_too() -> None:
    result = plan(
        (1000, 1200), cuts=(1106,), timing=FULL_TIMING, energy=envelope((1100, 1110))
    )
    promotion = result.promotion_placements[0]
    assert (promotion.start_frame, promotion.cut_frame) == (1106, 1106)
    assert promotion.reason == REASON_PROMOTED_SNAPPED_BACKWARD


def test_a_nearer_cut_that_would_truncate_an_animation_gives_way_to_a_valid_one() -> None:
    """The recovery is a valid anchor at +15, but the nearest cut sits at +14."""

    result = plan(
        (1000, 1200),
        cuts=(1014, 1019),
        timing=FULL_TIMING,
        energy=envelope((1010, 1015)),
    )
    promotion = result.promotion_placements[0]
    assert (promotion.start_frame, promotion.cut_frame) == (1019, 1019)
    assert "rejected for face_x2" in " ".join(result.decisions)


def test_an_entry_is_never_snapped_outside_the_analysed_range() -> None:
    """Same predicate that stops an entry overlapping the cycle before it: a floor."""

    result = plan(
        (1002, 1300),
        timeline=FrameRange(1000, 5000),
        cuts=(998,),
        timing=FULL_TIMING,
        energy=envelope(),
    )
    assert result.valid
    assert result.entry_placements[0].start_frame == 1002
    assert result.entry_placements[0].cut_frame is None


def test_a_reset_still_snaps_to_a_cut_from_any_level() -> None:
    """The reset asset follows the level; Phase 6's rule does not change with it."""

    result = plan(
        (1000, 1300),
        cuts=(1296,),
        timing=FULL_TIMING,
        energy=envelope((1100, 1110), (1200, 1210)),
    )
    reset = result.reset_placements[0]
    assert (reset.asset_role, reset.start_frame, reset.cut_frame) == (
        ROLE_FACE_X3_TO_X0,
        1296,
        1296,
    )
    assert reset.reason == REASON_RESET_SNAPPED_BACKWARD


def test_planning_the_same_material_twice_gives_the_identical_plan() -> None:
    speech = ((1000, 1300), (1500, 1900), (2400, 2450))
    energy = envelope((1100, 1110), (1600, 1610))
    first = plan(*speech, cuts=(1296, 1905), timing=FULL_TIMING, energy=energy)
    second = plan(*speech, cuts=(1296, 1905), timing=FULL_TIMING, energy=energy)
    assert first.to_dict() == second.to_dict()


# ---------------------------------------------------------------------------------------
# Phase 9a backwards compatibility.
#
# Adding STATE_GAMEPLAY to the graph made six new roles configurable. They are optional
# capabilities, and the promise attached to them is precise: a project that does not
# configure a gameplay asset must keep producing exactly the plan it produced before the
# state existed (D054). These tests are that promise, in code.
# ---------------------------------------------------------------------------------------

GAMEPLAY_TIMING = AssetTiming(
    dict(FULL_TIMING.transition_frames)
    | {role: 15 for role in GAMEPLAY_ROLES}
)


def test_configuring_gameplay_assets_does_not_change_the_facecam_plan() -> None:
    """Same speech, same cuts, same envelope: the six extra assets must be inert."""

    speech = ((1100, 1900), (2400, 3000), (3400, 4200))
    energy = envelope((1400, 1420), (1600, 1620), (2600, 2620))
    without = plan(*speech, timing=FULL_TIMING, cuts=(1099, 2405), energy=energy)
    with_gameplay = plan(*speech, timing=GAMEPLAY_TIMING, cuts=(1099, 2405), energy=energy)
    assert [p.to_dict() for p in with_gameplay.placements] == [
        p.to_dict() for p in without.placements
    ]
    assert with_gameplay.cue_outcomes == without.cue_outcomes
    # The trace's opening line inventories the configured animations, so it legitimately
    # lists six more assets. Every line that reports a *decision* has to be identical.
    assert [d for d in with_gameplay.decisions if "transition animations" not in d] == [
        d for d in without.decisions if "transition animations" not in d
    ]


def test_the_planner_still_places_no_gameplay_role_when_one_is_configured() -> None:
    """Phase 9a measures gameplay and plans none of it. The planner has not learned it."""

    result = plan((1100, 1900), (2400, 3000), timing=GAMEPLAY_TIMING, energy=envelope())
    assert result.placements
    assert not [p for p in result.placements if p.asset_role in GAMEPLAY_ROLES]
    for role in GAMEPLAY_ROLES:
        assert result.role_counts[role] == 0


def test_a_two_asset_config_is_still_a_complete_configuration() -> None:
    """The Phase 7 minimum: no x2/x3, no gameplay, and a plan comes out anyway."""

    result = plan((1100, 1900), timing=TIMING)
    assert [p.asset_role for p in result.placements] == [
        ROLE_X0_TO_FACE_X1,
        ROLE_FACE_X1_TO_X0,
    ]


def test_the_ladder_peak_count_ignores_states_that_are_not_rungs() -> None:
    """`top_state_counts` ranks facecam levels; gameplay is a subject, not a height."""

    result = plan((1100, 1900), (2400, 3000), timing=GAMEPLAY_TIMING, energy=envelope())
    assert set(result.top_state_counts) == set(FACECAM_LADDER)
    assert sum(result.top_state_counts.values()) == len(result.entry_placements)
