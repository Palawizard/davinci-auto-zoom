"""Safety tests for the Phase 3 render probe.

The property that matters is not "does it render" — it is **what state does it leave
behind**. Every test here either asserts that a guard produced zero mutations, or that the
mutations it did make were all undone.
"""

import wave
from pathlib import Path

import pytest

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.probe import (
    VoiceRenderTarget,
    voice_render_preflight_failures,
)
from davinci_auto_zoom.resolve.session import snapshot_project
from davinci_auto_zoom.resolve.voice_render import (
    SCRATCH_PREFIX,
    VoiceRenderRefused,
    render_voice_track,
    restore_preset_name,
    scratch_timeline_name,
)
from tests.fake_resolve import build_test_project

TARGET = VoiceRenderTarget(
    project="davinci-auto-zoom-test", source_timeline="DAZ_INPUT", voice_audio_track=1
)


@pytest.fixture
def live(tmp_path):
    """A fake project shaped like the real one, plus a working directory.

    `media-storage` stands in for a Resolve Media Storage volume: it is the only place the
    real application will render to (D024).
    """

    resolve, project = build_test_project(audio_tracks=3)
    storage = tmp_path / "media-storage"
    storage.mkdir()
    resolve.media_storage.volumes = [str(storage)]
    return resolve, project, tmp_path


def snapshot_of(resolve, project):
    return snapshot_project(resolve, project, Config())


def preflight(live, target=TARGET, **overrides):
    """Call the pure preflight with everything satisfied unless a test says otherwise."""

    resolve, project, _ = live
    kwargs = {
        "render_presets": ("Audio Only",),
        "rendering_in_progress": False,
        "media_storage_volumes": tuple(resolve.media_storage.volumes),
    }
    kwargs.update(overrides)
    return voice_render_preflight_failures(snapshot_of(resolve, project), target, **kwargs)


def run(live, target=TARGET, **kwargs):
    resolve, project, tmp_path = live
    return render_voice_track(
        resolve,
        project,
        Config(),
        target,
        tmp_path,
        confirmed=kwargs.pop("confirmed", True),
        poll_seconds=0.0,
        **kwargs,
    )


# --- preflight (pure) -----------------------------------------------------------------


def test_preflight_passes_for_the_expected_project(live):
    assert preflight(live) == ()


def test_preflight_refuses_the_wrong_project(live):
    failures = preflight(
        live,
        VoiceRenderTarget(project="someone-elses-project", source_timeline="DAZ_INPUT",
                          voice_audio_track=1),
    )
    assert any("open project" in failure for failure in failures)


def test_preflight_refuses_a_missing_timeline(live):
    failures = preflight(
        live,
        VoiceRenderTarget(project=TARGET.project, source_timeline="NOPE", voice_audio_track=1),
    )
    assert any("not found" in failure for failure in failures)


def test_preflight_refuses_an_out_of_range_voice_track(live):
    failures = preflight(
        live,
        VoiceRenderTarget(project=TARGET.project, source_timeline="DAZ_INPUT",
                          voice_audio_track=9),
    )
    assert any("does not exist" in failure and "A1 is 1" in failure for failure in failures)


def test_preflight_refuses_a_zero_or_negative_track_index(live):
    failures = preflight(
        live,
        VoiceRenderTarget(project=TARGET.project, source_timeline="DAZ_INPUT",
                          voice_audio_track=0),
    )
    assert any("1-based" in failure for failure in failures)


def test_preflight_refuses_while_the_user_is_already_rendering(live):
    failures = preflight(
        live,
        rendering_in_progress=True,
    )
    assert any("already rendering" in failure for failure in failures)


def test_preflight_refuses_a_missing_render_preset(live):
    failures = preflight(
        live,
        render_presets=("H.264 Master",),
    )
    assert any("Audio Only" in failure for failure in failures)


# --- fail-closed: a refused run mutates nothing ---------------------------------------


def test_without_the_confirmation_flag_nothing_happens(live):
    _, project, _ = live
    with pytest.raises(VoiceRenderRefused, match="--confirm-resolve-render-test"):
        run(live, confirmed=False)
    assert project.mutations == []


