"""The facecam golden: one synthetic timeline, one expected plan, written out in full.

`tests/test_planner.py` tests each rule in isolation, which is what you want when a rule
changes. This file tests the opposite thing: that the *combination* of every rule the creator
watched and validated still produces the same edit. It is deliberately one small scenario with
its entire answer typed out, so a diff on it is readable by a human rather than a hash nobody
can interpret.

The inputs are synthetic and versioned here — no footage, no audio, nothing of the user's, by
the repository's media rule. What they encode is the shape of the validated behaviour:

    burst 1   two speech regions bridged by a sub-gate pause, one voice valley -> FACE_X2,
              a second valley -> FACE_X3, entry snapped onto a nearby hard cut
    burst 2   one speech region, one valley -> FACE_X2, reset snapped backward onto a cut
    burst 3   a short burst with no valley at all: entry, hold, direct reset

Change any facecam rule and this file fails. That is the point: if the failure is intended,
the new table has to be typed out and read, which is the review this behaviour is owed.
"""

from fractions import Fraction

from davinci_auto_zoom.domain.dynamics import EnergyEnvelope, EnergyPoint, EnergySettings
from davinci_auto_zoom.domain.models import FrameRange, SpeechSegment
from davinci_auto_zoom.domain.planner import AssetTiming, PlannerSettings, plan_zooms
from davinci_auto_zoom.domain.transitions import (
    ROLE_FACE_X1_TO_FACE_X2,
    ROLE_FACE_X1_TO_X0,
    ROLE_FACE_X2_TO_FACE_X3,
    ROLE_FACE_X2_TO_X0,
    ROLE_FACE_X3_TO_X0,
    ROLE_X0_TO_FACE_X1,
)

FPS = Fraction(60)
TIMELINE = FrameRange(1000, 4000)

#: This user's bin: every transition animates in 15 frames (see config.example.toml).
TIMING = AssetTiming(
    {
        ROLE_X0_TO_FACE_X1: 15,
        ROLE_FACE_X1_TO_FACE_X2: 15,
        ROLE_FACE_X2_TO_FACE_X3: 15,
        ROLE_FACE_X1_TO_X0: 15,
        ROLE_FACE_X2_TO_X0: 15,
        ROLE_FACE_X3_TO_X0: 15,
    }
)

#: The validated settings, stated rather than defaulted: this file must fail if a default
#: moves, not silently follow it.
SETTINGS = PlannerSettings(
    reset_after_silence_ms=650,
    zoom_lead_in_ms=0,
    zoom_lead_out_ms=0,
    cut_snap_window_ms=350,
    cut_snap_lookback_ms=120,
    zoom_cut_snap_window_ms=120,
    zoom_cut_snap_lookback_ms=120,
    promotion_min_drop_db=20,
    promotion_recovery_within_db=6,
    promotion_min_valley_ms=30,
    promotion_max_valley_ms=650,
    promotion_min_hold_ms=400,
)

SPEAKING_DB = -18.0
QUIET_DB = -55.0

#: 650 ms at 60 fps = 39 frames. Burst 1's internal pause is below it, burst boundaries above.
SPEECH: tuple[tuple[int, int], ...] = (
    (1200, 1500),
    (1520, 1900),  # 20-frame gap: bridged into burst 1
    (2100, 2700),  # 200-frame gap: burst 2
    (3000, 3120),  # 300-frame gap: burst 3, too short to earn anything
)

#: Voice valleys, as (start, end) frames of near-silence inside a burst. 3 frames = 50 ms,
#: comfortably over promotion_min_valley_ms and under promotion_max_valley_ms.
VALLEYS: tuple[tuple[int, int], ...] = ((1350, 1353), (1700, 1703), (2300, 2303))

#: Hard cuts on the reference video track. 1198 sits 2 frames before burst 1's start (inside
#: the 7-frame zoom-in lookback) and 2702 sits 2 frames after burst 2's end.
CUTS: tuple[int, ...] = (1198, 2702, 3500)

#: The whole expected edit, as (role, start, end). Read it as three cycles.
GOLDEN: tuple[tuple[str, int, int], ...] = (
    # burst 1 — entry snapped back onto the cut at 1198, then two promotions, then the reset.
    (ROLE_X0_TO_FACE_X1, 1198, 1353),
    (ROLE_FACE_X1_TO_FACE_X2, 1353, 1703),
    (ROLE_FACE_X2_TO_FACE_X3, 1703, 1900),
    (ROLE_FACE_X3_TO_X0, 1900, 1915),
    # burst 2 — direct entry, one promotion, reset snapped forward onto the cut at 2702.
    (ROLE_X0_TO_FACE_X1, 2100, 2303),
    (ROLE_FACE_X1_TO_FACE_X2, 2303, 2702),
    (ROLE_FACE_X2_TO_X0, 2702, 2717),
    # burst 3 — no valley, so no promotion: the plain two-clip cycle.
    (ROLE_X0_TO_FACE_X1, 3000, 3120),
    (ROLE_FACE_X1_TO_X0, 3120, 3135),
)


