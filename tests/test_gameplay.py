"""The Phase 9a gameplay study, as pure functions — no Resolve, no ffmpeg, no numpy.

Two things are being protected here. The first is the *measurement* code: silence windows,
window features and the reconstruction of a human edit have to be right, because the whole
phase is an argument built on their output. The second is the boundary: a decision may only
look at neutral, relative facts about its own window, and must never be able to recognise
which window it is.
"""

from dataclasses import replace
from fractions import Fraction

import pytest

from davinci_auto_zoom.domain.gameplay import (
    ANCHOR_DIRECT,
    ANCHOR_SNAPPED_BACKWARD,
    ANCHOR_SNAPPED_FORWARD,
    MANUAL_GAMEPLAY,
    MANUAL_MIXED,
    MANUAL_X0,
    REASON_NO_SUPPORT,
    REASON_NOT_TALKING,
    REASON_NOT_ZOOM_WORTHY,
    REASON_SECONDARY_AUDIO,
    REASON_TAIL,
    REASON_TOO_SHORT,
    REASON_VISUAL_ACTIVITY,
    REASON_WINDOW_TOO_SHORT_FOR_PRIOR,
    REASON_ZOOM_WORTHY,
    GameplayPolicySettings,
    GameplayWindowFeatures,
    SilenceWindow,
    decide_gameplay,
    manual_coverage,
    percentile,
    propose_episode,
    reference_states,
    secondary_audio_features,
    silence_windows,
    video_activity_features,
)
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.transitions import (
    ROLE_FACE_X1_TO_GAMEPLAY,
    ROLE_GAMEPLAY_TO_FACE_X1,
    ROLE_X0_TO_GAMEPLAY,
    STATE_FACE_X1,
    STATE_X0,
    ForbiddenTransition,
)
from davinci_auto_zoom.domain.visual_episodes import (
    ZOOM_NO_EVENT,
    VisualEpisodeFeatures,
)

FPS = Fraction(60)

#: The user's own bin, mapped to the graph. Exactly what the study is configured with.
ROLE_OF = {
    "FACE_X1": "x0_to_face_x1",
    "FACE_X2": "face_x1_to_face_x2",
    "FACE_X3": "face_x2_to_face_x3",
    "X1_TO_X0": "face_x1_to_x0",
    "X2_TO_X0": "face_x2_to_x0",
    "X3_TO_X0": "face_x3_to_x0",
    "X0_TO_GAMEPLAY": "x0_to_gameplay",
    "X1_TO_GAMEPLAY": "face_x1_to_gameplay",
    "X2_TO_GAMEPLAY": "face_x2_to_gameplay",
    "X3_TO_GAMEPLAY": "face_x3_to_gameplay",
    "GAMEPLAY_TO_X0": "gameplay_to_x0",
    "GAMEPLAY_TO_X1": "gameplay_to_face_x1",
}


def window(start: int, end: int, index: int = 0, **kwargs: bool) -> SilenceWindow:
    return SilenceWindow(index, FrameRange(start, end), **kwargs)


# ---------------------------------------------------------------------------------------
# silence windows
# ---------------------------------------------------------------------------------------


def test_the_gaps_between_bursts_are_the_population() -> None:
    windows = silence_windows(
        FrameRange(1000, 2000),
        [FrameRange(1100, 1200), FrameRange(1500, 1600)],
    )
    assert [(w.start_frame, w.end_frame) for w in windows] == [
        (1000, 1100),
        (1200, 1500),
        (1600, 2000),
    ]
    assert [w.duration_frames for w in windows] == [100, 300, 400]
    assert [w.index for w in windows] == [0, 1, 2]


def test_the_head_and_the_tail_of_the_timeline_are_marked() -> None:
    """They are not pauses in a conversation, and the reference edit treats them differently."""

    windows = silence_windows(FrameRange(0, 500), [FrameRange(100, 200)])
    assert (windows[0].at_timeline_start, windows[0].at_timeline_end) == (True, False)
    assert (windows[1].at_timeline_start, windows[1].at_timeline_end) == (False, True)


