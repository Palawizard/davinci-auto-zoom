import json

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
