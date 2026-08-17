"""Every recorded `PlanSource` field must be re-checked, and any difference must refuse.

These are pure tests of the comparison itself. The executor-side property — a mismatch means
zero mutations — is asserted in `tests/test_executor.py` against the mutation log.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from davinci_auto_zoom.domain.dynamics import EnergySettings
from davinci_auto_zoom.domain.plan_validation import (
    build_plan_source,
    plan_source_mismatches,
    timeline_frame_rate,
    validate_plan_source,
)
from davinci_auto_zoom.domain.planner import AssetIdentity, PlannerSettings, PlanSource
from davinci_auto_zoom.domain.snapshot import (
    AssetSnapshot,
    TimelineItemSnapshot,
    TimelineSnapshot,
    TrackSnapshot,
)

ASSETS = {"x0_to_face_x1": "FACE_X1", "face_x1_to_x0": "X1_TO_X0"}
TRANSITIONS = {"x0_to_face_x1": 15, "face_x1_to_x0": 15}
FOUND = (
    AssetSnapshot("FACE_X1", "Master/DAZ", "Generator", 132, "media-x1", "uid-x1", "x0_to_face_x1"),
    AssetSnapshot(
        "X1_TO_X0", "Master/DAZ", "Generator", 42, "media-x0", "uid-x0", "face_x1_to_x0"
    ),
)


def _timeline(**overrides: object) -> TimelineSnapshot:
    base = TimelineSnapshot(
        name="DAZ_INPUT",
        unique_id="uid-DAZ_INPUT",
        frame_rate=60.0,
        start_frame=216000,
        end_frame=219555,
        start_timecode="01:00:00:00",
        is_current=False,
        tracks=(
            TrackSnapshot(
                "video",
                1,
                "Video 1",
                items=(
                    TimelineItemSnapshot("a.mov", 216000, 216132, "uid-a"),
                    TimelineItemSnapshot("b.mov", 216132, 216300, "uid-b"),
                ),
            ),
            TrackSnapshot("video", 2, "Video 2"),
            TrackSnapshot(
                "audio",
                1,
                "Audio 1",
                items=(TimelineItemSnapshot("voice.wav", 216000, 216500, "uid-v"),),
            ),
            TrackSnapshot("audio", 2, "Audio 2"),
        ),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _source(timeline: TimelineSnapshot | None = None, **overrides: object) -> PlanSource:
    timeline = timeline or _timeline()
    kwargs: dict[str, object] = {
        "project": "davinci-auto-zoom-test",
        "timeline": timeline,
        "frame_rate": timeline_frame_rate(timeline),
        "voice_audio_track": 1,
        "cut_reference_video_track": 1,
        "zoom_video_track": 3,
        "assets": ASSETS,
        "asset_transition_frames": TRANSITIONS,
        "planner_settings": PlannerSettings(),
        "energy_settings": EnergySettings(),
        "found_assets": FOUND,
    }
    kwargs.update(overrides)
    return build_plan_source(**kwargs)  # type: ignore[arg-type]


def test_an_exact_match_is_allowed() -> None:
    assert plan_source_mismatches(_source(), _source()) == ()


def test_a_plan_without_a_source_is_refused() -> None:
    assert validate_plan_source(None, _source()) != ()


@pytest.mark.parametrize(
    ("overrides", "expected_field"),
    [
        ({"project": "another-project"}, "project"),
        ({"voice_audio_track": 2}, "voice_audio_track"),
        ({"cut_reference_video_track": 2}, "cut_reference_video_track"),
        ({"zoom_video_track": 4}, "zoom_video_track"),
        ({"assets": {"x0_to_face_x1": "OTHER_X1", "face_x1_to_x0": "X1_TO_X0"}}, "assets"),
        (
            {"asset_transition_frames": {"x0_to_face_x1": 20, "face_x1_to_x0": 15}},
            "asset_transition_frames",
        ),
        (
            {"planner_settings": PlannerSettings(reset_after_silence_ms=900)},
            "planner_settings",
        ),
        # Phase 6: this one changes where every reset lands, so a plan built with a
        # different lookback must never be applied under the current config.
        (
            {"planner_settings": PlannerSettings(cut_snap_lookback_ms=0)},
            "planner_settings",
        ),
    ],
)
def test_a_changed_scalar_field_is_reported(
    overrides: dict[str, object], expected_field: str
) -> None:
    mismatches = plan_source_mismatches(_source(), _source(**overrides))
    assert any(mismatch.startswith(expected_field) for mismatch in mismatches), mismatches


@pytest.mark.parametrize(
    ("timeline_overrides", "expected_field"),
    [
        ({"name": "DAZ_SOMETHING_ELSE"}, "timeline"),
        ({"unique_id": "uid-other"}, "timeline_unique_id"),
        ({"start_frame": 216100}, "start_frame"),
        ({"end_frame": 219000}, "end_frame"),
        ({"frame_rate": 59.94}, "frame_rate"),
    ],
)
def test_a_changed_timeline_property_is_reported(
    timeline_overrides: dict[str, object], expected_field: str
) -> None:
    changed = _timeline(**timeline_overrides)
    mismatches = plan_source_mismatches(_source(), _source(changed))
    assert any(mismatch.startswith(expected_field) for mismatch in mismatches), mismatches


def test_a_different_asset_media_id_is_reported() -> None:
    """Same configured name, different Media Pool item: the plan is not about that clip."""

    reimported = (
        replace(FOUND[0], media_id="media-x1-reimported", unique_id="uid-x1-new"),
        FOUND[1],
    )
    mismatches = plan_source_mismatches(_source(), _source(found_assets=reimported))
    assert any("x0_to_face_x1" in mismatch for mismatch in mismatches), mismatches


def test_an_ambiguous_asset_loses_its_recorded_ids_and_is_reported() -> None:
    duplicated = (*FOUND, replace(FOUND[0], bin_path="Master/OTHER", unique_id="uid-x1-dup"))
    mismatches = plan_source_mismatches(_source(), _source(found_assets=duplicated))
    assert any("x0_to_face_x1" in mismatch for mismatch in mismatches), mismatches


def test_a_recut_source_is_reported_by_the_fingerprint_alone() -> None:
    """Same name, same id, same range, same duration — only the cuts moved."""

    recut = _timeline(
        tracks=(
            TrackSnapshot(
                "video",
                1,
                "Video 1",
                items=(
                    TimelineItemSnapshot("a.mov", 216000, 216150, "uid-a"),
                    TimelineItemSnapshot("b.mov", 216150, 216300, "uid-b"),
                ),
            ),
            TrackSnapshot("video", 2, "Video 2"),
            TrackSnapshot(
                "audio",
                1,
                "Audio 1",
                items=(TimelineItemSnapshot("voice.wav", 216000, 216500, "uid-v"),),
            ),
            TrackSnapshot("audio", 2, "Audio 2"),
        )
    )
    mismatches = plan_source_mismatches(_source(), _source(recut))
    assert len(mismatches) == 1
    assert "structural fingerprint changed" in mismatches[0]


def test_the_identities_record_every_stable_id_available() -> None:
    identities = _source().asset_identities
    assert identities == (
        # Sorted by role, as `asset_identities` builds them.
        AssetIdentity("face_x1_to_x0", "X1_TO_X0", "media-x0", "uid-x0"),
        AssetIdentity("x0_to_face_x1", "FACE_X1", "media-x1", "uid-x1"),
    )


def test_the_frame_rate_string_is_canonical_across_runs() -> None:
    """Resolve reports 60.0 and 59.94; neither may look like a rate change on its own."""

    assert timeline_frame_rate(_timeline()) == "60"
    assert timeline_frame_rate(_timeline(frame_rate=59.94)) == "60000/1001"