def test_a_burst_touching_the_timeline_edge_produces_no_empty_window() -> None:
    windows = silence_windows(FrameRange(0, 300), [FrameRange(0, 150), FrameRange(150, 300)])
    assert windows == ()


def test_overlapping_and_unsorted_bursts_still_give_one_clean_gap() -> None:
    windows = silence_windows(
        FrameRange(0, 400),
        [FrameRange(200, 300), FrameRange(50, 120), FrameRange(100, 160)],
    )
    assert [(w.start_frame, w.end_frame) for w in windows] == [(0, 50), (160, 200), (300, 400)]


def test_a_timeline_with_no_speech_at_all_is_one_window() -> None:
    windows = silence_windows(FrameRange(10, 90), [])
    assert len(windows) == 1
    assert windows[0].at_timeline_start and windows[0].at_timeline_end


@pytest.mark.parametrize(
    ("frame_rate", "frames", "expected_ms"),
    [
        (Fraction(60), 60, 1000),
        (Fraction(30000, 1001), 30, 1001),  # 29.97: the exact rate, not the rounded one
        (Fraction(24000, 1001), 24, 1001),
    ],
)
def test_window_duration_in_ms_uses_the_exact_fractional_rate(
    frame_rate: Fraction, frames: int, expected_ms: int
) -> None:
    assert window(0, frames).duration_ms(frame_rate) == expected_ms


# ---------------------------------------------------------------------------------------
# reading the human edit
# ---------------------------------------------------------------------------------------


def test_a_manual_gameplay_episode_is_reconstructed_with_its_neighbourhood() -> None:
    states = reference_states(
        [
            ("FACE_X1", 100, 200),
            ("X1_TO_GAMEPLAY", 200, 400),
            ("GAMEPLAY_TO_X1", 400, 450),
            ("X1_TO_X0", 450, 465),
        ],
        ROLE_OF,
        bursts=[FrameRange(100, 190), FrameRange(460, 500)],
    )
    assert states.final_state == STATE_X0
    assert len(states.episodes) == 1
    episode = states.episodes[0]
    assert (episode.start_frame, episode.end_frame, episode.duration_frames) == (200, 400, 200)
    assert episode.entry_role == ROLE_FACE_X1_TO_GAMEPLAY
    assert episode.exit_role == ROLE_GAMEPLAY_TO_FACE_X1
    assert (episode.previous_state, episode.next_state) == (STATE_FACE_X1, STATE_FACE_X1)
    assert episode.frames_since_voice == 10  # 200 - 190
    assert episode.frames_until_voice == 60  # 460 - 400


def test_an_episode_the_editor_started_while_the_vad_still_heard_speech_is_negative() -> None:
    """It happens, and it is a fact about burst extent — not something to clamp away."""

    states = reference_states(
        [("X0_TO_GAMEPLAY", 300, 400), ("GAMEPLAY_TO_X0", 400, 415)],
        ROLE_OF,
        bursts=[FrameRange(100, 380)],
    )
    assert states.episodes[0].frames_since_voice == -80


def test_a_move_outside_the_graph_is_reported_not_interpreted() -> None:
    """The whole point of reading MVP3 through the graph: a surprise must stop the analysis."""

    with pytest.raises(ForbiddenTransition, match="not in the state graph"):
        reference_states(
            [
                ("X0_TO_GAMEPLAY", 0, 100),
                # GAMEPLAY -> FACE_X2 is deliberately not authorised.
                ("FACE_X2", 100, 200),
            ],
            ROLE_OF,
        )


def test_a_clip_with_no_configured_role_stops_the_analysis() -> None:
    with pytest.raises(ForbiddenTransition, match="no configured transition role"):
        reference_states([("SOMETHING_ELSE", 0, 100)], ROLE_OF)


