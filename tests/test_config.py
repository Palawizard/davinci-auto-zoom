import pytest

from davinci_auto_zoom.config import Config


def test_defaults_are_usable_without_a_config_file() -> None:
    config = Config.load(None)
    assert config.asset_bin == "DAVINCI_AUTO_ZOOM"
    assert set(config.assets) == {"facecam_x1", "reset_x0"}


def test_missing_config_file_is_reported_clearly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(FileNotFoundError):
        Config.load(tmp_path / "nope.toml")


def test_user_asset_names_override_defaults(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "config.toml"
    path.write_text(
        '[resolve]\nvoice_audio_track = 2\nasset_bin = "MY_BIN"\n'
        '[assets]\nfacecam_x1 = "PUNCH_IN"\n',
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.voice_audio_track == 2
    assert config.asset_bin == "MY_BIN"
    # Overridden role plus the untouched default.
    assert config.assets == {"facecam_x1": "PUNCH_IN", "reset_x0": "FACE_X0_SMOOTH"}


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
