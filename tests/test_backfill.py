from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app import backfill
from app.collect import SourceResult
from app.config import Settings, load_sources
from app.db import connect
from app.pipeline_types import Processed
from factories import NOW, make_item

SOURCE = next(s for s in load_sources() if s.enabled)
TITLES = ["Acme raises $20M Series A led by Sequoia", "Zeta Robotics raises $30M Series B led by Accel"]
SUMMARY = "An AI startup building agents announced new funding."


def _items(titles: list[str]) -> list[Any]:
    return [
        make_item(t, source=SOURCE.name, region=SOURCE.region, tier=SOURCE.tier, summary=SUMMARY, url=f"https://news.example/{n}")
        for n, t in enumerate(titles)
    ]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"titles": TITLES, "processed": 0, "fail": False, "per_call": 0.001}

    def fake_collect(sources: Any, now: Any, seen: Any, delay_seconds: float = 1.0, window_hours: float = 36) -> list[SourceResult]:
        state["window_hours"] = window_hours
        return [SourceResult(source=SOURCE, items=_items(state["titles"]))]

    def fake_process(conn: Any, cache: Any, clusters: Any, **kwargs: Any) -> Processed:
        state["processed"] += 1
        state["budget"] = kwargs["budget_usd"]
        if state["fail"]:
            raise ValueError("model exploded")
        conn.execute("INSERT INTO sent_reports VALUES ('marker', 'x', 'x')")  # a change that must be undone on failure
        return Processed()

    monkeypatch.setattr(backfill, "collect_all", fake_collect)
    monkeypatch.setattr(backfill, "process_clusters", fake_process)
    monkeypatch.setattr(backfill, "make_client", lambda key: object())
    monkeypatch.setattr(backfill, "estimate_call_usd", lambda model, prompt: state["per_call"])
    monkeypatch.setattr(backfill, "FETCH_PAUSE", 0)
    return state


def _settings(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "t.db")


def _count(tmp_path: Path, table: str) -> int:
    conn = connect(tmp_path / "t.db")
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_preview_shows_the_estimate_and_changes_nothing(tmp_path: Path, env: dict[str, Any]) -> None:
    result = backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: True, dry_run=True, now=NOW)
    assert result.events == 2 and result.articles_found == 2 and result.estimated_usd == pytest.approx(0.002)
    assert result.oldest_article == "2026-10-08" and "Preview" in result.status
    assert env["processed"] == 0 and _count(tmp_path, "raw_items") == 0


def test_the_look_back_window_runs_from_the_start_date(tmp_path: Path, env: dict[str, Any]) -> None:
    backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: True, dry_run=True, now=NOW)
    assert env["window_hours"] == pytest.approx(7.5 * 24)  # 1 Oct 00:00 to 8 Oct 12:00


def test_real_run_marks_articles_as_seen_and_logs_the_run(tmp_path: Path, env: dict[str, Any]) -> None:
    result = backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: True, now=NOW)
    assert result.processed is not None and result.status == "Backfill finished."
    assert _count(tmp_path, "raw_items") == 2 and _count(tmp_path, "sent_reports") == 1  # the marker row was kept
    assert _count(tmp_path, "runs") == 1
    assert env["budget"] == pytest.approx(0.002)  # the approved estimate is the limit for this run


def test_backfill_has_no_email_code() -> None:
    assert not hasattr(backfill, "send_email") and not hasattr(backfill, "render_daily")


def test_estimate_above_five_dollars_needs_a_yes(tmp_path: Path, env: dict[str, Any]) -> None:
    env["per_call"] = 3.0  # two events: $6
    with pytest.raises(RuntimeError, match="not confirmed"):
        backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: False, now=NOW)
    assert env["processed"] == 0 and _count(tmp_path, "raw_items") == 0


def test_estimate_above_five_dollars_runs_once_approved(tmp_path: Path, env: dict[str, Any]) -> None:
    env["per_call"] = 3.0
    asked: list[float] = []
    result = backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: asked.append(e) or True, now=NOW)
    assert asked == [pytest.approx(6.0)] and result.processed is not None


def test_cheap_backfill_does_not_ask(tmp_path: Path, env: dict[str, Any]) -> None:
    def never(_: float) -> bool:
        raise AssertionError("should not ask below $5")

    assert backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), never, now=NOW).processed is not None


def test_nothing_new_to_read(tmp_path: Path, env: dict[str, Any]) -> None:
    env["titles"] = []
    result = backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: True, now=NOW)
    assert result.status == "Nothing new to read." and env["processed"] == 0


def test_failure_undoes_everything(tmp_path: Path, env: dict[str, Any]) -> None:
    env["fail"] = True
    with pytest.raises(RuntimeError, match="nothing was saved"):
        backfill.run_backfill(_settings(tmp_path), date(2026, 10, 1), lambda e: True, now=NOW)
    assert _count(tmp_path, "raw_items") == 0 and _count(tmp_path, "sent_reports") == 0 and _count(tmp_path, "runs") == 0


def test_start_date_in_the_future_is_refused(tmp_path: Path, env: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="future"):
        backfill.run_backfill(_settings(tmp_path), date(2026, 11, 1), lambda e: True, now=NOW)