def test_an_asset_that_does_not_start_where_the_picture_is_stops_the_analysis() -> None:
    """X0 -> FACE_X1 is a legal move, but GAMEPLAY_TO_X1 is not the asset that performs it."""

    with pytest.raises(ForbiddenTransition, match="but the picture is at"):
        reference_states([("GAMEPLAY_TO_X1", 0, 15), ("X1_TO_X0", 15, 30)], ROLE_OF)


def test_a_timeline_that_ends_on_the_game_is_an_error() -> None:
    with pytest.raises(ForbiddenTransition, match="never left"):
        reference_states([("X0_TO_GAMEPLAY", 0, 100)], ROLE_OF)


def test_clips_are_ordered_before_they_are_walked() -> None:
    states = reference_states(
        [("GAMEPLAY_TO_X0", 100, 115), ("X0_TO_GAMEPLAY", 0, 100)], ROLE_OF
    )
    assert [t.role for t in states.transitions] == ["x0_to_gameplay", "gameplay_to_x0"]


# ---------------------------------------------------------------------------------------
# classifying what the human did with a window
# ---------------------------------------------------------------------------------------


def _episodes(*spans: tuple[int, int]) -> list:
    states = reference_states(
        [
            item
            for start, end in spans
            for item in (("X0_TO_GAMEPLAY", start, end), ("GAMEPLAY_TO_X0", end, end + 15))
        ],
        ROLE_OF,
    )
    return list(states.episodes)


def test_a_window_the_human_filled_is_gameplay_and_an_empty_one_is_x0() -> None:
    episodes = _episodes((100, 195))
    assert manual_coverage(window(100, 200), episodes).verdict == MANUAL_GAMEPLAY
    assert manual_coverage(window(400, 500), episodes).verdict == MANUAL_X0


def test_a_partly_filled_window_is_mixed_and_reports_its_fraction() -> None:
    episodes = _episodes((150, 200))
    coverage = manual_coverage(window(100, 200), episodes)
    assert coverage.verdict == MANUAL_MIXED
    assert coverage.gameplay_frames == 50
    assert coverage.fraction(window(100, 200)) == pytest.approx(0.5)


def test_only_the_overlap_counts_when_an_episode_spills_out_of_the_window() -> None:
    """An episode that starts inside a burst must not be credited for the part outside."""

    episodes = _episodes((50, 150))
    coverage = manual_coverage(window(100, 200), episodes)
    assert coverage.gameplay_frames == 50
    assert coverage.gameplay_start == 50  # the episode's own start, reported unclipped


# ---------------------------------------------------------------------------------------
# objective features — relative, and deterministic
# ---------------------------------------------------------------------------------------


def test_percentile_is_nearest_rank_and_refuses_an_empty_sequence() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 1.0) == 5.0
    assert percentile([5.0, 1.0, 3.0], 0.5) == 3.0
    with pytest.raises(ValueError, match="empty"):
        percentile([], 0.5)


def test_digital_silence_scores_no_activity_and_no_onsets() -> None:
    envelope = [(frame, -140.0) for frame in range(100, 200)]
    features = secondary_audio_features(envelope, window(100, 200), reference_db=-20.0)
    assert features.active_fraction == 0.0
    assert features.onsets == 0
    assert features.dynamic_range_db == 0.0


def test_a_constant_level_has_no_dynamic_range_whatever_the_level_is() -> None:
    for level in (-6.0, -30.0, -60.0):
        envelope = [(frame, level) for frame in range(0, 100)]
        features = secondary_audio_features(envelope, window(0, 100), reference_db=level)
        assert features.dynamic_range_db == 0.0
        assert features.mean_db == pytest.approx(0.0)
        assert features.active_fraction == 1.0


def test_a_burst_of_energy_raises_the_active_fraction_and_the_range() -> None:
    envelope = [(frame, -60.0) for frame in range(0, 50)]
    envelope += [(frame, -10.0) for frame in range(50, 100)]
    features = secondary_audio_features(envelope, window(0, 100), reference_db=-10.0)
    assert features.active_fraction == pytest.approx(0.5)
    assert features.dynamic_range_db == pytest.approx(50.0)
    assert features.thirds_db[0] < features.thirds_db[2]