def test_a_failed_preflight_mutates_nothing(live):
    _, project, _ = live
    with pytest.raises(VoiceRenderRefused, match="preflight failed"):
        run(live, VoiceRenderTarget(project="wrong", source_timeline="DAZ_INPUT",
                                    voice_audio_track=1))
    assert project.mutations == []


def test_a_busy_render_queue_mutates_nothing(live):
    _, project, _ = live
    project.rendering_in_progress = True
    with pytest.raises(VoiceRenderRefused, match="already rendering"):
        run(live)
    assert project.mutations == []


def test_missing_ffmpeg_refuses_before_touching_resolve(live, monkeypatch):
    from davinci_auto_zoom.speech import audio as audio_module

    _, project, _ = live
    monkeypatch.setattr(audio_module.shutil, "which", lambda _: None)
    with pytest.raises(VoiceRenderRefused, match="ffmpeg"):
        run(live)
    assert project.mutations == []


# --- the happy path, and what it leaves behind ----------------------------------------


def test_a_successful_run_isolates_the_voice_track_and_cleans_up(live):
    resolve, project, _ = live
    before = snapshot_of(resolve, project).timeline("DAZ_INPUT")
    report, rendered = run(live)

    assert report.error is None
    assert rendered is not None and rendered.is_file()
    assert report.queue.job_status == "Complete"

    # Every other audio track was removed from the scratch, verifiably.
    assert report.audio_tracks_before == 3
    assert report.audio_tracks_after == 1
    assert report.removed_audio_tracks == (2, 3)
    assert report.voice_track_name == "Audio 1"
    assert project.GetRenderJobList() == []
    assert report.clean

    # The source timeline never had a track removed.
    assert not any(
        "DeleteTrack" in call and "DAZ_INPUT" in call for call in project.mutations
    )
    after = snapshot_of(resolve, project).timeline("DAZ_INPUT")
    assert before == after
    assert len(after.tracks_of("audio")) == 3


def test_the_unverifiable_enable_api_is_never_used(live):
    """D017: SetTrackEnable claims success without changing anything readable."""

    _, project, _ = live
    run(live)
    assert not any("SetTrackEnable" in call for call in project.mutations)


def test_a_middle_voice_track_survives_isolation(live):
    """Deleting tracks renumbers them; A2 must still be the track that gets analysed."""

    _, project, _ = live
    report, _ = run(
        live,
        VoiceRenderTarget(
            project=TARGET.project, source_timeline="DAZ_INPUT", voice_audio_track=2
        ),
    )
    assert report.removed_audio_tracks == (1, 3)
    assert report.voice_track_name == "Audio 2"
    assert report.audio_tracks_after == 1
    assert report.error is None


def test_a_failed_track_deletion_aborts_before_rendering(live, monkeypatch):
    _, project, _ = live
    original = type(project.GetTimelineByIndex(1)).DeleteTrack
    monkeypatch.setattr(
        type(project.GetTimelineByIndex(1)),
        "DeleteTrack",
        lambda self, kind, index: False if kind == "audio" else original(self, kind, index),
    )
    report, rendered = run(live)
    assert rendered is None
    assert "DeleteTrack" in (report.error or "")
    # No render job was ever queued, and the scratch is gone.
    assert report.queue.our_job_id is None
    assert project.GetRenderJobList() == []
    assert report.scratch_absent_after_cleanup is True


def test_the_scratch_timeline_is_created_and_deleted(live):
    resolve, project, _ = live
    report, _ = run(live)
    assert report.scratch_name is not None
    assert report.scratch_name.startswith(SCRATCH_PREFIX)
    assert report.scratch_deleted is True
    assert report.scratch_absent_after_cleanup is True
    names = [
        project.GetTimelineByIndex(i).GetName()
        for i in range(1, project.GetTimelineCount() + 1)
    ]
    assert names == ["DAZ_INPUT", "DAZ_OUTPUT_MVP"]


def test_the_previously_open_timeline_is_restored(live):
    _, project, _ = live
    report, _ = run(live)
    assert report.previous_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"


def test_a_preexisting_render_job_is_never_deleted(live):
    _, project, _ = live
    project.SetRenderSettings({"TargetDir": str(project.GetName())})
    existing = project.AddRenderJob()
    report, _ = run(live)

    assert existing in report.queue.job_ids_before
    assert existing in report.queue.job_ids_after
    assert report.queue.preexisting_jobs_preserved is True
    assert report.queue.our_job_id != existing
    assert report.queue.our_job_deleted is True
    assert [job["JobId"] for job in project.GetRenderJobList()] == [existing]


