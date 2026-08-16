"""Safety tests for the write-capable Phase 2 probe.

The property under test is not "the probe works" but "the probe cannot write when it must
not". Every refusal case asserts the shared mutation log is *empty*, which is the only
statement that actually protects the user's timelines. No test here needs Resolve.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.probe import (
    WriteProbeTarget,
    preflight_failures,
    signature_differences,
    structural_signature,
)
from davinci_auto_zoom.resolve.session import snapshot_project, snapshot_timeline
from davinci_auto_zoom.resolve.write_probe import (
    SCRATCH_PREFIX,
    ProbeReport,
    WriteProbeRefused,
    plan_experiments,
    run_write_probe,
    scratch_timeline_name,
)
from tests.fake_resolve import build_test_project

TARGET = WriteProbeTarget(
    project="davinci-auto-zoom-test",
    source_timeline="DAZ_INPUT",
    reference_timeline="DAZ_OUTPUT_MVP",
    asset_bin="DAVINCI_AUTO_ZOOM",
    assets=(("facecam_x1", "FACE_X1"), ("reset_x0", "FACE_X0_SMOOTH")),
)


def _run(target: WriteProbeTarget = TARGET, **kwargs: object):  # type: ignore[no-untyped-def]
    resolve, project = build_test_project()
    report = run_write_probe(
        resolve, project, Config(), target, confirmed=True, **kwargs  # type: ignore[arg-type]
    )
    return report, project


# --- refusals: nothing may be written ------------------------------------------------


def _refuses(target: WriteProbeTarget, **kwargs: object) -> None:
    resolve, project = build_test_project()
    with pytest.raises(WriteProbeRefused):
        run_write_probe(
            resolve, project, Config(), target, confirmed=True, **kwargs  # type: ignore[arg-type]
        )
    assert project.mutations == []


def test_missing_confirmation_flag_writes_nothing() -> None:
    resolve, project = build_test_project()
    with pytest.raises(WriteProbeRefused):
        run_write_probe(resolve, project, Config(), TARGET, confirmed=False)
    assert project.mutations == []


def test_wrong_project_writes_nothing() -> None:
    from dataclasses import replace

    _refuses(replace(TARGET, project="some-other-project"))


def test_missing_source_timeline_writes_nothing() -> None:
    from dataclasses import replace

    _refuses(replace(TARGET, source_timeline="DAZ_DOES_NOT_EXIST"))


def test_missing_reference_timeline_writes_nothing() -> None:
    from dataclasses import replace

    _refuses(replace(TARGET, reference_timeline="DAZ_DOES_NOT_EXIST"))


def test_same_source_and_reference_writes_nothing() -> None:
    from dataclasses import replace

    _refuses(replace(TARGET, source_timeline="DAZ_INPUT", reference_timeline="DAZ_INPUT"))


def test_missing_asset_writes_nothing() -> None:
    from dataclasses import replace

    _refuses(replace(TARGET, assets=(("facecam_x1", "NOT_AN_ASSET"),)))


def test_duplicate_asset_writes_nothing() -> None:
    resolve, project = build_test_project()
    bin_folder = project.GetMediaPool().GetRootFolder().GetSubFolderList()[0]
    bin_folder.GetClipList().append(bin_folder.GetClipList()[0])  # same name twice
    with pytest.raises(WriteProbeRefused, match="exactly once"):
        run_write_probe(resolve, project, Config(), TARGET, confirmed=True)
    assert project.mutations == []


def test_wrong_asset_type_writes_nothing() -> None:
    from dataclasses import replace

    _refuses(replace(TARGET, expected_clip_type="Adjustment Clip"))


def test_preflight_lists_every_problem_at_once() -> None:
    from dataclasses import replace

    resolve, project = build_test_project()
    snapshot = snapshot_project(resolve, project, Config())
    failures = preflight_failures(
        snapshot, replace(TARGET, project="nope", source_timeline="nope")
    )
    assert len(failures) >= 2
    assert preflight_failures(snapshot, TARGET) == ()


# --- happy path -----------------------------------------------------------------------


def test_probe_inserts_on_the_scratch_track_and_leaves_originals_untouched() -> None:
    report, _ = _run()

    assert report.succeeded
    assert report.audit_differences == ()
    assert report.scratch_name is not None
    assert report.scratch_name.startswith(SCRATCH_PREFIX)
    assert report.scratch_track_index == 3
    assert report.scratch_matched_source is True

    labels = [result.label for result in report.insertions]
    assert labels == [
        "x1_native",
        "x1_endframe_semantics",
        "x1_short_87",
        "x1_extended_141",
        "x1_shorter_than_animation",
        "x0_native",
    ]
    for result in report.insertions:
        assert result.track_index == 3
        assert result.record_frame_is_absolute is True
        assert result.duration_as_requested is True
        assert result.fusion_comp_count == 1
        assert result.comp_comparison is not None
        assert result.comp_comparison["carries_user_effect"] is True


def test_every_write_targets_only_the_scratch_timeline() -> None:
    report, project = _run()
    scratch = report.scratch_name
    appends = [call for call in project.mutations if call.startswith("AppendToTimeline")]
    assert appends
    assert all(f"'{scratch}'" in call for call in appends)
    assert not any("DAZ_INPUT'" in call or "DAZ_OUTPUT_MVP'" in call for call in appends)


def test_cleanup_restores_the_previous_timeline_and_deletes_the_scratch() -> None:
    report, project = _run()
    assert report.previous_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.scratch_deleted is True
    assert report.scratch_absent_after_cleanup is True
    deletions = [c for c in project.mutations if c.startswith("DeleteTimelines")]
    assert deletions == [f"DeleteTimelines({report.scratch_name!r})"]


def test_cleanup_still_runs_when_an_experiment_step_explodes() -> None:
    resolve, project = build_test_project()
    source = project.GetTimelineByIndex(1)
    original_duplicate = source.DuplicateTimeline

    def duplicate_then_break(name: str):  # type: ignore[no-untyped-def]
        scratch = original_duplicate(name)
        scratch.AddTrack = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        return scratch

    source.DuplicateTimeline = duplicate_then_break  # type: ignore[method-assign]
    report = run_write_probe(resolve, project, Config(), TARGET, confirmed=True)

    assert report.error is not None and "boom" in report.error
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.scratch_absent_after_cleanup is True
    assert report.succeeded is False


def test_scratch_names_are_unique_per_run() -> None:
    assert scratch_timeline_name() != scratch_timeline_name()
    assert scratch_timeline_name().startswith(SCRATCH_PREFIX)


def test_cleanup_never_deletes_a_timeline_this_run_did_not_create() -> None:
    """A scratch handle whose name lacks the run's prefix must not be deleted."""

    resolve, project = build_test_project()
    source = project.GetTimelineByIndex(1)
    original_duplicate = source.DuplicateTimeline

    def duplicate_with_foreign_name(_name: str):  # type: ignore[no-untyped-def]
        return original_duplicate("SOMEONE_ELSES_TIMELINE")

    source.DuplicateTimeline = duplicate_with_foreign_name  # type: ignore[method-assign]
    report = run_write_probe(resolve, project, Config(), TARGET, confirmed=True)

    assert report.scratch_deleted is False
    assert not any(c.startswith("DeleteTimelines") for c in project.mutations)
    assert any("refusing to delete" in note for note in report.notes)