def test_an_onset_is_counted_once_per_sudden_rise() -> None:
    envelope = [(0, -50.0), (1, -50.0), (2, -20.0), (3, -20.0), (4, -50.0), (5, -19.0)]
    features = secondary_audio_features(envelope, window(0, 10), reference_db=-20.0)
    assert features.onsets == 2


def test_a_global_gain_change_cannot_change_an_audio_feature() -> None:
    """The rule `dynamics.py` already follows (D051): every level is a difference."""

    base = [(frame, -40.0 + frame) for frame in range(0, 30)]
    quiet = [(frame, level - 18.0) for frame, level in base]
    loud = [(frame, level + 7.5) for frame, level in base]
    reference = -25.0
    assert secondary_audio_features(
        quiet, window(0, 30), reference - 18.0
    ) == secondary_audio_features(loud, window(0, 30), reference + 7.5)


def test_an_unmeasured_window_reports_zero_samples_rather_than_silence() -> None:
    features = secondary_audio_features([(500, -10.0)], window(0, 100), reference_db=-10.0)
    assert features.samples == 0


def test_identical_frames_are_zero_visual_activity() -> None:
    envelope = [(frame, 0.0) for frame in range(0, 100)]
    features = video_activity_features(envelope, window(0, 100), 0.05, (), FPS)
    assert features.mean_motion == 0.0
    assert features.active_fraction == 0.0
    assert features.variance == 0.0


def test_motion_is_normalised_so_two_timelines_are_comparable() -> None:
    envelope = [(frame, 0.10) for frame in range(0, 60)]
    busy = video_activity_features(envelope, window(0, 60), 0.05, (), FPS)
    typical = video_activity_features(envelope, window(0, 60), 0.10, (), FPS)
    assert busy.mean_motion == pytest.approx(2.0)
    assert typical.mean_motion == pytest.approx(1.0)
    assert busy.active_fraction == 1.0


def test_cut_density_counts_the_edits_inside_the_window_per_second() -> None:
    envelope = [(frame, 0.05) for frame in range(0, 120)]
    features = video_activity_features(
        envelope, window(0, 120), 0.05, (10, 50, 119, 500), FPS
    )
    assert features.hard_cuts == 3
    assert features.cut_density == pytest.approx(1.5)  # 3 cuts over 2 seconds


def test_features_are_deterministic() -> None:
    envelope = [(frame, -30.0 + (frame % 7)) for frame in range(0, 200)]
    motion = [(frame, 0.01 * (frame % 5)) for frame in range(0, 200)]
    first = (
        secondary_audio_features(envelope, window(0, 200), -20.0),
        video_activity_features(motion, window(0, 200), 0.02, (5, 90), FPS),
    )
    second = (
        secondary_audio_features(envelope, window(0, 200), -20.0),
        video_activity_features(motion, window(0, 200), 0.02, (5, 90), FPS),
    )
    assert first == second


# ---------------------------------------------------------------------------------------
# the decision — question A, then question B
# ---------------------------------------------------------------------------------------


def _features(
    start: int, end: int, *, audio: float = 0.0, video: float = 0.0, **kwargs: bool
) -> GameplayWindowFeatures:
    """A window whose measured activity is stated directly, so a test says what it means."""

    span = window(start, end, **kwargs)
    audio_envelope = [
        (frame, -10.0 if (frame - start) < (end - start) * audio else -60.0)
        for frame in range(start, end)
    ]
    motion_envelope = [
        (frame, 0.10 if (frame - start) < (end - start) * video else 0.001)
        for frame in range(start, end)
    ]
    return GameplayWindowFeatures(
        window=span,
        audio=secondary_audio_features(audio_envelope, span, reference_db=-10.0),
        video=video_activity_features(motion_envelope, span, 0.05, (), FPS),
    )


#: The ablation baseline switched off, so the two measured signals decide on their own.
SIGNALS_ONLY = GameplayPolicySettings(gameplay_by_default=False)


