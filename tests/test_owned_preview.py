"""Safety tests for the two destructive Phase 7 commands, on fakes.

The properties under test, in order of how much they would cost to get wrong:

1. **a clip DAZ did not create is never deleted** — asserted against the fake's own item
   lists, not against the report's opinion of itself;
2. **ambiguity deletes nothing at all** — a single unclassifiable item must produce zero
   `DeleteClips` calls, not a partial clean;
3. **a recovery copy exists before the first delete, and survives any failure**;
4. **two rebuilds produce the same structural result** — same count, same frames, same roles,
   same placement ids, no duplicates. New `GetUniqueId()`s in between are expected and are
   deliberately not part of the comparison.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

import pytest

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.ownership import build_record, serialize
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
from davinci_auto_zoom.resolve.executor import PREVIEW_PREFIX, apply_preview
from davinci_auto_zoom.resolve.owned_preview import (
    CLEAN,
    REBUILD,
    RECOVERY_PREFIX,
    OwnedPreviewRefused,
    recovery_timeline_name,
    run_owned_preview,
)
from davinci_auto_zoom.resolve.session import snapshot_project
from tests.fake_resolve import FakeTimelineItem, build_test_project

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

PLACEMENTS = (
    AssetPlacement(ROLE_FACECAM_X1, FrameRange(216045, 216132), "x1_until_direct_reset"),
    AssetPlacement(ROLE_RESET_X0, FrameRange(216132, 216147), "reset_direct"),
    AssetPlacement(ROLE_FACECAM_X1, FrameRange(216300, 216550), "x1_until_direct_reset"),
    AssetPlacement(ROLE_RESET_X0, FrameRange(216550, 216565), "reset_direct"),
)


def _plan(source: PlanSource | None, placements: Any = PLACEMENTS) -> ZoomPlan:
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


def _source(resolve: Any, project: Any) -> PlanSource:
    snapshot = snapshot_project(resolve, project, CONFIG)
    timeline = snapshot.timeline("DAZ_INPUT")
    assert timeline is not None and CONFIG.asset_timing is not None
    return build_plan_source(
        project=snapshot.project_name,
        timeline=timeline,
        frame_rate=timeline_frame_rate(timeline),
        voice_audio_track=CONFIG.voice_audio_track,
        cut_reference_video_track=CONFIG.cut_reference_video_track,
        zoom_video_track=CONFIG.zoom_video_track,
        assets=CONFIG.assets,
        asset_transition_frames=CONFIG.asset_timing.to_dict(),
        planner_settings=CONFIG.planner,
        found_assets=snapshot.assets,
    )


def _owned_preview() -> tuple[Any, Any, PlanSource, str]:
    """A real, fully owned Phase 7 preview, built by the executor itself.

    Built rather than hand-faked on purpose: the destructive commands must be tested against
    what `apply-preview` actually produces, not against a test author's idea of it.
    """

    resolve, project = build_test_project(audio_tracks=3)
    source = _source(resolve, project)
    report = apply_preview(resolve, project, CONFIG, TARGET, _plan(source), confirmed=True)
    assert report.succeeded is True
    assert report.preview_name is not None
    return resolve, project, source, report.preview_name


def _timeline(project: Any, name: str) -> Any:
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline.GetName() == name:
            return timeline
    return None


def _names(project: Any) -> list[str]:
    return [
        project.GetTimelineByIndex(i).GetName()
        for i in range(1, project.GetTimelineCount() + 1)
    ]


def _zoom_items(project: Any, name: str) -> list[Any]:
    return _timeline(project, name).GetItemListInTrack("video", 3)


def _clean(project: Any, resolve: Any, preview: str, **kwargs: Any) -> Any:
    return run_owned_preview(
        resolve, project, CONFIG, TARGET, preview, operation=CLEAN, confirmed=True, **kwargs
    )


def _rebuild(project: Any, resolve: Any, preview: str, plan: ZoomPlan, **kwargs: Any) -> Any:
    return run_owned_preview(
        resolve,
        project,
        CONFIG,
        TARGET,
        preview,
        operation=REBUILD,
        confirmed=True,
        plan=plan,
        **kwargs,
    )


def _foreign(name: str = "USER_TITLE", start: int = 217000, end: int = 217100) -> Any:
    """A clip on the zoom track that DAZ did not make and must never remove."""

    return FakeTimelineItem(name, start, end, None, None, 1, ("video", 3))


def _structure(project: Any, name: str) -> list[tuple[str, int, int]]:
    return [(i.GetName(), i.GetStart(), i.GetEnd()) for i in _zoom_items(project, name)]


def _deletes(project: Any) -> list[str]:
    return [call for call in project.mutations if call.startswith("DeleteClips")]


# --- naming and opt-in ---------------------------------------------------------------------


def test_the_recovery_name_is_unique_to_the_run() -> None:
    from datetime import datetime

    stamp = datetime(2026, 8, 17, 14, 30, 0)
    assert recovery_timeline_name(stamp, "abcd1234") == "DAZ_RECOVERY_20260817_143000_abcd1234"
    assert recovery_timeline_name(stamp) != recovery_timeline_name(stamp)


@pytest.mark.parametrize("operation", [CLEAN, REBUILD])
def test_without_the_confirmation_flag_nothing_is_deleted(operation: str) -> None:
    resolve, project, source, preview = _owned_preview()
    before = list(project.mutations)
    with pytest.raises(OwnedPreviewRefused, match="requires --confirm-"):
        run_owned_preview(
            resolve,
            project,
            CONFIG,
            TARGET,
            preview,
            operation=operation,
            confirmed=False,
            plan=_plan(source),
        )
    assert project.mutations == before


# --- refusals: nothing may be deleted -------------------------------------------------------


def _refuses(project: Any, resolve: Any, preview: str, match: str, **kwargs: Any) -> None:
    structure = _structure(project, preview)
    with pytest.raises(OwnedPreviewRefused, match=match):
        _clean(project, resolve, preview, **kwargs)
    assert _deletes(project) == []
    assert _structure(project, preview) == structure
    assert not [n for n in _names(project) if n.startswith(RECOVERY_PREFIX)]


@pytest.mark.parametrize("protected", ["DAZ_INPUT", "DAZ_OUTPUT_MVP"])
def test_a_protected_timeline_is_refused(protected: str) -> None:
    resolve, project, _, _ = _owned_preview()
    with pytest.raises(OwnedPreviewRefused, match="protected timeline"):
        _clean(project, resolve, protected)
    assert _deletes(project) == []


def test_a_timeline_that_is_not_a_preview_is_refused() -> None:
    resolve, project = build_test_project(audio_tracks=3)
    project.add_timeline(_timeline(project, "DAZ_INPUT").DuplicateTimeline("SOME_USER_EDIT"))
    with pytest.raises(OwnedPreviewRefused, match="not a DAZ preview"):
        _clean(project, resolve, "SOME_USER_EDIT")
    assert _deletes(project) == []


def test_a_missing_timeline_is_refused() -> None:
    resolve, project, _, _ = _owned_preview()
    with pytest.raises(OwnedPreviewRefused, match="was not found"):
        _clean(project, resolve, f"{PREVIEW_PREFIX}19700101_000000_nope")


def test_a_legacy_preview_is_refused_and_left_exactly_as_it_is() -> None:
    """The Phase 5 and Phase 6 previews, in miniature: real items, no ownership metadata."""

    resolve, project = build_test_project(audio_tracks=3)
    legacy = _timeline(project, "DAZ_INPUT").DuplicateTimeline(
        f"{PREVIEW_PREFIX}20260816_211026_c676d5af"
    )
    legacy.AddTrack("video")
    for placement in PLACEMENTS:
        legacy.add_item(
            "video",
            3,
            FakeTimelineItem(
                "FACE_X1" if placement.asset_role == ROLE_FACECAM_X1 else "FACE_X0_SMOOTH",
                placement.start_frame,
                placement.end_frame,
                None,
                None,
                1,
                ("video", 3),
            ),
        )
    name = legacy.GetName()
    _refuses(project, resolve, name, "legacy preview: no verifiable DAZ ownership")
    assert len(_zoom_items(project, name)) == len(PLACEMENTS)


def test_one_ambiguous_item_stops_the_whole_run_before_any_delete() -> None:
    resolve, project, _, preview = _owned_preview()
    # A marker that claims to be DAZ's and then contradicts itself.
    _zoom_items(project, preview)[2].add_user_marker(
        7, '{"namespace":"davinci-auto-zoom","schema":1}'
    )
    _refuses(project, resolve, preview, "ambiguous")


def test_a_hand_duplicated_preview_is_stale_and_is_never_cleaned() -> None:
    """Section 22 of the contract, and the reason `stale` is a state of its own.

    A user duplicating a tagged preview gets a timeline whose items carry copies of the
    original's ownership records — records naming a preview that is not this one. It looks
    owned, item for item, and it must not be destructively cleanable.
    """

    resolve, project, _, preview = _owned_preview()
    copy = _timeline(project, preview).DuplicateTimeline(
        f"{PREVIEW_PREFIX}20260817_170000_c0p1ed00"
    )
    # The premise: the copy really does carry the markers, and its own identity differs.
    assert copy.GetUniqueId() != _timeline(project, preview).GetUniqueId()
    assert _zoom_items(project, copy.GetName())[0].GetMarkers()

    _refuses(project, resolve, copy.GetName(), "stale")
    assert len(_zoom_items(project, copy.GetName())) == len(PLACEMENTS)


def test_a_single_contradictory_marker_pair_is_ambiguous_and_stops_the_run() -> None:
    resolve, project, source, preview = _owned_preview()
    item = _zoom_items(project, preview)[0]
    item.add_user_marker(
        9,
        serialize(
            build_record(
                preview_id="uid-some-other-preview",
                role="facecam_x1",
                asset="FACE_X1",
                start=item.GetStart(),
                end=item.GetEnd(),
                source_fingerprint=source.structural_fingerprint,
            )
        ),
    )
    _refuses(project, resolve, preview, "different ownership claims")


# --- clean: exactly the owned items ---------------------------------------------------------


def test_a_fully_owned_preview_is_emptied_and_the_recovery_is_removed() -> None:
    resolve, project, _, preview = _owned_preview()
    report = _clean(project, resolve, preview)

    assert report.succeeded is True
    assert report.owned_before == len(PLACEMENTS)
    assert report.deleted_items == len(PLACEMENTS)
    assert _zoom_items(project, preview) == []
    assert report.recovery_created is True
    assert report.recovery_absent_after_success is True
    assert report.recovery_retained is False
    assert [n for n in _names(project) if n.startswith(RECOVERY_PREFIX)] == []


def test_the_other_tracks_and_the_protected_timelines_are_untouched() -> None:
    resolve, project, _, preview = _owned_preview()
    before = {
        (kind, index): [(i.GetName(), i.GetStart()) for i in
                        _timeline(project, name).GetItemListInTrack(kind, index)]
        for name in ("DAZ_INPUT", "DAZ_OUTPUT_MVP")
        for kind, index in [("video", 1), ("video", 2), ("audio", 1), ("audio", 2)]
    }
    preview_v1 = [(i.GetName(), i.GetStart()) for i in
                  _timeline(project, preview).GetItemListInTrack("video", 1)]

    report = _clean(project, resolve, preview)
    assert report.succeeded is True
    assert report.audit_differences == ()
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    after = {
        (kind, index): [(i.GetName(), i.GetStart()) for i in
                        _timeline(project, name).GetItemListInTrack(kind, index)]
        for name in ("DAZ_INPUT", "DAZ_OUTPUT_MVP")
        for kind, index in [("video", 1), ("video", 2), ("audio", 1), ("audio", 2)]
    }
    assert after == before
    assert [(i.GetName(), i.GetStart()) for i in
            _timeline(project, preview).GetItemListInTrack("video", 1)] == preview_v1


def test_a_foreign_clip_survives_a_clean_that_removes_everything_around_it() -> None:
    """Section 14 of the contract: 4 owned go, 1 unowned stays, and its presence is reported."""

    resolve, project, _, preview = _owned_preview()
    sentinel = _foreign()
    _timeline(project, preview).add_item("video", 3, sentinel)
    sentinel_id = sentinel.GetUniqueId()

    report = _clean(project, resolve, preview)

    assert report.succeeded is True
    assert report.owned_before == len(PLACEMENTS)
    assert report.unowned_before == 1
    assert report.deleted_items == len(PLACEMENTS)
    assert report.preserved_unowned == 1
    survivors = _zoom_items(project, preview)
    assert [i.GetName() for i in survivors] == ["USER_TITLE"]
    assert survivors[0] is sentinel
    assert survivors[0].GetUniqueId() == sentinel_id
    assert "1 unowned item(s) left untouched" in report.to_text()


def test_a_track_holding_only_foreign_clips_deletes_nothing() -> None:
    resolve, project = build_test_project(audio_tracks=3)
    preview = _timeline(project, "DAZ_INPUT").DuplicateTimeline(
        f"{PREVIEW_PREFIX}20260817_150000_abcdef01"
    )
    preview.AddTrack("video")
    preview.add_item("video", 3, _foreign())
    _refuses(project, resolve, preview.GetName(), "legacy preview")


def test_an_empty_target_track_is_a_successful_no_op() -> None:
    resolve, project, _, preview = _owned_preview()
    assert _clean(project, resolve, preview).succeeded is True
    second = _clean(project, resolve, preview)
    assert second.succeeded is True
    assert second.deleted_items == 0
    assert _deletes(project) == _deletes(project)[:1]  # only the first run deleted anything


def test_the_delete_is_never_a_ripple_delete() -> None:
    resolve, project, _, preview = _owned_preview()
    report = _clean(project, resolve, preview)

    assert [call.ripple for call in report.delete_calls] == [False]
    assert [call.items for call in report.delete_calls] == [len(PLACEMENTS)]
    # The exact argument Resolve was handed, from the fake's own log.
    assert _deletes(project) == [
        f"DeleteClips([FACE_X1@216045, FACE_X0_SMOOTH@216132, FACE_X1@216300, "
        f"FACE_X0_SMOOTH@216550], ripple=False) on {preview!r}"
    ]


def test_no_video_track_is_ever_deleted() -> None:
    resolve, project, _, preview = _owned_preview()
    _clean(project, resolve, preview)
    assert not any(call.startswith("DeleteTrack") for call in project.mutations)
    assert _timeline(project, preview).GetTrackCount("video") == 3


# --- clean: failures keep the recovery -------------------------------------------------------


def test_a_delete_that_reports_failure_keeps_the_recovery_and_names_it() -> None:
    resolve, project, _, preview = _owned_preview()
    _timeline(project, preview).delete_clips_outcome = False
    report = _clean(project, resolve, preview)

    assert report.succeeded is False
    assert report.error is not None
    assert report.recovery_retained is True
    assert report.recovery_name is not None
    assert report.recovery_name in _names(project)
    assert report.recovery_name in report.to_text()
    # Nothing was actually removed, and the user's timeline is back.
    assert len(_zoom_items(project, preview)) == len(PLACEMENTS)
    assert report.current_timeline_restored is True


def test_a_delete_that_raises_keeps_the_recovery() -> None:
    resolve, project, _, preview = _owned_preview()
    _timeline(project, preview).delete_clips_outcome = None
    report = _clean(project, resolve, preview)

    assert report.succeeded is False
    assert report.recovery_retained is True
    assert report.recovery_name in _names(project)


def test_a_recovery_that_cannot_be_created_deletes_nothing_at_all() -> None:
    resolve, project, _, preview = _owned_preview()
    _timeline(project, preview).DuplicateTimeline = lambda name: None  # type: ignore[method-assign]
    report = _clean(project, resolve, preview)

    assert report.succeeded is False
    assert report.recovery_created is False
    assert _deletes(project) == []
    assert len(_zoom_items(project, preview)) == len(PLACEMENTS)


def test_a_protected_timeline_changing_mid_run_keeps_the_recovery() -> None:
    resolve, project, _, preview = _owned_preview()
    original = _timeline(project, preview).DeleteClips

    def delete(items: list[Any], ripple: bool = False) -> bool:
        result = original(items, ripple)
        _timeline(project, "DAZ_INPUT").add_item("video", 1, _foreign("INTRUDER", 218000, 218100))
        return result

    _timeline(project, preview).DeleteClips = delete  # type: ignore[method-assign]
    report = _clean(project, resolve, preview)

    assert report.succeeded is False
    assert report.audit_differences
    assert report.recovery_retained is True


# --- rebuild ----------------------------------------------------------------------------------


def test_a_rebuild_reapplies_the_plan_exactly() -> None:
    resolve, project, source, preview = _owned_preview()
    before = _structure(project, preview)

    report = _rebuild(project, resolve, preview, _plan(source))

    assert report.succeeded is True
    assert report.deleted_items == len(PLACEMENTS)
    assert report.items_created == report.items_owned == len(PLACEMENTS)
    assert report.items_unowned == report.items_ambiguous == report.items_stale == 0
    assert _structure(project, preview) == before
    assert report.recovery_absent_after_success is True


def test_two_rebuilds_produce_the_same_structural_result_with_no_duplicates() -> None:
    """The live idempotence proof, in miniature."""

    resolve, project, source, preview = _owned_preview()

    first = _rebuild(project, resolve, preview, _plan(source))
    structure_after_first = _structure(project, preview)

    second = _rebuild(project, resolve, preview, _plan(source))

    assert first.succeeded is second.succeeded is True
    # The four things idempotence is defined as: same placement ids, same frames, same roles,
    # no accumulation. Resolve's own object ids are expected to change and are deliberately
    # NOT part of the comparison — we want the same desired state, not the same objects.
    assert second.placement_ids == first.placement_ids
    assert _structure(project, preview) == structure_after_first
    assert [r.role for r in second.insertions] == [r.role for r in first.insertions]
    assert len(_zoom_items(project, preview)) == len(PLACEMENTS)


def test_a_third_rebuild_still_does_not_accumulate() -> None:
    resolve, project, source, preview = _owned_preview()
    for _ in range(3):
        assert _rebuild(project, resolve, preview, _plan(source)).succeeded is True
    assert len(_zoom_items(project, preview)) == len(PLACEMENTS)


def test_a_rebuild_refuses_a_foreign_item_before_deleting_anything() -> None:
    """clean tolerates a foreign clip; rebuild must not (D040)."""

    resolve, project, source, preview = _owned_preview()
    _timeline(project, preview).add_item("video", 3, _foreign())
    structure = _structure(project, preview)

    with pytest.raises(OwnedPreviewRefused, match="DAZ did not create"):
        _rebuild(project, resolve, preview, _plan(source))

    assert _deletes(project) == []
    assert _structure(project, preview) == structure
    assert not [n for n in _names(project) if n.startswith(RECOVERY_PREFIX)]


def test_a_rebuild_refuses_ambiguous_ownership_before_deleting_anything() -> None:
    resolve, project, source, preview = _owned_preview()
    _zoom_items(project, preview)[1].add_user_marker(
        3, '{"namespace":"davinci-auto-zoom","schema":77}'
    )
    with pytest.raises(OwnedPreviewRefused, match="ambiguous"):
        _rebuild(project, resolve, preview, _plan(source))
    assert _deletes(project) == []


def test_a_rebuild_refuses_a_stale_plan_before_deleting_anything() -> None:
    from dataclasses import replace

    resolve, project, source, preview = _owned_preview()
    stale = replace(source, structural_fingerprint="sha256:stale")
    with pytest.raises(OwnedPreviewRefused, match="no longer matches the project"):
        _rebuild(project, resolve, preview, _plan(stale))
    assert _deletes(project) == []


def test_a_rebuild_refuses_when_the_source_was_recut_after_planning() -> None:
    resolve, project, source, preview = _owned_preview()
    _timeline(project, "DAZ_INPUT").add_item(
        "video", 1, FakeTimelineItem("extra.mov", 216500, 216600, None, None, 0, ("video", 1))
    )
    with pytest.raises(OwnedPreviewRefused, match="no longer matches the project"):
        _rebuild(project, resolve, preview, _plan(source))
    assert _deletes(project) == []


def test_a_rebuild_refuses_when_the_configured_asset_changed() -> None:
    from dataclasses import replace

    resolve, project, source, preview = _owned_preview()
    moved = replace(
        source, assets=(("facecam_x1", "SOMETHING_ELSE"), ("reset_x0", "FACE_X0_SMOOTH"))
    )
    with pytest.raises(OwnedPreviewRefused, match="no longer matches the project"):
        _rebuild(project, resolve, preview, _plan(moved))
    assert _deletes(project) == []


def test_a_rebuild_that_fails_during_reinsertion_keeps_the_recovery() -> None:
    resolve, project, source, preview = _owned_preview()
    project.GetMediaPool().AppendToTimeline = lambda clip_infos: []  # type: ignore[method-assign]

    report = _rebuild(project, resolve, preview, _plan(source))

    assert report.succeeded is False
    assert report.cleaned is True  # the delete half worked
    assert report.recovery_retained is True
    assert report.recovery_name in _names(project)
    assert "RECOVERY TIMELINE KEPT" in report.to_text()


def test_a_rebuild_that_fails_during_retagging_keeps_the_recovery() -> None:
    resolve, project, source, preview = _owned_preview()
    media_pool = project.GetMediaPool()
    original = media_pool.AppendToTimeline

    def append(clip_infos: list[dict[str, Any]]) -> list[Any]:
        items = original(clip_infos)
        for item in items:
            item.add_marker_outcome = False
        return items

    media_pool.AppendToTimeline = append  # type: ignore[method-assign]
    report = _rebuild(project, resolve, preview, _plan(source))

    assert report.succeeded is False
    assert report.insertions[0].owned is False
    assert report.recovery_retained is True
    assert report.recovery_name in _names(project)


def test_a_successful_rebuild_leaves_the_protected_timelines_alone() -> None:
    resolve, project, source, preview = _owned_preview()
    report = _rebuild(project, resolve, preview, _plan(source))

    assert report.succeeded is True
    assert report.audit_differences == ()
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    assert sorted(n for n in _names(project) if n.startswith(RECOVERY_PREFIX)) == []
    assert not any("SaveProject" in call for call in project.mutations)


def test_the_preview_is_made_current_before_the_delete() -> None:
    """Regression: DeleteClips silently no-ops on a non-current timeline (D042).

    Found live on Studio 21.0.4.5, where the first clean-preview run returned False from
    DeleteClips, removed nothing and correctly kept its recovery. The README documents no
    such restriction; `AppendToTimeline` has the same one and it is the only reason we
    guessed right. The fake reproduces the behaviour, so this test fails against the old code.
    """

    resolve, project, _, preview = _owned_preview()
    order: list[str] = []
    original_set = project.SetCurrentTimeline
    original_delete = _timeline(project, preview).DeleteClips

    def set_current(timeline: Any) -> bool:
        order.append(f"SetCurrentTimeline({timeline.GetName()})")
        return original_set(timeline)

    def delete(items: list[Any], ripple: bool = False) -> bool:
        order.append("DeleteClips")
        return original_delete(items, ripple)

    project.SetCurrentTimeline = set_current  # type: ignore[method-assign]
    _timeline(project, preview).DeleteClips = delete  # type: ignore[method-assign]

    report = _clean(project, resolve, preview)

    assert report.succeeded is True
    assert order.index(f"SetCurrentTimeline({preview})") < order.index("DeleteClips")
    # And the user's timeline is put back afterwards, as part of the transaction.
    assert order[-1] == "SetCurrentTimeline(DAZ_OUTPUT_MVP)"
    assert report.current_timeline_restored is True


def test_a_preview_that_cannot_be_made_current_deletes_nothing() -> None:
    resolve, project, _, preview = _owned_preview()
    original = project.SetCurrentTimeline

    def set_current(timeline: Any) -> bool:
        return False if timeline.GetName() == preview else original(timeline)

    project.SetCurrentTimeline = set_current  # type: ignore[method-assign]
    report = _clean(project, resolve, preview)

    assert report.succeeded is False
    assert _deletes(project) == []
    assert len(_zoom_items(project, preview)) == len(PLACEMENTS)
    assert report.recovery_retained is True
