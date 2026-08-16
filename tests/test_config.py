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