def test_the_candidate_rule_makes_a_would_be_x0_window_gameplay() -> None:
    """The measured default: he stopped talking, so show the game. No signal required."""

    decision = decide_gameplay(_features(1000, 1300), (), FPS)
    assert decision.use_gameplay
    assert decision.reasons == (REASON_NOT_TALKING,)


def test_a_window_with_a_busy_world_becomes_gameplay() -> None:
    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (), FPS, SIGNALS_ONLY)
    assert decision.use_gameplay
    assert REASON_SECONDARY_AUDIO in decision.reasons


def test_a_window_with_a_busy_picture_becomes_gameplay() -> None:
    decision = decide_gameplay(_features(1000, 1300, video=1.0), (), FPS, SIGNALS_ONLY)
    assert decision.use_gameplay
    assert REASON_VISUAL_ACTIVITY in decision.reasons


def test_a_quiet_still_window_stays_x0_and_carries_no_anchors() -> None:
    decision = decide_gameplay(_features(1000, 1300), (), FPS, SIGNALS_ONLY)
    assert not decision.use_gameplay
    assert decision.reason == REASON_NO_SUPPORT
    assert decision.entry_frame is None and decision.exit_frame is None


def test_a_window_shorter_than_the_move_is_refused_before_anything_is_measured() -> None:
    decision = decide_gameplay(_features(1000, 1010, audio=1.0, video=1.0), (), FPS)
    assert not decision.use_gameplay
    assert decision.reason == REASON_TOO_SHORT


def test_the_timeline_tail_stays_x0_by_default() -> None:
    """The longest silence in the reference edit is the outro, and the human left it at X0."""

    features = _features(1000, 2000, audio=1.0, video=1.0, at_timeline_end=True)
    assert not decide_gameplay(features, (), FPS).use_gameplay
    assert decide_gameplay(features, (), FPS).reason == REASON_TAIL
    opted_in = GameplayPolicySettings(gameplay_at_timeline_tail=True)
    assert decide_gameplay(features, (), FPS, opted_in).use_gameplay


@pytest.mark.parametrize("fraction", [0.49, 0.50, 0.51])
def test_the_audio_threshold_is_a_boundary_not_a_cliff_edge(fraction: float) -> None:
    """Exactly at the threshold counts, and the neighbours behave as the comparison says."""

    features = _features(1000, 2000, audio=fraction)
    decision = decide_gameplay(
        features, (), FPS, GameplayPolicySettings(gameplay_by_default=False, use_video=False)
    )
    assert decision.use_gameplay is (features.audio.active_fraction >= 0.5)


def test_turning_a_signal_off_is_how_the_ablation_runs() -> None:
    features = _features(1000, 1300, audio=1.0)
    assert decide_gameplay(features, (), FPS, SIGNALS_ONLY).use_gameplay
    silent = GameplayPolicySettings(
        gameplay_by_default=False, use_secondary_audio=False, use_video=False
    )
    assert not decide_gameplay(features, (), FPS, silent).use_gameplay


def test_a_very_long_silence_is_a_backstop_even_with_no_supporting_signal() -> None:
    settings = GameplayPolicySettings(gameplay_by_default=False, long_silence_ms=1000)
    decision = decide_gameplay(_features(1000, 1300), (), FPS, settings)
    assert decision.use_gameplay
    assert decision.reasons == ("long_creator_silence",)


def test_the_anchors_snap_to_the_nearest_cut_on_each_side() -> None:
    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (996, 1304), FPS)
    assert (decision.entry_anchor, decision.entry_frame) == (1000, 996)
    assert decision.entry_snap == ANCHOR_SNAPPED_BACKWARD
    assert (decision.exit_anchor, decision.exit_frame) == (1300, 1304)
    assert decision.exit_snap == ANCHOR_SNAPPED_FORWARD


def test_with_no_cut_in_the_window_the_anchors_stay_on_the_raw_boundaries() -> None:
    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (500, 1800), FPS)
    assert (decision.entry_frame, decision.exit_frame) == (1000, 1300)
    assert decision.entry_snap == decision.exit_snap == ANCHOR_DIRECT
    assert decision.entry_cut is None and decision.exit_cut is None


