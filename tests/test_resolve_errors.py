"""A machine without Resolve must fail cleanly, never with a raw traceback."""

import pytest

from davinci_auto_zoom.cli import main
from davinci_auto_zoom.resolve import session
from davinci_auto_zoom.resolve.loader import ResolveModuleLoadResult

MODULE_PATH = "/opt/resolve/Developer/Scripting/Modules"


@pytest.fixture
def resolve_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session,
        "load_resolve_script_module",
        lambda: ResolveModuleLoadResult(None, (MODULE_PATH,), "boom"),
    )


def test_connect_raises_an_actionable_error(resolve_missing: None) -> None:
    with pytest.raises(session.ResolveUnavailableError) as excinfo:
        session.connect()

    message = str(excinfo.value)
    assert "boom" in message
    assert "RESOLVE_SCRIPT_API" in message
    assert "/opt/resolve/Developer/Scripting/Modules" in message


def test_snapshot_command_exits_with_a_message(
    resolve_missing: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["snapshot"]) == 2
    assert "error:" in capsys.readouterr().out


def test_doctor_reports_disconnected_instead_of_crashing(
    resolve_missing: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor"]) == 2
    assert "connected     : False" in capsys.readouterr().out


def test_unknown_config_path_is_a_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["snapshot", "--config", "/nonexistent/config.toml"]) == 3
    assert "error:" in capsys.readouterr().out
