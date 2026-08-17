"""Pure Phase 5 decisions: which target track is usable, and did the track become the plan."""

from __future__ import annotations

from fractions import Fraction

from davinci_auto_zoom.domain.apply import (
    ExpectedItem,
    clip_info_for,
    expected_items,
    insertion_differences,
    placement_differences,
    plan_target_track,
)
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.planner import (
    AssetPlacement,
    AssetTiming,
    PlannerSettings,
    ZoomPlan,
)
from davinci_auto_zoom.domain.snapshot import (
    TimelineItemSnapshot,
    TimelineSnapshot,
    TrackSnapshot,
)
from davinci_auto_zoom.domain.transitions import (
    ROLE_FACE_X1_TO_X0,
    ROLE_X0_TO_FACE_X1,
)

ASSETS = {ROLE_X0_TO_FACE_X1: "FACE_X1", ROLE_FACE_X1_TO_X0: "X1_TO_X0"}


def _timeline(tracks: tuple[TrackSnapshot, ...]) -> TimelineSnapshot:
    return TimelineSnapshot(
        name="DAZ_PREVIEW",
        unique_id="uid-preview",
        frame_rate=60.0,
        start_frame=216000,
        end_frame=219555,
        start_timecode="01:00:00:00",
        is_current=False,
        tracks=tracks,
    )


def _video(index: int, items: tuple[TimelineItemSnapshot, ...] = ()) -> TrackSnapshot:
    return TrackSnapshot("video", index, f"Video {index}", items=items)


def _plan(*placements: AssetPlacement) -> ZoomPlan:
    return ZoomPlan(
        timeline=FrameRange(216000, 219555),
        frame_rate=Fraction(60),
        settings=PlannerSettings(),
        timing=AssetTiming({"x0_to_face_x1": 15, "face_x1_to_x0": 15}),
        speech_segments=(),
        bursts=(),
        placements=placements,
        decisions=(),
    )


def _x1(start: int, end: int) -> AssetPlacement:
    return AssetPlacement(ROLE_X0_TO_FACE_X1, FrameRange(start, end), "x1_until_direct_reset")


def _x0(start: int) -> AssetPlacement:
    return AssetPlacement(ROLE_FACE_X1_TO_X0, FrameRange(start, start + 15), "reset_direct")


def _item(name: str, start: int, end: int, comps: int = 1) -> TimelineItemSnapshot:
    return TimelineItemSnapshot(name=name, start=start, end=end, fusion_comp_count=comps)


# --- target track ---------------------------------------------------------------------


def test_a_missing_target_track_is_created() -> None:
    preparation = plan_target_track(_timeline((_video(1), _video(2))), 3)
    assert preparation.usable
    assert preparation.tracks_to_add == 1


def test_several_missing_tracks_are_all_counted() -> None:
    preparation = plan_target_track(_timeline((_video(1),)), 4)
    assert preparation.usable
    assert preparation.tracks_to_add == 3


def test_an_existing_empty_target_track_is_accepted_as_is() -> None:
    preparation = plan_target_track(_timeline((_video(1), _video(2), _video(3))), 3)
    assert preparation.usable
    assert preparation.tracks_to_add == 0


def test_a_non_empty_target_track_is_refused() -> None:
    """MVP collision policy: DAZ writes to an empty dedicated track or not at all."""

    occupied = _video(3, (_item("someone_elses_title", 216500, 216600),))
    preparation = plan_target_track(_timeline((_video(1), _video(2), occupied)), 3)
    assert not preparation.usable
    assert "already holds 1 item(s)" in preparation.blockers[0]


def test_a_target_track_holding_previous_daz_zooms_is_refused_too() -> None:
    """Recognising and replacing our own output is Phase 6 work, not a licence to overwrite."""

    occupied = _video(
        3, (_item("FACE_X1", 216045, 216132), _item("X1_TO_X0", 216132, 216147))
    )
    preparation = plan_target_track(_timeline((_video(1), _video(2), occupied)), 3)
    assert not preparation.usable


def test_a_nonsense_track_index_is_refused() -> None:
    assert not plan_target_track(_timeline((_video(1),)), 0).usable


# --- the insertion arguments ----------------------------------------------------------


def test_the_clip_info_is_exactly_the_placement() -> None:
    sentinel = object()
    info = clip_info_for(_x1(216045, 216138), sentinel, 3)
    assert info == {
        "mediaPoolItem": sentinel,
        "startFrame": 0,
        "endFrame": 93,
        "trackIndex": 3,
        "recordFrame": 216045,
    }


