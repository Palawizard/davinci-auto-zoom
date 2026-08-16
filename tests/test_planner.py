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
    REASON_RESET_SNAPPED,
    REASON_X1_HELD_TO_TIMELINE_END,
    ROLE_FACECAM_X1,
    ROLE_RESET_X0,
    AssetTiming,
    PlannerSettings,
    frames_from_ms,
    plan_zooms,
)

FPS60 = Fraction(60)
NTSC = Fraction(60000, 1001)

#: This user's assets: both animate in 15 frames. FACE_X0_SMOOTH is 42 frames long in the
#: Media Pool and that number appears nowhere in the planner, which is the point.
TIMING = AssetTiming(facecam_x1_transition_frames=15, reset_x0_transition_frames=15)

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
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1200)]
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215)]
    assert result.x0_placements[0].reason == REASON_RESET_DIRECT
    assert result.valid


def test_a_pause_below_the_gate_keeps_one_continuous_x1() -> None:
    """The zoom must not pop out and back in across a breath."""

    result = plan((1000, 1200), (1230, 1400))  # gap of 30 < 39
    assert len(result.bursts) == 1
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1400)]
    assert ranges(result, ROLE_RESET_X0) == [(1400, 1415)]
    assert result.merged_gaps == 1


def test_a_gap_exactly_at_the_gate_allows_a_reset() -> None:
    result = plan((1000, 1200), (1200 + GATE_FRAMES, 1400))
    assert len(result.bursts) == 2
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1200), (1239, 1400)]
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215), (1400, 1415)]
    assert result.merged_gaps == 0


def test_a_gap_one_frame_below_the_gate_is_bridged() -> None:
    result = plan((1000, 1200), (1200 + GATE_FRAMES - 1, 1400))
    assert len(result.bursts) == 1
    assert result.merged_gaps == 1


def test_a_gap_above_the_gate_resets() -> None:
    result = plan((1000, 1200), (1500, 1700))
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1200), (1500, 1700)]
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215), (1700, 1715)]


# --- x1 length -------------------------------------------------------------------------


def test_x1_is_as_long_as_the_burst_needs() -> None:
    """A FACE_X1 instance is entry animation *plus hold*; 900 frames is normal."""

    result = plan((1000, 1900))
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1900)]
    assert result.x1_placements[0].duration_frames == 900


def test_x1_of_exactly_the_animation_length_is_kept() -> None:
    result = plan((1000, 1015))
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1015)]
    assert result.suppressed_cycles == 0


def test_x1_shorter_than_its_animation_drops_the_whole_cycle() -> None:
    result = plan((1000, 1014))
    assert result.placements == ()
    assert result.suppressed_cycles == 1
    assert any("shorter than its own 15-frame animation" in d for d in result.decisions)


def test_x0_lasts_exactly_its_animation_not_the_assets_native_length() -> None:
    """FACE_X0_SMOOTH is 42 frames in the Media Pool; the plan uses 15."""

    result = plan((1000, 1200))
    x0 = result.x0_placements[0]
    assert x0.duration_frames == 15
    assert x0.duration_frames != 42


def test_a_different_asset_timing_changes_only_the_lengths() -> None:
    timing = AssetTiming(facecam_x1_transition_frames=8, reset_x0_transition_frames=25)
    result = plan((1000, 1010), timing=timing)
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1010)]
    assert ranges(result, ROLE_RESET_X0) == [(1010, 1035)]


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
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215), (1400, 1415)]
    assert result.suppressed_resets == 0


def test_a_pause_one_frame_too_short_holds_the_zoom_instead() -> None:
    result = plan(
        (1000, 1200),
        (1214, 1400),
        settings=PlannerSettings(reset_after_silence_ms=0),
    )
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1400)]
    assert ranges(result, ROLE_RESET_X0) == [(1400, 1415)]
    assert result.suppressed_resets == 1
    assert any("x1 stays active" in decision for decision in result.decisions)


# --- hard cuts -------------------------------------------------------------------------