def test_a_cut_exactly_on_the_anchor_is_not_called_a_snap() -> None:
    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (1000,), FPS)
    assert decision.entry_frame == 1000
    assert decision.entry_cut == 1000
    assert decision.entry_snap == ANCHOR_DIRECT


def test_a_snap_that_would_invert_the_episode_is_dropped_on_both_sides() -> None:
    """A three-frame window with cuts either side must not produce a negative-length move."""

    settings = GameplayPolicySettings(min_window_ms=0)
    # One cut inside a six-frame window: both anchors are nearest to it, so snapping both
    # would collapse the episode to zero length. Neither snap is taken.
    decision = decide_gameplay(_features(1000, 1006, audio=1.0), (1003,), FPS, settings)
    assert (decision.entry_frame, decision.exit_frame) == (1000, 1006)
    assert decision.entry_snap == decision.exit_snap == ANCHOR_DIRECT


def test_the_decision_cannot_see_which_window_it_is() -> None:
    """Two windows with identical measurements must decide identically (D058)."""

    first = decide_gameplay(_features(1000, 1300, audio=1.0, video=1.0), (), FPS)
    second = decide_gameplay(_features(50000, 50300, audio=1.0, video=1.0), (), FPS)
    assert first.use_gameplay == second.use_gameplay
    assert first.reasons == second.reasons


# ---------------------------------------------------------------------------------------
# the proposal, and where Phase 9a stops
# ---------------------------------------------------------------------------------------


def test_a_proposal_names_the_roles_the_move_would_need() -> None:
    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (), FPS)
    proposal = propose_episode(decision, STATE_X0, STATE_FACE_X1)
    assert proposal is not None
    assert proposal.entry_role == ROLE_X0_TO_GAMEPLAY
    assert proposal.exit_role == ROLE_GAMEPLAY_TO_FACE_X1
    assert (proposal.start_frame, proposal.end_frame) == (1000, 1300)


def test_a_declined_window_produces_no_proposal_at_all() -> None:
    decision = decide_gameplay(_features(1000, 1300), (), FPS, SIGNALS_ONLY)
    assert propose_episode(decision, STATE_X0, STATE_FACE_X1) is None


def test_a_proposal_that_cannot_be_expressed_in_the_graph_raises() -> None:
    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (), FPS)
    with pytest.raises(ForbiddenTransition):
        propose_episode(decision, STATE_X0, "face_x2")


def test_a_proposal_is_not_an_asset_placement() -> None:
    """Phase 9a is a dry run. A proposal must not carry a clip name or a frame count (D061)."""

    decision = decide_gameplay(_features(1000, 1300, audio=1.0), (), FPS)
    proposal = propose_episode(decision, STATE_X0, STATE_FACE_X1)
    assert proposal is not None
    fields = set(proposal.to_dict())
    assert not fields & {"asset_name", "asset_role", "placement_id", "transition_frames"}


def test_the_next_burst_is_the_one_the_creator_may_already_be_in() -> None:
    """An exit four frames after a burst started is +4 from it, not -260 from the next one."""

    states = reference_states(
        [("X0_TO_GAMEPLAY", 100, 300), ("GAMEPLAY_TO_X1", 300, 340), ("X1_TO_X0", 340, 355)],
        ROLE_OF,
        bursts=[FrameRange(296, 350), FrameRange(900, 1000)],
    )
    episode = states.episodes[0]
    assert episode.next_burst_start == 296
    assert episode.frames_until_voice == -4


# ---------------------------------------------------------------------------------------
# Phase 9b: the zoom-utility branch and its two context gates
# ---------------------------------------------------------------------------------------

