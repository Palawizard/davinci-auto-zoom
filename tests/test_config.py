import pytest

from davinci_auto_zoom.config import Config


def test_defaults_are_usable_without_a_config_file() -> None:
    config = Config.load(None)
    assert config.asset_bin == "DAVINCI_AUTO_ZOOM"
    # Every transition of the Phase 8 graph, because this user has built every asset.
    assert set(config.assets) == {
        "x0_to_face_x1",
        "face_x1_to_face_x2",
        "face_x2_to_face_x3",
        "face_x1_to_x0",
        "face_x2_to_x0",
        "face_x3_to_x0",
    }


def test_missing_config_file_is_reported_clearly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(FileNotFoundError):
        Config.load(tmp_path / "nope.toml")


def test_user_asset_names_override_defaults(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text(
        '[resolve]\nvoice_audio_track = 2\nasset_bin = "MY_BIN"\n'
        '[assets]\nx0_to_face_x1 = "PUNCH_IN"\nface_x1_to_x0 = "PULL_OUT"\n',
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.voice_audio_track == 2
    assert config.asset_bin == "MY_BIN"
    # A file that names any asset REPLACES the table: this user has no x2/x3 assets, and
    # merging the defaults back in would plan promotions they cannot perform.
    assert config.assets == {"x0_to_face_x1": "PUNCH_IN", "face_x1_to_x0": "PULL_OUT"}


def test_example_config_parses() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    config = Config.load(example)
    assert config.zoom_video_track == 3
    assert config.timeline == "DAZ_INPUT"
    # A1 is the voice track on the current test project (configuration, never detection).
    assert config.voice_audio_track == 1


def test_vad_defaults_are_sileros_own() -> None:
    vad = Config.load(None).vad
    assert (vad.threshold, vad.min_speech_ms, vad.min_silence_ms, vad.speech_pad_ms) == (
        0.5,
        250,
        100,
        30,
    )


def test_vad_settings_are_read_from_the_speech_vad_table(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text(
        '[speech]\nprovider = "silero_vad"\n'
        "[speech.vad]\nthreshold = 0.6\nmin_silence_ms = 250\nspeech_pad_ms = 0\n",
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.vad.threshold == 0.6
    assert config.vad.min_silence_ms == 250
    assert config.vad.speech_pad_ms == 0
    assert config.vad.min_speech_ms == 250  # untouched default


def test_a_misspelled_vad_key_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text("[speech.vad]\nthreshhold = 0.6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="threshhold"):
        Config.load(path)


def test_the_editorial_silence_knob_does_not_leak_into_the_vad(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`reset_after_silence_ms` is a planner concern; putting it in [speech.vad] is a mistake."""

    path = tmp_path / "config.toml"
    path.write_text("[speech.vad]\nreset_after_silence_ms = 650\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reset_after_silence_ms"):
        Config.load(path)


def test_an_unavailable_provider_is_rejected_at_load_time(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text('[speech]\nprovider = "whisper"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="whisper"):
        Config.load(path)


def test_an_invalid_vad_value_is_rejected_at_load_time(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text("[speech.vad]\nthreshold = 1.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="threshold"):
        Config.load(path)


def test_a_zero_or_negative_voice_track_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text("[resolve]\nvoice_audio_track = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="1-based"):
        Config.load(path)


def test_planner_and_asset_timing_are_parsed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text(
        "[resolve]\ncut_reference_video_track = 2\n"
        '[assets]\nx0_to_face_x1 = "FACE_X1"\nface_x1_to_x0 = "X1_TO_X0"\n'
        "[assets.transition_frames]\nx0_to_face_x1 = 15\nface_x1_to_x0 = 15\n"
        "[planner]\nreset_after_silence_ms = 800\nzoom_lead_in_ms = 0\n"
        "cut_snap_lookback_ms = 60\n",
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.cut_reference_video_track == 2
    assert config.planner.reset_after_silence_ms == 800
    assert config.planner.zoom_lead_in_ms == 0
    assert config.planner.cut_snap_lookback_ms == 60
    assert config.asset_timing is not None
    assert config.asset_timing.frames_for("x0_to_face_x1") == 15
    assert config.asset_timing.frames_for("face_x1_to_x0") == 15


def test_asset_timing_has_no_default_so_the_planner_cannot_guess() -> None:
    """15 frames is this user's asset, not a property of the software."""

    assert Config.load(None).asset_timing is None


def test_the_planner_baseline_has_no_lead_in_or_lead_out() -> None:
    planner = Config.load(None).planner
    assert (planner.zoom_lead_in_ms, planner.zoom_lead_out_ms) == (0, 0)
    assert planner.reset_after_silence_ms == 650
    # Asymmetric on purpose, and measured (Phase 6): 350 ms forward, 120 ms = 7 frames back.
    assert (planner.cut_snap_window_ms, planner.cut_snap_lookback_ms) == (350, 120)


def test_the_removed_min_zoom_assumption_is_now_an_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """min_zoom_ms = 450 was an editorial guess; the real minimum is the x1 animation."""

    path = tmp_path / "config.toml"
    path.write_text("[planner]\nmin_zoom_ms = 450\n", encoding="utf-8")
    with pytest.raises(ValueError, match="min_zoom_ms"):
        Config.load(path)


def test_a_half_configured_asset_timing_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text("[assets.transition_frames]\nx0_to_face_x1 = 15\n", encoding="utf-8")
    with pytest.raises(ValueError, match="face_x1_to_x0"):
        Config.load(path)


def test_an_unknown_asset_role_timing_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text(
        "[assets.transition_frames]\n"
        "x0_to_face_x1 = 15\nface_x1_to_x0 = 15\nfacecam_x9 = 15\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="facecam_x9"):
        Config.load(path)


def test_the_example_config_carries_this_projects_asset_timing() -> None:
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    config = Config.load(example)
    assert config.cut_reference_video_track == 1
    assert config.asset_timing is not None
    assert config.asset_timing.to_dict() == {
        "x0_to_face_x1": 15,
        "face_x1_to_face_x2": 15,
        "face_x2_to_face_x3": 15,
        "face_x1_to_x0": 15,
        "face_x2_to_x0": 15,
        "face_x3_to_x0": 15,
    }
    assert config.planner.reset_after_silence_ms == 650
    assert config.assets == {
        "x0_to_face_x1": "FACE_X1",
        "face_x1_to_face_x2": "FACE_X2",
        "face_x2_to_face_x3": "FACE_X3",
        "face_x1_to_x0": "X1_TO_X0",
        "face_x2_to_x0": "X2_TO_X0",
        "face_x3_to_x0": "X3_TO_X0",
    }
    # Calibrated on DAZ_OUTPUT_MVP2 (Phase 8c); see
    # .agent/reports/phase-08c-voice-dynamics-analysis.txt.
    assert config.planner.promotion_min_drop_db == 20
    assert config.planner.promotion_recovery_within_db == 6
    assert config.planner.promotion_min_valley_ms == 30
    assert config.planner.promotion_max_valley_ms == 650
    assert config.planner.promotion_min_hold_ms == 400
    assert config.planner.zoom_cut_snap_window_ms == 120
    assert config.planner.zoom_cut_snap_lookback_ms == 120
    assert config.energy.window_ms == 30
    assert config.energy.hop_ms == 10
    assert config.energy.smoothing_ms == 30