def test_a_hard_cut_in_the_window_moves_the_reset() -> None:
    result = plan((1000, 1200), cuts=(1210,))
    assert ranges(result, ROLE_RESET_X0) == [(1210, 1225)]
    assert result.x0_placements[0].reason == REASON_RESET_SNAPPED
    assert result.x0_placements[0].cut_frame == 1210
    assert ranges(result, ROLE_FACECAM_X1) == [(1000, 1210)]


def test_the_last_valid_cut_in_the_window_wins() -> None:
    result = plan((1000, 1200), cuts=(1205, 1210, 1215))
    assert result.x0_placements[0].cut_frame == 1215


def test_a_cut_outside_the_window_is_ignored() -> None:
    # 350 ms at 60 fps = 21 frames, so 1222 is out of reach of a reset at 1200.
    result = plan((1000, 1200), cuts=(1222,))
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215)]
    assert result.x0_placements[0].reason == REASON_RESET_DIRECT


def test_a_cut_leaving_only_fourteen_frames_is_rejected() -> None:
    """1210 + 15 > 1224, so that cut cannot fit the reset before the next zoom."""

    result = plan((1000, 1200), (1224, 1400), cuts=(1210,),
                  settings=PlannerSettings(reset_after_silence_ms=0))
    assert result.rejected_cuts == 1
    assert result.x0_placements[0].reason == REASON_RESET_DIRECT
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215), (1400, 1415)]


def test_an_earlier_cut_is_used_when_the_last_one_is_too_late() -> None:
    result = plan((1000, 1200), (1224, 1400), cuts=(1205, 1210),
                  settings=PlannerSettings(reset_after_silence_ms=0))
    assert result.x0_placements[0].cut_frame == 1205
    assert ranges(result, ROLE_RESET_X0) == [(1205, 1220), (1400, 1415)]
    assert result.rejected_cuts == 1


def test_no_cut_is_ever_snapped_before_the_end_of_speech() -> None:
    result = plan((1000, 1200), cuts=(1100, 1199))
    assert result.x0_placements[0].cut_frame is None
    assert ranges(result, ROLE_RESET_X0) == [(1200, 1215)]


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
    assert result.x0_placements[0].reason == REASON_RESET_DIRECT


# --- timeline edges --------------------------------------------------------------------


def test_speech_at_the_very_start_is_clamped_to_the_range() -> None:
    result = plan(
        (1000, 1200), settings=PlannerSettings(zoom_lead_in_ms=200)  # 12 frames before 1000
    )
    assert result.x1_placements[0].start_frame == TIMELINE.start


def test_the_last_burst_resets_when_the_animation_fits() -> None:
    result = plan((4900, 4985), timeline=FrameRange(1000, 5000))
    assert ranges(result, ROLE_RESET_X0) == [(4985, 5000)]


def test_the_last_burst_holds_the_zoom_when_only_fourteen_frames_remain() -> None:
    result = plan((4900, 4986), timeline=FrameRange(1000, 5000))
    assert ranges(result, ROLE_RESET_X0) == []
    assert ranges(result, ROLE_FACECAM_X1) == [(4900, 5000)]
    assert result.x1_placements[0].reason == REASON_X1_HELD_TO_TIMELINE_END
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
    assert result.x1_placements
    assert all(p.duration_frames >= 1 for p in result.placements)


def test_a_fractional_rate_changes_the_gate_not_the_frame_arithmetic() -> None:
    """The same 16-frame pause is a reset at 23.976 fps and a bridged breath at 60 fps."""

    # 650 ms is 15.58 frames at 24000/1001 (gate 16) and 39 frames at 60.
    speech = ((1000, 1200), (1216, 1400))
    ntsc24 = plan(*speech, frame_rate=Fraction(24000, 1001))
    assert len(ntsc24.bursts) == 2
    assert ranges(ntsc24, ROLE_RESET_X0) == [(1200, 1215), (1400, 1415)]

    sixty = plan(*speech)
    assert len(sixty.bursts) == 1
    assert ranges(sixty, ROLE_FACECAM_X1) == [(1000, 1400)]