#: A window whose picture holds a small, steady, central episode, and one whose picture holds
#: nothing at all. Built as features directly: the grids themselves are tested in
#: `tests/test_visual_episodes.py`, and what matters here is what the policy does with them.
ZOOM_WORTHY = VisualEpisodeFeatures(
    samples=10,
    roi_activity=0.02,
    outside_activity=0.001,
    roi_ratio=1.5,
    active_cell_fraction=0.05,
    peak_active_cell_fraction=0.06,
    bbox_area=0.04,
    concentration=0.9,
    regions=1.0,
    persistence_seconds=1.0,
    novelty=0.5,
    onset=1120,
)
NOTHING_VISIBLE = VisualEpisodeFeatures(samples=10)

SPATIAL = GameplayPolicySettings(gameplay_by_default=False, use_zoom_utility=True)


def _visual(
    start: int, end: int, visual: VisualEpisodeFeatures, *, audio: float = 0.0
) -> GameplayWindowFeatures:
    return replace(_features(start, end, audio=audio), visual=visual)


def test_a_zoom_worthy_episode_makes_the_window_gameplay() -> None:
    decision = decide_gameplay(_visual(1000, 1300, ZOOM_WORTHY), (), FPS, SPATIAL)
    assert decision.use_gameplay
    assert decision.reasons == (REASON_ZOOM_WORTHY,)


def test_a_window_with_nothing_visible_stays_x0_and_says_why() -> None:
    """Gaps 1, 11 and 12: audible friends, nothing new on the screen (D067)."""

    decision = decide_gameplay(_visual(1000, 1300, NOTHING_VISIBLE), (), FPS, SPATIAL)
    assert not decision.use_gameplay
    assert decision.reasons[0] == REASON_NOT_ZOOM_WORTHY
    assert ZOOM_NO_EVENT in decision.reasons


def test_secondary_audio_alone_can_never_trigger_a_gameplay_move() -> None:
    """The world talking is context, not a trigger (D067)."""

    loud = _visual(1000, 1300, NOTHING_VISIBLE, audio=1.0)
    assert not decide_gameplay(loud, (), FPS, SPATIAL).use_gameplay


def test_a_long_creator_silence_alone_can_never_trigger_a_gameplay_move() -> None:
    """The silence is the candidate population, not the decision (D067)."""

    long_window = _visual(1000, 3000, NOTHING_VISIBLE)
    settings = replace(SPATIAL, candidate_silence_ms=1000)
    assert not decide_gameplay(long_window, (), FPS, settings).use_gameplay


def test_the_silence_prior_can_only_remove_a_candidate() -> None:
    short = _visual(1000, 1030, ZOOM_WORTHY)
    settings = replace(SPATIAL, candidate_silence_ms=1000)
    refused = decide_gameplay(short, (), FPS, settings)
    assert not refused.use_gameplay
    assert refused.reasons == (REASON_WINDOW_TOO_SHORT_FOR_PRIOR,)
    assert decide_gameplay(short, (), FPS, SPATIAL).use_gameplay


def test_the_audio_gate_can_only_remove_a_candidate() -> None:
    quiet = _visual(1000, 1300, ZOOM_WORTHY)
    settings = replace(SPATIAL, require_secondary_audio=True)
    assert not decide_gameplay(quiet, (), FPS, settings).use_gameplay
    talking = _visual(1000, 1300, ZOOM_WORTHY, audio=1.0)
    assert decide_gameplay(talking, (), FPS, settings).use_gameplay


def test_the_entry_anchor_is_the_window_edge_and_may_snap_to_a_cut() -> None:
    """Question B is unchanged by the spatial branch: whether and where stay apart (D057)."""

    decision = decide_gameplay(_visual(1000, 1300, ZOOM_WORTHY), (1004,), FPS, SPATIAL)
    assert decision.entry_anchor == 1000
    assert decision.entry_frame == 1004
    assert decision.entry_cut == 1004


def test_the_spatial_branch_is_off_by_default_so_phase_9a_is_unchanged() -> None:
    assert not GameplayPolicySettings().use_zoom_utility
    baseline = decide_gameplay(_visual(1000, 1300, NOTHING_VISIBLE), (), FPS)
    assert baseline.use_gameplay
    assert baseline.reasons == (REASON_NOT_TALKING,)
