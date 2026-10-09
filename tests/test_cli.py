from pathlib import Path

import pytest

from app.cli import build_parser, main


def test_help_lists_all_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in ("daily", "monthly", "backfill", "costs"):
        assert command in out


def test_command_creates_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "data" / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    assert main(["costs", "--month", "2026-10"]) == 0
    assert db_file.exists()


def test_month_must_be_valid() -> None:
    parser = build_parser()
    assert parser.parse_args(["monthly", "--month", "2026-10"]).month == "2026-10"
    with pytest.raises(SystemExit):
        parser.parse_args(["monthly", "--month", "2026-13"])


def test_backfill_requires_from_date() -> None:
    parser = build_parser()
    assert parser.parse_args(["backfill", "--from", "2026-10-01"]).from_date == "2026-10-01"
    with pytest.raises(SystemExit):
        parser.parse_args(["backfill"])
