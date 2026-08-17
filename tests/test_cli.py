import json
import shutil

import pytest

from davinci_auto_zoom import cli
from tests.fake_resolve import build_test_project


@pytest.fixture(autouse=True)
def fake_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve, _ = build_test_project()
    monkeypatch.setattr(cli, "connect", lambda: resolve)
    monkeypatch.setattr(cli, "current_project", lambda r: r.GetCurrentProject())


@pytest.mark.parametrize(
    "argv",
    [
        ["snapshot"],
        ["assets"],
        ["compare", "DAZ_INPUT", "DAZ_OUTPUT_MVP"],
    ],
)
def test_commands_emit_valid_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([*argv, "--json"]) == 0
    json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    "argv",
    [
        ["snapshot"],
        ["assets"],
        ["compare", "DAZ_INPUT", "DAZ_OUTPUT_MVP"],
    ],
)
def test_commands_emit_text(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip()


def test_compare_with_unknown_timeline_lists_the_available_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["compare", "DAZ_INPUT", "NOPE"]) == 3
    out = capsys.readouterr().out
    assert "NOPE" in out
    assert "DAZ_OUTPUT_MVP" in out


def test_compare_text_survives_a_timeline_without_cuts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Formatting must not blow up on the None offsets of a cut-less source track."""

    resolve, project = build_test_project()
    project.GetTimelineByIndex(1)._tracks[("video", 1)][2].clear()  # noqa: SLF001
    monkeypatch.setattr(cli, "connect", lambda: resolve)

    assert cli.main(["compare", "DAZ_INPUT", "DAZ_OUTPUT_MVP"]) == 0
    assert "startΔcut=    ?" in capsys.readouterr().out


def test_probe_write_refuses_without_the_confirmation_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "probe-write",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
            "--reference-timeline",
            "DAZ_OUTPUT_MVP",
        ]
    )
    assert exit_code == 3
    assert "--confirm-resolve-write-test" in capsys.readouterr().out


def test_speech_probe_refuses_without_the_confirmation_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "speech-probe",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
        ]
    )
    assert exit_code == 3
    assert "--confirm-resolve-render-test" in capsys.readouterr().out


def test_the_two_confirmation_flags_are_not_interchangeable() -> None:
    """Neither probe may be triggered by muscle memory for the other."""

    with pytest.raises(SystemExit):
        cli.main(
            [
                "speech-probe",
                "--confirm-resolve-write-test",
                "--project",
                "davinci-auto-zoom-test",
                "--source-timeline",
                "DAZ_INPUT",
            ]
        )


def test_speech_file_reports_a_missing_audio_file(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["speech-file", str(tmp_path / "nope.wav")]) == 3
    assert "not found" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_speech_file_analyses_silence_without_resolve(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The engine must be usable with no Resolve session anywhere."""

    import wave

    def refuse() -> None:
        raise AssertionError("speech-file must not connect to Resolve")

    monkeypatch.setattr(cli, "connect", lambda: refuse())

    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000 * 2)

    assert cli.main(["speech-file", str(path), "--fps", "60", "--start-frame", "216000",
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["segments"] == []
    assert payload["timebase"]["start_frame"] == 216000
    assert payload["audio"]["sample_rate"] == 16000
    assert payload["vad"]["settings"]["threshold"] == 0.5


def test_plan_probe_refuses_without_the_confirmation_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "plan-probe",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
            "--config",
            "config.example.toml",
        ]
    )
    assert exit_code == 3
    assert "--confirm-resolve-render-test" in capsys.readouterr().out


def test_plan_probe_refuses_without_asset_timing(capsys: pytest.CaptureFixture[str]) -> None:
    """The planner will not guess how long someone else's assets take to animate."""

    exit_code = cli.main(
        [
            "plan-probe",
            "--confirm-resolve-render-test",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
        ]
    )
    assert exit_code == 3
    assert "[assets.transition_frames]" in capsys.readouterr().out