def _envelope() -> EnergyEnvelope:
    """A constant speaking level over the timeline with `VALLEYS` punched out of it."""

    settings = EnergySettings()
    step = Fraction(settings.hop_ms, 1000) * FPS
    points: list[EnergyPoint] = []
    index = 0
    while True:
        frame = TIMELINE.start + int(index * step)
        if frame >= TIMELINE.end:
            break
        quiet = any(start <= frame < end for start, end in VALLEYS)
        points.append(EnergyPoint(frame, QUIET_DB if quiet else SPEAKING_DB))
        index += 1
    return EnergyEnvelope(tuple(points), settings)


def _plan():
    return plan_zooms(
        timeline=TIMELINE,
        frame_rate=FPS,
        speech_segments=[SpeechSegment(FrameRange(a, b)) for a, b in SPEECH],
        timing=TIMING,
        hard_cuts=CUTS,
        settings=SETTINGS,
        energy=_envelope(),
    )


def test_the_validated_facecam_edit_is_reproduced_exactly() -> None:
    result = _plan()
    assert [(p.asset_role, p.start_frame, p.end_frame) for p in result.placements] == list(
        GOLDEN
    )


def test_the_bursts_are_grouped_by_the_gate_not_by_a_delay() -> None:
    """The 20-frame pause inside burst 1 is bridged; the 200/300-frame ones are not."""

    result = _plan()
    assert [(b.start, b.end) for b in result.bursts] == [
        (1200, 1900),
        (2100, 2700),
        (3000, 3120),
    ]
    assert result.merged_gaps == 1


def test_the_ladder_is_climbed_one_rung_at_a_time_per_burst() -> None:
    result = _plan()
    per_burst: dict[int, list[str]] = {}
    for placement in result.placements:
        per_burst.setdefault(placement.burst_index, []).append(placement.asset_role)
    assert per_burst == {
        0: [
            ROLE_X0_TO_FACE_X1,
            ROLE_FACE_X1_TO_FACE_X2,
            ROLE_FACE_X2_TO_FACE_X3,
            ROLE_FACE_X3_TO_X0,
        ],
        1: [ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_FACE_X2, ROLE_FACE_X2_TO_X0],
        2: [ROLE_X0_TO_FACE_X1, ROLE_FACE_X1_TO_X0],
    }


def test_every_cycle_ends_on_the_reset_matching_the_level_it_reached() -> None:
    result = _plan()
    assert [p.asset_role for p in result.reset_placements] == [
        ROLE_FACE_X3_TO_X0,
        ROLE_FACE_X2_TO_X0,
        ROLE_FACE_X1_TO_X0,
    ]


def test_the_plan_is_ordered_gapless_within_a_cycle_and_free_of_overlaps() -> None:
    result = _plan()
    assert result.valid
    assert result.overlaps == ()
    starts = [p.start_frame for p in result.placements]
    assert starts == sorted(starts)
    for placement in result.placements:
        assert placement.end_frame > placement.start_frame
        assert TIMELINE.start <= placement.start_frame
        assert placement.end_frame <= TIMELINE.end
    # Inside a burst the clips are strictly adjacent: the picture is never left unowned
    # between two transitions of the same cycle.
    for burst_index in range(len(result.bursts)):
        cycle = [p for p in result.placements if p.burst_index == burst_index]
        for previous, following in zip(cycle, cycle[1:], strict=False):
            assert previous.end_frame == following.start_frame


def test_a_cut_never_creates_a_transition() -> None:
    """The cut at 3500 sits in no burst, so nothing is planned anywhere near it."""

    result = _plan()
    assert not [p for p in result.placements if p.start_frame > 3200]


def test_removing_every_cut_moves_transitions_back_onto_their_audio_anchors() -> None:
    """Snapping is an adjustment to an anchor, never the reason a transition exists."""

    without_cuts = plan_zooms(
        timeline=TIMELINE,
        frame_rate=FPS,
        speech_segments=[SpeechSegment(FrameRange(a, b)) for a, b in SPEECH],
        timing=TIMING,
        hard_cuts=(),
        settings=SETTINGS,
        energy=_envelope(),
    )
    assert [
        (p.asset_role, p.start_frame, p.end_frame) for p in without_cuts.placements
    ] == [
        (ROLE_X0_TO_FACE_X1, 1200, 1353),
        (ROLE_FACE_X1_TO_FACE_X2, 1353, 1703),
        (ROLE_FACE_X2_TO_FACE_X3, 1703, 1900),
        (ROLE_FACE_X3_TO_X0, 1900, 1915),
        (ROLE_X0_TO_FACE_X1, 2100, 2303),
        (ROLE_FACE_X1_TO_FACE_X2, 2303, 2700),
        (ROLE_FACE_X2_TO_X0, 2700, 2715),
        (ROLE_X0_TO_FACE_X1, 3000, 3120),
        (ROLE_FACE_X1_TO_X0, 3120, 3135),
    ]
    assert len(without_cuts.placements) == len(GOLDEN)


def test_the_same_inputs_always_produce_the_same_plan() -> None:
    assert _plan().to_dict() == _plan().to_dict()
