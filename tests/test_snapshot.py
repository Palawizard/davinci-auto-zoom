import json

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.compare import added_generator_items, alternation, compare_timelines
from davinci_auto_zoom.domain.snapshot import (
    ITEM_KIND_LIKELY_GENERATOR,
    ITEM_KIND_MEDIA,
    TimelineItemSnapshot,
    TimelineSnapshot,
    TrackSnapshot,
    ms_to_frames,
)
from davinci_auto_zoom.resolve.session import runtime_method_names, snapshot_project
from tests.fake_resolve import FakeFolder, FakeMediaPool, build_test_project


def _snapshot():  # type: ignore[no-untyped-def]
    resolve, project = build_test_project()
    return snapshot_project(resolve, project, Config())


def test_snapshot_reads_project_and_timelines() -> None:
    snapshot = _snapshot()
    assert snapshot.product_name == "DaVinci Resolve Studio"
    assert [t.name for t in snapshot.timelines] == ["DAZ_INPUT", "DAZ_OUTPUT_MVP"]
    assert snapshot.current_timeline == "DAZ_OUTPUT_MVP"


def test_track_enable_state_is_unknown_for_non_current_timelines() -> None:
    snapshot = _snapshot()
    assert snapshot.timeline("DAZ_INPUT").track("video", 1).enabled is None  # type: ignore[union-attr]
    assert snapshot.timeline("DAZ_OUTPUT_MVP").track("video", 1).enabled is True  # type: ignore[union-attr]


def test_generator_items_are_distinguished_from_media_items() -> None:
    generator = TimelineItemSnapshot("FACE_X1", 100, 200, fusion_comp_count=1)
    media = TimelineItemSnapshot(
        "clip.mov", 100, 200, media_pool_item_name="clip.mov", source_start_frame=5
    )
    assert generator.probable_kind == ITEM_KIND_LIKELY_GENERATOR
    assert media.probable_kind == ITEM_KIND_MEDIA
    assert generator.duration == 100


def test_edit_boundaries_are_deduplicated_and_sorted() -> None:
    timeline = _snapshot().timeline("DAZ_INPUT")
    assert timeline is not None
    assert timeline.edit_boundaries() == (216000, 216132, 216300, 216500)
    assert timeline.edit_boundaries(video_track_index=9) == ()


def test_assets_are_discovered_and_mapped_to_roles() -> None:
    snapshot = _snapshot()
    assert snapshot.asset_bin_found is True
    roles = {a.name: (a.role, a.clip_type, a.frames) for a in snapshot.assets}
    assert roles == {
        "FACE_X1": ("facecam_x1", "Generator", 132),
        "FACE_X0_SMOOTH": ("reset_x0", "Generator", 42),
    }
    assert snapshot.warnings == ()


def test_missing_asset_produces_a_warning_not_an_exception() -> None:
    resolve, project = build_test_project()
    config = Config(assets={"facecam_x1": "MISSING_ASSET"})
    snapshot = snapshot_project(resolve, project, config)
    assert any("MISSING_ASSET" in warning for warning in snapshot.warnings)


def test_snapshot_is_json_serializable_without_proxy_objects() -> None:
    json.dumps(_snapshot().to_dict())


def test_compare_reports_reference_only_track_and_added_items() -> None:
    snapshot = _snapshot()
    comparison = compare_timelines(
        snapshot.timeline("DAZ_INPUT"),  # type: ignore[arg-type]
        snapshot.timeline("DAZ_OUTPUT_MVP"),  # type: ignore[arg-type]
    )
    assert comparison.reference_only_tracks == ("video3",)
    assert comparison.input_only_tracks == ()
    added = added_generator_items(comparison)
    assert alternation(added) == ("FACE_X1", "FACE_X0_SMOOTH", "FACE_X1")
    assert [i.duration for i in added] == [87, 42, 50]


def test_compare_measures_distance_to_cuts() -> None:
    snapshot = _snapshot()
    comparison = compare_timelines(
        snapshot.timeline("DAZ_INPUT"),  # type: ignore[arg-type]
        snapshot.timeline("DAZ_OUTPUT_MVP"),  # type: ignore[arg-type]
    )
    first = added_generator_items(comparison)[0]
    assert first.start_offset_to_nearest_cut == 45  # 216045 vs cut at 216000
    assert first.end_offset_to_nearest_cut == 0  # 216132 is a cut
    assert comparison.ends_on_cut == 1
    assert first.gap_from_previous is None


def test_ms_to_frames_rounds_to_integer_frames() -> None:
    assert ms_to_frames(1000, 60.0) == 60
    assert ms_to_frames(350, 59.94) == 21


def test_runtime_sample_finds_a_media_pool_item_nested_in_a_bin() -> None:
    """The capability sample must not depend on a clip sitting in the Media Pool root."""

    resolve, project = build_test_project()
    root = project.GetMediaPool().GetRootFolder()
    # A realistic project keeps its clips in bins, leaving the root folder empty.
    empty_root = FakeFolder("Master", [], root.GetSubFolderList())
    project._media_pool = FakeMediaPool(empty_root)  # noqa: SLF001

    runtime = runtime_method_names(resolve, project)
    assert "GetClipProperty" in runtime["MediaPoolItem"]


def test_compare_handles_a_reference_track_with_no_cuts_to_measure_against() -> None:
    """Offsets are None when the cut source track is empty; nothing may crash on that."""

    empty = TimelineSnapshot(
        name="A",
        unique_id="a",
        frame_rate=60.0,
        start_frame=0,
        end_frame=100,
        start_timecode="00:00:00:00",
        is_current=False,
        tracks=(TrackSnapshot("video", 1, "Video 1"),),
    )
    reference = TimelineSnapshot(
        name="B",
        unique_id="b",
        frame_rate=60.0,
        start_frame=0,
        end_frame=100,
        start_timecode="00:00:00:00",
        is_current=False,
        tracks=(
            TrackSnapshot("video", 1, "Video 1"),
            TrackSnapshot(
                "video", 2, "Video 2", items=(TimelineItemSnapshot("FACE_X1", 10, 50),)
            ),
        ),
    )
    added = added_generator_items(compare_timelines(empty, reference))
    assert added[0].start_offset_to_nearest_cut is None
    assert added[0].end_offset_to_nearest_cut is None


def test_hard_cuts_need_a_clip_on_both_sides() -> None:
    """Entering from black or running out into a gap is not a cut (Phase 4)."""

    timeline = _snapshot().timeline("DAZ_INPUT")
    assert timeline is not None
    # V1 holds three contiguous clips spanning [216000, 216500).
    assert timeline.hard_cuts() == (216132, 216300)
    # The head of the first clip and the tail of the last one are boundaries, not cuts.
    assert timeline.edit_boundaries() == (216000, 216132, 216300, 216500)
    assert timeline.hard_cuts(video_track_index=9) == ()