def test_the_deliver_page_comes_back_to_where_it_was(live):
    _, project, _ = live
    before = (project.render_format, project.render_codec, project.render_mode)
    presets_before = project.GetRenderPresetList()

    report, _ = run(live)

    assert (project.render_format, project.render_codec, project.render_mode) == before
    assert project.GetRenderPresetList() == presets_before
    assert report.delivery.preset_saved is True
    assert report.delivery.preset_deleted is True
    assert report.delivery.format_restored is True
    assert report.delivery.mode_restored is True
    assert report.delivery.unrestored == ()


def test_an_unsnapshottable_deliver_page_fails_closed(live):
    """If the Deliver state cannot be captured, it could not be restored either.

    `SetRenderSettings` has no getter (D020), so the temporary preset *is* the snapshot. With
    no snapshot the run must stop before it loads a preset or changes a single render setting,
    rather than proceeding and leaving the user's Deliver page somewhere else.
    """

    _, project, _ = live
    project.can_save_render_preset = False
    report, rendered = run(live)

    assert rendered is None
    assert report.delivery.preset_saved is False
    assert report.succeeded is False
    assert "SaveAsNewRenderPreset" in (report.error or "")
    assert any("SaveAsNewRenderPreset" in note for note in report.notes)
    # Nothing downstream ran: no preset loaded, no render settings written, no job queued.
    # (Cleanup still re-asserts the format/codec/mode it read, which writes the same values
    # back — that is the verification step, not a change.)
    assert not any(
        call.startswith(("LoadRenderPreset", "SetRenderSettings", "AddRenderJob"))
        for call in project.mutations
    )
    assert project.GetRenderJobList() == []
    assert report.queue.our_job_id is None
    # And the Deliver page is untouched, so there is nothing to report as unrestored.
    assert (project.render_format, project.render_codec, project.render_mode) == (
        "mov",
        "ProRes422HQ",
        1,
    )
    assert report.delivery.unrestored == ()


def test_the_first_deliver_mutation_is_already_inside_the_cleanup_transaction(live, monkeypatch):
    """`SaveAsNewRenderPreset` writes to the project; a later failure must still undo it."""

    _, project, _ = live
    presets_before = project.GetRenderPresetList()
    # Fail at the very next mutating step after the preset was created.
    monkeypatch.setattr(
        type(project.GetTimelineByIndex(1)), "DuplicateTimeline", lambda self, name: None
    )
    report, rendered = run(live)

    assert rendered is None
    assert "DuplicateTimeline" in (report.error or "")
    # The preset existed, and cleanup removed it: no DAZ_ leftovers on the Deliver page.
    assert report.delivery.preset_saved is True
    assert report.delivery.preset_deleted is True
    assert project.GetRenderPresetList() == presets_before
    assert not any(name.startswith("DAZ_RENDER_RESTORE_") for name in presets_before)
    assert report.delivery.unrestored == ()
    assert (project.render_format, project.render_codec, project.render_mode) == (
        "mov",
        "ProRes422HQ",
        1,
    )


def test_a_temporary_preset_that_cannot_be_deleted_is_reported(live):
    _, project, _ = live
    project.DeleteRenderPreset = lambda name: False
    report, _ = run(live)

    assert report.delivery.preset_deleted is False
    assert any("still exists" in item for item in report.delivery.unrestored)
    assert report.clean is False
    assert report.succeeded is False


def test_a_failed_render_still_cleans_up_completely(live):
    resolve, project, _ = live
    project.render_outcome = "Failed"
    report, rendered = run(live)

    assert rendered is None
    assert report.error is not None
    assert report.succeeded is False
    # Everything that was created was still removed.
    assert report.scratch_absent_after_cleanup is True
    assert project.GetRenderJobList() == []
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"
    assert report.audit_differences == ()