def test_media_pool_selection_workaround_is_only_used_after_a_real_failure() -> None:
    resolve, project = build_test_project()  # noqa: F841 - project owns the media pool
    project.GetMediaPool().requires_selection = True
    report = run_write_probe(resolve, project, Config(), TARGET, confirmed=True)

    assert report.succeeded
    # Only the first call needs it: the selection Resolve was missing now exists.
    assert report.insertions[0].used_media_pool_selection_workaround is True
    assert any("retrying once" in note for note in report.notes)

    report_without_bug, _ = _run()
    assert not any(
        r.used_media_pool_selection_workaround for r in report_without_bug.insertions
    )


def test_audit_reports_a_change_to_a_protected_timeline() -> None:
    resolve, project = build_test_project()
    reference = project.GetTimelineByIndex(2)
    before = structural_signature(snapshot_timeline(reference, is_current=False))
    reference.AddTrack("video")
    after = structural_signature(snapshot_timeline(reference, is_current=False))

    differences = signature_differences("DAZ_OUTPUT_MVP", before, after)
    assert differences
    assert any("track_count" in difference for difference in differences)


def test_report_is_json_safe_and_never_leaks_proxy_objects() -> None:
    import json

    report, _ = _run()
    payload = json.dumps(report.to_dict())
    assert "FakeMediaPoolItem" not in payload
    assert '"mediaPoolItem": "FACE_X1"' in payload


def test_plan_puts_the_native_duration_experiment_first() -> None:
    experiments = plan_experiments(
        TARGET,
        {"FACE_X1": 132, "FACE_X0_SMOOTH": 42},
        first_record_frame=216200,
        spacing=400,
    )
    assert experiments[0].label == "x1_native"
    assert experiments[0].expected_duration == 132
    assert experiments[1].required is False  # semantics probe only
    assert experiments[3].expected_duration == 141
    # endFrame is exclusive on the verified build: it equals the requested duration.
    assert experiments[3].end_frame == 141
    assert experiments[3].exclusive_duration == 141
    assert [e.record_frame for e in experiments] == [
        216200,
        216600,
        217000,
        217400,
        217800,
        218200,
    ]


def test_empty_report_is_not_a_success() -> None:
    assert ProbeReport().succeeded is False