def test_the_global_help_states_what_each_class_of_command_may_touch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    # Claims that used to be there and are now false.
    assert "Every command in this phase is strictly read-only" not in out
    assert "No command places, moves or deletes a zoom" not in out
    # Which commands are read-only, and that the probes do mutate temporarily.
    assert "doctor, snapshot, assets, compare, speech-file) are strictly read-only" in out
    assert "temporary, opt-in changes" in out
    # Phase 5: one command writes zooms, and it writes them to a new timeline of its own.
    assert "No command modifies an existing timeline of yours" in out
    assert "apply-preview" in out
    assert "DAZ_AUTO_PREVIEW_*" in out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_plan_probe_plans_without_inserting_anything(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole Phase 4 chain on fakes: render -> speech -> cuts -> plan, zero zoom writes."""

    resolve, project = build_test_project(audio_tracks=3)
    storage = tmp_path / "media-storage"
    storage.mkdir()
    resolve.media_storage.volumes = [str(storage)]
    monkeypatch.setattr(cli, "connect", lambda: resolve)
    monkeypatch.setattr(cli, "current_project", lambda r: project)

    config = tmp_path / "config.toml"
    config.write_text(
        "[resolve]\nvoice_audio_track = 1\ncut_reference_video_track = 1\n"
        '[assets]\nx0_to_face_x1 = "FACE_X1"\nface_x1_to_x0 = "X1_TO_X0"\n'
        "[assets.transition_frames]\nx0_to_face_x1 = 15\nface_x1_to_x0 = 15\n",
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "plan-probe",
            "--confirm-resolve-render-test",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
            "--reference-timeline",
            "DAZ_OUTPUT_MVP",
            "--config",
            str(config),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    # The fake renders silence, so there is nothing to zoom on — but the plan must exist,
    # be valid, and carry the signature a future executor would verify.
    assert payload["plan"]["diagnostics"]["valid"] is True
    assert payload["plan"]["diagnostics"]["overlaps"] == []
    source = payload["plan"]["source"]
    assert source["timeline"] == "DAZ_INPUT"
    assert source["cut_reference_video_track"] == 1
    assert source["zoom_video_track"] == 3
    assert source["asset_transition_frames"] == {"x0_to_face_x1": 15, "face_x1_to_x0": 15}
    assert payload["plan_reference"]["reference_timeline"] == "DAZ_OUTPUT_MVP"
    assert exit_code == 0

    # Nothing was appended to any timeline, and the originals are unchanged.
    assert not any("AppendToTimeline" in call for call in project.mutations)
    names = [
        project.GetTimelineByIndex(i).GetName()
        for i in range(1, project.GetTimelineCount() + 1)
    ]
    assert names == ["DAZ_INPUT", "DAZ_OUTPUT_MVP"]


def test_apply_preview_without_its_own_flag_refuses_before_touching_resolve(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The write opt-in is checked first: Resolve is never even contacted."""

    resolve, project = build_test_project(audio_tracks=3)
    monkeypatch.setattr(cli, "connect", lambda: resolve)
    monkeypatch.setattr(cli, "current_project", lambda r: project)
    config = tmp_path / "config.toml"
    config.write_text(
        '[assets]\nx0_to_face_x1 = "FACE_X1"\nface_x1_to_x0 = "X1_TO_X0"\n'
        "[assets.transition_frames]\nx0_to_face_x1 = 15\nface_x1_to_x0 = 15\n", encoding="utf-8"
    )

    exit_code = cli.main(
        [
            "apply-preview",
            "--confirm-resolve-render-test",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
            "--config",
            str(config),
        ]
    )

    assert exit_code == 3
    assert "--confirm-create-preview-timeline" in capsys.readouterr().out
    assert project.mutations == []
    assert project.GetTimelineCount() == 2


def test_apply_preview_needs_the_asset_animation_lengths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "apply-preview",
            "--confirm-create-preview-timeline",
            "--confirm-resolve-render-test",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
        ]
    )
    assert exit_code == 3
    assert "[assets.transition_frames]" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_apply_preview_reports_nothing_to_apply_without_creating_a_timeline(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fake renders silence, so the plan is empty — and an empty plan creates nothing."""

    resolve, project = build_test_project(audio_tracks=3)
    storage = tmp_path / "media-storage"
    storage.mkdir()
    resolve.media_storage.volumes = [str(storage)]
    monkeypatch.setattr(cli, "connect", lambda: resolve)
    monkeypatch.setattr(cli, "current_project", lambda r: project)

    config = tmp_path / "config.toml"
    config.write_text(
        "[resolve]\nvoice_audio_track = 1\ncut_reference_video_track = 1\n"
        '[assets]\nx0_to_face_x1 = "FACE_X1"\nface_x1_to_x0 = "X1_TO_X0"\n'
        "[assets.transition_frames]\nx0_to_face_x1 = 15\nface_x1_to_x0 = 15\n",
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "apply-preview",
            "--confirm-create-preview-timeline",
            "--confirm-resolve-render-test",
            "--project",
            "davinci-auto-zoom-test",
            "--source-timeline",
            "DAZ_INPUT",
            "--config",
            str(config),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["apply"]["nothing_to_apply"] is True
    assert payload["apply"]["succeeded"] is True
    assert not any("AppendToTimeline" in call for call in project.mutations)
    assert [
        project.GetTimelineByIndex(i).GetName()
        for i in range(1, project.GetTimelineCount() + 1)
    ] == ["DAZ_INPUT", "DAZ_OUTPUT_MVP"]
