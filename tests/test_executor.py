"""Safety and exactness tests for the Phase 5 executor, on fakes.

Two properties are under test, and they are the ones that protect the user:

* **when a guard fails, nothing is created** — asserted against the shared mutation log and
  the project's timeline list, not against the report's own opinion of itself;
* **when anything goes wrong after the preview exists, the preview goes away** — and only
  that preview: an older `DAZ_AUTO_PREVIEW_*` is never touched.

The happy path additionally asserts the executor inserted *exactly* the plan: same count,
same order, same frames, `endFrame == duration`.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.plan_validation import build_plan_source, timeline_frame_rate
from davinci_auto_zoom.domain.planner import (
    ROLE_FACECAM_X1,
    ROLE_RESET_X0,
    AssetPlacement,
    AssetTiming,
    PlannerSettings,
    PlanSource,
    ZoomPlan,
)
from davinci_auto_zoom.domain.probe import ApplyPreviewTarget
from davinci_auto_zoom.resolve.executor import (
    PREVIEW_PREFIX,
    ApplyPreviewRefused,
    apply_preview,
    preview_timeline_name,
)
from davinci_auto_zoom.resolve.session import snapshot_project
from tests.fake_resolve import (
    FakeTimeline,
    FakeTimelineItem,
    build_test_project,
    comp_text,
)

CONFIG = Config(asset_timing=AssetTiming(15, 15))

TARGET = ApplyPreviewTarget(
    project="davinci-auto-zoom-test",
    source_timeline="DAZ_INPUT",
    reference_timeline="DAZ_OUTPUT_MVP",
    voice_audio_track=1,
    cut_reference_video_track=1,
    zoom_video_track=3,
    asset_bin="DAVINCI_AUTO_ZOOM",
    assets=(("facecam_x1", "FACE_X1"), ("reset_x0", "FACE_X0_SMOOTH")),
)

#: Two full zoom cycles inside DAZ_INPUT's [216000, 219555) range. The x1 lengths differ on
#: purpose (a variable hold), the x0 lengths are exactly the configured 15 frames.
PLACEMENTS = (
    AssetPlacement(ROLE_FACECAM_X1, FrameRange(216045, 216132), "x1_until_direct_reset"),
    AssetPlacement(ROLE_RESET_X0, FrameRange(216132, 216147), "reset_direct"),
    AssetPlacement(ROLE_FACECAM_X1, FrameRange(216300, 216550), "x1_until_direct_reset"),
    AssetPlacement(ROLE_RESET_X0, FrameRange(216550, 216565), "reset_direct"),
)


def _plan(
    source: PlanSource | None, placements: tuple[AssetPlacement, ...] = PLACEMENTS
) -> ZoomPlan:
    return ZoomPlan(
        timeline=FrameRange(216000, 219555),
        frame_rate=Fraction(60),
        settings=PlannerSettings(),
        timing=AssetTiming(15, 15),
        speech_segments=(),
        bursts=(),
        placements=placements,
        decisions=(),
        source=source,
    )


def _live(config: Config = CONFIG) -> tuple[Any, Any, PlanSource]:
    """A fake project plus the `PlanSource` a plan-probe run against it would have recorded."""

    resolve, project = build_test_project(audio_tracks=3)
    snapshot = snapshot_project(resolve, project, config)
    timeline = snapshot.timeline("DAZ_INPUT")
    assert timeline is not None
    assert config.asset_timing is not None
    source = build_plan_source(
        project=snapshot.project_name,
        timeline=timeline,
        frame_rate=timeline_frame_rate(timeline),
        voice_audio_track=config.voice_audio_track,
        cut_reference_video_track=config.cut_reference_video_track,
        zoom_video_track=config.zoom_video_track,
        assets=config.assets,
        asset_transition_frames=config.asset_timing.to_dict(),
        planner_settings=config.planner,
        found_assets=snapshot.assets,
    )
    return resolve, project, source


def _timeline_names(project: Any) -> list[str]:
    return [
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)
    ]


def _previews(project: Any) -> list[str]:
    return [name for name in _timeline_names(project) if name.startswith(PREVIEW_PREFIX)]


# --- naming ---------------------------------------------------------------------------


def test_the_preview_name_is_unique_to_the_run() -> None:
    from datetime import datetime

    stamp = datetime(2026, 8, 16, 20, 15, 15)
    assert preview_timeline_name(stamp, "abcd1234") == "DAZ_AUTO_PREVIEW_20260816_201515_abcd1234"
    assert preview_timeline_name(stamp) != preview_timeline_name(stamp)


# --- refusals: nothing may be created --------------------------------------------------


def _refuses(project: Any) -> None:
    assert project.mutations == []
    assert _previews(project) == []
    assert _timeline_names(project) == ["DAZ_INPUT", "DAZ_OUTPUT_MVP"]


def test_without_the_confirmation_flag_nothing_is_created() -> None:
    resolve, project, source = _live()
    with pytest.raises(ApplyPreviewRefused):
        apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=False)
    _refuses(project)


def test_a_wrong_expected_project_creates_nothing() -> None:
    resolve, project, source = _live()
    with pytest.raises(ApplyPreviewRefused):
        apply_preview(
            resolve,
            project,
            CONFIG,
            replace(TARGET, project="some-other-project"),
            _plan(source),
            confirmed=True,
        )
    _refuses(project)


def test_a_missing_source_timeline_creates_nothing() -> None:
    resolve, project, source = _live()
    with pytest.raises(ApplyPreviewRefused):
        apply_preview(
            resolve,
            project,
            CONFIG,
            replace(TARGET, source_timeline="DAZ_NOPE"),
            _plan(source),
            confirmed=True,
        )
    _refuses(project)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: replace(s, project="another"), id="project"),
        pytest.param(lambda s: replace(s, timeline="DAZ_OTHER"), id="timeline-name"),
        pytest.param(lambda s: replace(s, timeline_unique_id="uid-other"), id="timeline-uid"),
        pytest.param(lambda s: replace(s, start_frame=216100), id="range-start"),
        pytest.param(lambda s: replace(s, end_frame=219000), id="range-end"),
        pytest.param(lambda s: replace(s, frame_rate="30000/1001"), id="fps"),
        pytest.param(lambda s: replace(s, voice_audio_track=2), id="voice-track"),
        pytest.param(lambda s: replace(s, cut_reference_video_track=2), id="cut-track"),
        pytest.param(lambda s: replace(s, zoom_video_track=4), id="zoom-track"),
        pytest.param(
            lambda s: replace(s, assets=(("facecam_x1", "OTHER"), ("reset_x0", "FACE_X0_SMOOTH"))),
            id="asset-mapping",
        ),
        pytest.param(
            lambda s: replace(
                s, asset_transition_frames=(("facecam_x1", 20), ("reset_x0", 15))
            ),
            id="transition-frames",
        ),
        pytest.param(
            lambda s: replace(s, planner_settings=PlannerSettings(cut_snap_window_ms=800)),
            id="planner-settings",
        ),
        pytest.param(
            lambda s: replace(s, structural_fingerprint="sha256:stale"),
            id="structural-fingerprint",
        ),
        pytest.param(lambda s: None, id="no-source-at-all"),
    ],
)
def test_a_stale_plan_source_creates_nothing(mutate: Any) -> None:
    """Every recorded field is a veto, and a veto happens before the duplicate exists."""

    resolve, project, source = _live()
    with pytest.raises(ApplyPreviewRefused):
        apply_preview(
            resolve, project, CONFIG, TARGET, _plan(mutate(source)), confirmed=True
        )
    _refuses(project)


def test_a_source_recut_after_planning_creates_nothing() -> None:
    """The name, id, range and duration all still match; only a cut on V1 moved."""

    resolve, project, source = _live()
    plan = _plan(source)
    timeline = project.GetTimelineByIndex(1)
    items = timeline.GetItemListInTrack("video", 1)
    items[0] = FakeTimelineItem("source.mov", 216000, 216150, None, 100000)
    items[1] = FakeTimelineItem("source.mov", 216150, 216300, None, 100150)

    with pytest.raises(ApplyPreviewRefused) as excinfo:
        apply_preview(resolve, project, CONFIG, TARGET, plan, confirmed=True)
    assert "fingerprint" in str(excinfo.value)
    _refuses(project)


def test_a_non_empty_target_track_creates_nothing() -> None:
    """The MVP collision policy, end to end: refuse rather than discover what Resolve does."""

    resolve, project, source = _live()
    timeline = project.GetTimelineByIndex(1)
    timeline.AddTrack("video")
    timeline.add_item(
        "video",
        3,
        FakeTimelineItem("someone_elses_clip", 216500, 216600, track=("video", 3)),
    )
    project.mutations.clear()
    # Re-derive the source, so the *only* reason to refuse is the occupied track.
    _, _, _ = _live()
    snapshot = snapshot_project(resolve, project, CONFIG)
    fresh = snapshot.timeline("DAZ_INPUT")
    assert fresh is not None
    assert CONFIG.asset_timing is not None
    source = build_plan_source(
        project=snapshot.project_name,
        timeline=fresh,
        frame_rate=timeline_frame_rate(fresh),
        voice_audio_track=CONFIG.voice_audio_track,
        cut_reference_video_track=CONFIG.cut_reference_video_track,
        zoom_video_track=CONFIG.zoom_video_track,
        assets=CONFIG.assets,
        asset_transition_frames=CONFIG.asset_timing.to_dict(),
        planner_settings=CONFIG.planner,
        found_assets=snapshot.assets,
    )

    with pytest.raises(ApplyPreviewRefused) as excinfo:
        apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)
    assert "already holds" in str(excinfo.value)
    assert not any("AppendToTimeline" in call for call in project.mutations)
    assert _previews(project) == []


def test_an_empty_plan_creates_no_preview_and_still_succeeds() -> None:
    resolve, project, source = _live()
    report = apply_preview(
        resolve, project, CONFIG, TARGET, _plan(source, ()), confirmed=True
    )
    assert report.nothing_to_apply is True
    assert report.succeeded is True
    assert project.mutations == []
    assert _previews(project) == []


# --- the happy path -------------------------------------------------------------------


def test_a_valid_plan_lands_exactly_on_a_kept_preview_timeline() -> None:
    resolve, project, source = _live()
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)

    assert report.succeeded is True
    assert report.verified is True
    assert report.verification_differences == ()
    assert report.audit_differences == ()
    assert len(report.insertions) == len(PLACEMENTS)
    assert all(record.ok for record in report.insertions)

    # The preview is kept, and it is the only new timeline.
    assert report.preview_kept is True
    assert report.preview_deleted is None
    assert _previews(project) == [report.preview_name]
    assert report.preview_name is not None
    assert report.preview_name.startswith(PREVIEW_PREFIX)
    assert report.preview_unique_id is not None
    assert "PREVIEW READY FOR HUMAN VISUAL REVIEW" in report.to_text()


def test_every_append_call_is_exactly_the_placement() -> None:
    resolve, project, source = _live()
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)

    sent = [record.clip_info for record in report.insertions]
    assert sent == [
        {
            "mediaPoolItem": "FACE_X1",
            "startFrame": 0,
            "endFrame": 87,
            "trackIndex": 3,
            "recordFrame": 216045,
        },
        {
            "mediaPoolItem": "FACE_X0_SMOOTH",
            "startFrame": 0,
            "endFrame": 15,
            "trackIndex": 3,
            "recordFrame": 216132,
        },
        {
            "mediaPoolItem": "FACE_X1",
            "startFrame": 0,
            "endFrame": 250,
            "trackIndex": 3,
            "recordFrame": 216300,
        },
        {
            "mediaPoolItem": "FACE_X0_SMOOTH",
            "startFrame": 0,
            "endFrame": 15,
            "trackIndex": 3,
            "recordFrame": 216550,
        },
    ]
    # x0 is exactly the configured animation length, never the 42-frame native asset.
    assert [record.duration for record in report.insertions] == [87, 15, 250, 15]


def test_the_target_track_is_created_and_the_other_tracks_are_left_alone() -> None:
    resolve, project, source = _live()
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)
    assert report.tracks_added == 1

    preview = next(
        project.GetTimelineByIndex(index)
        for index in range(1, project.GetTimelineCount() + 1)
        if project.GetTimelineByIndex(index).GetName() == report.preview_name
    )
    assert preview.GetTrackCount("video") == 3
    assert len(preview.GetItemListInTrack("video", 3)) == len(PLACEMENTS)
    # V1/V2 are byte-for-structure what DAZ_INPUT has.
    source_timeline = project.GetTimelineByIndex(1)
    for index in (1, 2):
        assert [
            (item.GetName(), item.GetStart(), item.GetEnd())
            for item in preview.GetItemListInTrack("video", index)
        ] == [
            (item.GetName(), item.GetStart(), item.GetEnd())
            for item in source_timeline.GetItemListInTrack("video", index)
        ]
    # No audio track was touched on the preview.
    assert preview.GetTrackCount("audio") == source_timeline.GetTrackCount("audio")


def test_the_originals_are_untouched_and_the_active_timeline_is_restored() -> None:
    resolve, project, source = _live()
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)

    assert report.previous_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.audit_checked == ("DAZ_INPUT", "DAZ_OUTPUT_MVP", "assets")
    assert project.GetTimelineByIndex(1).GetTrackCount("video") == 2  # no V3 on DAZ_INPUT
    assert len(project.GetTimelineByIndex(2).GetItemListInTrack("video", 3)) == 3


def test_no_project_save_is_ever_forced() -> None:
    resolve, project, source = _live()
    apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)
    assert not any("SaveProject" in call for call in project.mutations)


# --- failures after the preview exists -------------------------------------------------


def _old_preview(project: Any) -> FakeTimeline:
    """A preview left over from an earlier run. It must survive everything."""

    old = FakeTimeline(
        f"{PREVIEW_PREFIX}20260101_010101_deadbeef",
        {("video", 1): ("Video 1", "", [])},
        log=project.mutations,
    )
    project.add_timeline(old)
    return old


def _run_failing(break_append: Any) -> tuple[Any, Any]:
    resolve, project, source = _live()
    _old_preview(project)
    project.GetMediaPool().AppendToTimeline = break_append  # type: ignore[method-assign]
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)
    return report, project


def _assert_rolled_back(report: Any, project: Any) -> None:
    assert report.succeeded is False
    assert report.error is not None
    assert report.preview_kept is False
    assert report.preview_deleted is True
    assert report.preview_absent_after_rollback is True
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.audit_differences == ()
    # The failed run's preview is gone; the older one was never touched.
    assert _previews(project) == [f"{PREVIEW_PREFIX}20260101_010101_deadbeef"]


def test_an_insertion_returning_nothing_rolls_the_whole_preview_back() -> None:
    report, project = _run_failing(lambda clip_infos: [])
    _assert_rolled_back(report, project)


def test_an_insertion_returning_two_items_rolls_back() -> None:
    def two(clip_infos: list[dict[str, Any]]) -> list[FakeTimelineItem]:
        return [
            FakeTimelineItem("FACE_X1", 216045, 216132, fusion_comp_count=1, track=("video", 3)),
            FakeTimelineItem("FACE_X1", 216045, 216132, fusion_comp_count=1, track=("video", 3)),
        ]

    report, project = _run_failing(two)
    assert report.insertions[0].returned_items == 2
    _assert_rolled_back(report, project)


@pytest.mark.parametrize(
    ("start", "end", "name", "track", "comps"),
    [
        pytest.param(216046, 216132, "FACE_X1", 3, 1, id="wrong-start"),
        pytest.param(216045, 216131, "FACE_X1", 3, 1, id="wrong-end"),
        pytest.param(216045, 216132, "WRONG_ASSET", 3, 1, id="wrong-name"),
        pytest.param(216045, 216132, "FACE_X1", 2, 1, id="wrong-track"),
        pytest.param(216045, 216132, "FACE_X1", 3, 0, id="no-fusion-comp"),
    ],
)
def test_a_misplaced_insertion_rolls_back(
    start: int, end: int, name: str, track: int, comps: int
) -> None:
    def wrong(clip_infos: list[dict[str, Any]]) -> list[FakeTimelineItem]:
        return [
            FakeTimelineItem(
                name,
                start,
                end,
                fusion_comp_count=comps,
                track=("video", track),
                comp=comp_text(name, end - start),
            )
        ]

    report, project = _run_failing(wrong)
    assert report.insertions[0].problems
    _assert_rolled_back(report, project)


def test_an_exception_at_placement_n_rolls_the_earlier_ones_back_too() -> None:
    resolve, project, source = _live()
    _old_preview(project)
    media_pool = project.GetMediaPool()
    original = media_pool.AppendToTimeline
    calls = {"n": 0}

    def explode(clip_infos: list[dict[str, Any]]) -> list[FakeTimelineItem]:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("Resolve went away")
        return original(clip_infos)

    media_pool.AppendToTimeline = explode  # type: ignore[method-assign]
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)

    assert calls["n"] == 3
    assert [record.ok for record in report.insertions] == [True, True, False]
    _assert_rolled_back(report, project)


def test_a_track_that_cannot_be_created_rolls_back_before_any_insertion() -> None:
    resolve, project, source = _live()
    _old_preview(project)
    original_duplicate = project.GetTimelineByIndex(1).DuplicateTimeline

    def duplicate(name: str) -> FakeTimeline:
        preview = original_duplicate(name)
        preview.AddTrack = lambda *args, **kwargs: False  # type: ignore[method-assign]
        return preview

    project.GetTimelineByIndex(1).DuplicateTimeline = duplicate  # type: ignore[method-assign]
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)

    assert report.insertions == []
    assert not any("AppendToTimeline" in call for call in project.mutations)
    _assert_rolled_back(report, project)
