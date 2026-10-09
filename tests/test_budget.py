import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.budget import costs_report, estimate_call_usd, room_left
from app.db import connect

MODEL = "gemini-3.5-flash-lite"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def _run(conn: sqlite3.Connection, started: str, cost: float, kind: str = "daily") -> None:
    conn.execute(
        "INSERT INTO runs (kind, started_at, tokens_in, tokens_out, cost_usd) VALUES (?, ?, 1000, 200, ?)", (kind, started, cost)
    )


def test_estimate_is_positive_and_grows_with_the_prompt() -> None:
    assert 0 < estimate_call_usd(MODEL, "x" * 400) < estimate_call_usd(MODEL, "x" * 40_000)


def test_room_left_is_limited_by_the_tighter_cap(conn: sqlite3.Connection) -> None:
    assert room_left(conn, NOW, 0.25, 3.00) == 0.25
    _run(conn, "2026-10-08T06:00:00+00:00", 0.10)
    _run(conn, "2026-10-01T06:00:00+00:00", 2.85)
    assert room_left(conn, NOW, 0.25, 3.00) == pytest.approx(0.05)  # month cap is the tighter one
    _run(conn, "2026-10-08T07:00:00+00:00", 1.00)
    assert room_left(conn, NOW, 0.25, 3.00) == 0.0


def test_last_months_spending_does_not_count(conn: sqlite3.Connection) -> None:
    _run(conn, "2026-09-30T06:00:00+00:00", 2.99)
    assert room_left(conn, NOW, 0.25, 3.00) == 0.25


def test_costs_report_lists_runs_for_the_month(conn: sqlite3.Connection) -> None:
    _run(conn, "2026-10-08T06:00:00+00:00", 0.0123)
    _run(conn, "2026-09-08T06:00:00+00:00", 0.5, kind="daily_dry")
    text = costs_report(conn, "2026-10", 3.00)
    assert "$0.0123" in text and "1 run(s)" in text and "0.5000" not in text
    assert "No AI calls" in costs_report(conn, "2026-08", 3.00)
