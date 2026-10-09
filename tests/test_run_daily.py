"""The daily run end to end, with no network: collection and sending are replaced by stand-ins."""

from pathlib import Path
from typing import Any

import pytest

from app import pipeline
from app.collect import SourceResult
from app.config import Settings
from app.db import connect
from app.send import SendError
from factories import NOW


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(
        db_path=tmp_path / "t.db", resend_api_key="re_x", email_from="onboarding@resend.dev", email_to="a@example.com", **extra
    )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replaces the news download (a quiet day) and the email sending; records every email 'sent'."""
    subjects: list[str] = []
    monkeypatch.setattr(pipeline, "collect_all", lambda sources, now, seen: [SourceResult(source=sources[0])])
    monkeypatch.setattr(pipeline, "send_email", lambda settings, subject, html, text: subjects.append(subject) or "id")
    return subjects


def _count(tmp_path: Path, table: str) -> int:
    conn = connect(tmp_path / "t.db")
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_real_run_sends_once_and_is_idempotent(tmp_path: Path, sent: list[str]) -> None:
    first = pipeline.run_daily(_settings(tmp_path), dry_run=False, now=NOW)
    assert first.status == "Email sent to 1 recipient(s)." and len(sent) == 1 and sent[0].endswith("quiet day")
    assert _count(tmp_path, "sent_reports") == 1
    second = pipeline.run_daily(_settings(tmp_path), dry_run=False, now=NOW)
    assert "already sent" in second.status and second.email is None and len(sent) == 1  # never twice


def test_dry_run_sends_nothing_and_records_no_report(tmp_path: Path, sent: list[str]) -> None:
    result = pipeline.run_daily(_settings(tmp_path), dry_run=True, now=NOW)
    assert result.email is not None and "AI Funding" in result.email.html and sent == []
    assert _count(tmp_path, "sent_reports") == 0 and _count(tmp_path, "runs") == 1  # only the usage log row


def test_failed_sending_raises_and_leaves_the_day_open_for_a_retry(tmp_path: Path, sent: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any) -> str:
        raise SendError("Resend refused the email (HTTP 403)")

    monkeypatch.setattr(pipeline, "send_email", refuse)
    with pytest.raises(RuntimeError, match="403"):
        pipeline.run_daily(_settings(tmp_path), dry_run=False, now=NOW)
    assert _count(tmp_path, "sent_reports") == 0  # so the next run tries again
    assert "email" in connect(tmp_path / "t.db").execute("SELECT errors FROM runs").fetchone()["errors"]


def test_quiet_day_note_can_be_switched_off(tmp_path: Path, sent: list[str]) -> None:
    result = pipeline.run_daily(_settings(tmp_path, send_quiet_day_note=False), dry_run=False, now=NOW)
    assert "no email sent" in result.status and sent == [] and _count(tmp_path, "sent_reports") == 0


def test_missing_email_setup_stops_before_any_work(tmp_path: Path, sent: list[str]) -> None:
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        pipeline.run_daily(Settings(db_path=tmp_path / "t.db"), dry_run=False, now=NOW)
    assert not (tmp_path / "t.db").exists()