def test_the_render_target_is_inside_media_storage_and_is_removed_after(live):
    """Resolve refuses any render path outside Media Storage (D024)."""

    resolve, project, tmp_path = live
    storage = Path(resolve.media_storage.volumes[0])
    report, rendered = run(live)

    assert report.render_directory is not None
    assert Path(report.render_directory).parent == storage
    assert Path(report.render_directory).name.startswith("DAZ_RENDER_TMP_")
    # The audio was moved out, and nothing of ours is left in the user's media storage.
    assert report.render_directory_removed is True
    assert not Path(report.render_directory).exists()
    assert list(storage.iterdir()) == []
    assert rendered is not None and rendered.is_file()
    assert rendered.parent == tmp_path


def test_no_media_storage_volume_refuses_before_mutating(live):
    resolve, project, _ = live
    resolve.media_storage.volumes = []
    with pytest.raises(VoiceRenderRefused, match="Media Storage"):
        run(live)
    assert project.mutations == []


def test_an_unwritable_media_storage_volume_is_reported(live):
    resolve, project, _ = live
    resolve.media_storage.volumes = ["/proc/definitely/not/writable"]
    report, rendered = run(live)
    assert rendered is None
    assert "render directory" in (report.error or "")
    # Nothing was created, so nothing needed undoing.
    assert report.scratch_name is None
    assert project.GetRenderJobList() == []


def test_a_job_that_never_starts_is_reported_instead_of_spinning(live):
    """A modal dialog in the Resolve GUI leaves a job queued and never running (D018)."""

    _, project, _ = live
    project.render_outcome = "Ready"  # queued forever, never picked up
    report, rendered = run(live)

    assert rendered is None
    assert "never started" in (report.error or "")
    assert "modal dialog" in (report.error or "")
    # And it still cleaned up everything it had created.
    assert project.GetRenderJobList() == []
    assert report.scratch_absent_after_cleanup is True
    assert report.restored_current_timeline == "DAZ_OUTPUT_MVP"


def test_start_rendering_is_called_positionally(live):
    """The keyword overload hung indefinitely through the Blackmagic C bridge (D018)."""

    _, project, _ = live
    run(live)
    started = [call for call in project.mutations if call.startswith("StartRendering")]
    assert len(started) == 1


def test_a_rejected_render_settings_call_aborts_and_cleans_up(live):
    _, project, _ = live
    project.accept_render_settings = False
    report, rendered = run(live)
    assert rendered is None
    assert "SetRenderSettings" in (report.error or "")
    assert report.scratch_absent_after_cleanup is True
    assert project.GetRenderJobList() == []


def test_the_render_covers_the_whole_timeline_not_a_marked_range(live):
    _, project, _ = live
    run(live)
    # The job's settings are captured at AddRenderJob time.
    assert project.render_settings["SelectAllFrames"] is True
    assert project.render_settings["ExportVideo"] is False
    assert project.render_settings["ExportAudio"] is True
    assert project.render_settings["AddFrameHandles"] == 0
    assert project.render_settings["UseFullExtents"] is False
    # D019: documented but rejected by Studio 21.0.4.5, and SetRenderSettings is
    # all-or-nothing, so including it would fail every render.
    assert "ReplaceExistingFilesInPlace" not in project.render_settings


def test_the_rendered_file_matches_the_timeline_length(live):
    _, project, _ = live
    report, rendered = run(live)
    with wave.open(str(rendered), "rb") as handle:
        seconds = handle.getnframes() / handle.getframerate()
    # DAZ_INPUT in the fake spans 216000..219555 at 60 fps.
    assert seconds == pytest.approx((219555 - 216000) / 60, abs=0.01)
    assert report.timeline_start_frame == 216000
    assert report.timeline_end_frame == 219555


def test_temporary_names_are_unique_and_prefixed():
    names = {scratch_timeline_name() for _ in range(50)}
    assert len(names) == 50
    assert all(name.startswith(SCRATCH_PREFIX) for name in names)
    presets = {restore_preset_name() for _ in range(50)}
    assert len(presets) == 50
    assert all(name.startswith("DAZ_RENDER_RESTORE_") for name in presets)


def test_delete_all_render_jobs_is_never_called(live):
    # The fake raises if it ever is; this test states the intent explicitly.
    _, project, _ = live
    run(live)
    with pytest.raises(AssertionError, match="destroy the user's render queue"):
        project.DeleteAllRenderJobs()