def test_end_frame_is_the_duration_not_the_duration_minus_one() -> None:
    """`endFrame` is exclusive (D013): a 15-frame reset asks for 15, never 14."""

    info = clip_info_for(_x0(216132), object(), 3)
    assert info["endFrame"] == 15
    assert info["endFrame"] - info["startFrame"] == 15


def test_the_x1_duration_is_the_planner_s_variable_one() -> None:
    for duration in (30, 93, 250):
        info = clip_info_for(_x1(216000, 216000 + duration), object(), 3)
        assert info["endFrame"] == duration


def test_expected_items_restate_the_plan_without_deciding_anything() -> None:
    plan = _plan(_x1(216045, 216132), _x0(216132))
    assert expected_items(plan, ASSETS, 3) == (
        ExpectedItem(ROLE_X0_TO_FACE_X1, "FACE_X1", 216045, 216132, 3),
        ExpectedItem(ROLE_FACE_X1_TO_X0, "X1_TO_X0", 216132, 216147, 3),
    )


# --- verifying one insertion ----------------------------------------------------------


def _facts(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "name": "FACE_X1",
        "track_index": 3,
        "start": 216045,
        "end": 216132,
        "duration": 87,
        "fusion_comp_count": 1,
    }
    facts.update(overrides)
    return facts


EXPECTED = ExpectedItem(ROLE_X0_TO_FACE_X1, "FACE_X1", 216045, 216132, 3)


def test_a_correct_insertion_has_no_differences() -> None:
    assert insertion_differences(EXPECTED, _facts()) == ()


def test_a_wrong_name_start_end_duration_or_track_is_caught() -> None:
    for override in (
        {"name": "SOMETHING_ELSE"},
        {"start": 216044},
        {"end": 216133},
        {"duration": 86},
        {"track_index": 2},
    ):
        assert insertion_differences(EXPECTED, _facts(**override)) != (), override


def test_an_instance_without_a_fusion_composition_is_caught() -> None:
    problems = insertion_differences(EXPECTED, _facts(fusion_comp_count=0))
    assert any("Fusion" in problem for problem in problems)


# --- verifying the finished track -----------------------------------------------------


def test_a_track_that_equals_the_plan_has_no_differences() -> None:
    expected = expected_items(_plan(_x1(216045, 216132), _x0(216132)), ASSETS, 3)
    actual = (_item("FACE_X1", 216045, 216132), _item("X1_TO_X0", 216132, 216147))
    assert placement_differences(expected, actual) == ()


def test_a_missing_or_extra_item_is_caught() -> None:
    expected = expected_items(_plan(_x1(216045, 216132), _x0(216132)), ASSETS, 3)
    assert placement_differences(expected, (_item("FACE_X1", 216045, 216132),)) != ()
    assert placement_differences(
        expected,
        (
            _item("FACE_X1", 216045, 216132),
            _item("X1_TO_X0", 216132, 216147),
            _item("FACE_X1", 216200, 216300),
        ),
    ) != ()


def test_a_single_frame_of_drift_is_caught() -> None:
    expected = expected_items(_plan(_x1(216045, 216132)), ASSETS, 3)
    assert placement_differences(expected, (_item("FACE_X1", 216046, 216132),)) != ()
    assert placement_differences(expected, (_item("FACE_X1", 216045, 216131),)) != ()


def test_items_in_the_wrong_order_are_caught() -> None:
    expected = expected_items(_plan(_x1(216045, 216132), _x0(216132)), ASSETS, 3)
    swapped = (_item("X1_TO_X0", 216132, 216147), _item("FACE_X1", 216045, 216132))
    assert placement_differences(expected, swapped) != ()


def test_overlapping_items_are_caught_even_when_each_one_matches() -> None:
    expected = (
        ExpectedItem(ROLE_X0_TO_FACE_X1, "FACE_X1", 216045, 216132, 3),
        ExpectedItem(ROLE_FACE_X1_TO_X0, "X1_TO_X0", 216100, 216115, 3),
    )
    actual = (_item("FACE_X1", 216045, 216132), _item("X1_TO_X0", 216100, 216115))
    problems = placement_differences(expected, actual)
    assert any("overlap" in problem for problem in problems)


def test_a_placed_item_without_a_fusion_composition_fails_verification() -> None:
    expected = expected_items(_plan(_x1(216045, 216132)), ASSETS, 3)
    assert placement_differences(expected, (_item("FACE_X1", 216045, 216132, comps=0),)) != ()
