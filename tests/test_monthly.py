import base64
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app import monthly
from app.config import Settings
from app.db import connect
from app.insights import compute_insights
from app.llm import Usage
from app.narrative import NarrativeResult, template_narrative
from app.render_monthly import render_monthly_email, render_report_html
from app.send import SendError
from factories import NOW
from test_insights import _event, seed_october

PNG = b"\x89PNG-fake"
CHARTS = {"layers": PNG, "global": PNG}


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(db_path=tmp_path / "t.db", resend_api_key="re_x", email_from="onboarding@resend.dev", email_to="a@example.com", **extra)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Seeded October database; files go to a temp folder; AI, browser and email are replaced by stand-ins."""
    conn = connect(tmp_path / "t.db")
    seed_october(conn)
    conn.commit()
    conn.close()
    state: dict[str, Any] = {"emails": [], "ai_calls": 0, "assets": (b"%PDF-fake", CHARTS, "")}

    def fake_ai(client: Any, model: str, i: Any) -> NarrativeResult:
        state["ai_calls"] += 1
        return NarrativeResult(template_narrative(i), "model", Usage(input_tokens=500, output_tokens=100, cost_usd=0.01))

    def fake_send(settings: Settings, subject: str, html: str, text: str, attachments: Any = None) -> str:
        if state.get("send_error"):
            raise SendError("boom")
        state["emails"].append({"subject": subject, "html": html, "text": text, "attachments": attachments})
        return "id"

    monkeypatch.setattr(monthly, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(monthly, "make_client", lambda key: object())
    monkeypatch.setattr(monthly, "generate_narrative", fake_ai)
    monkeypatch.setattr(monthly, "render_assets", lambda path: state["assets"])
    monkeypatch.setattr(monthly, "send_email", fake_send)
    return state


def _rows(tmp_path: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(tmp_path / "t.db")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_dry_run_saves_previews_and_sends_nothing(tmp_path: Path, env: dict[str, Any]) -> None:
    result = monthly.run_monthly(_settings(tmp_path), "2026-10", dry_run=True, now=NOW)
    names = sorted(p.name for p in result.files)
    assert names == ["monthly_2026-10.html", "monthly_2026-10.pdf", "monthly_2026-10_email.html"]
    assert env["emails"] == [] and _rows(tmp_path, "SELECT COUNT(*) FROM sent_reports") == [(0,)]
    assert _rows(tmp_path, "SELECT kind FROM runs") == [("monthly_dry",)]
    assert "data:image/png;base64," in (tmp_path / "out" / "monthly_2026-10_email.html").read_text(encoding="utf-8")


def test_real_run_sends_once_with_pdf_and_chart_attachments(tmp_path: Path, env: dict[str, Any]) -> None:
    result = monthly.run_monthly(_settings(tmp_path), "2026-10", dry_run=False, now=NOW)
    assert "sent" in result.status and (tmp_path / "reports" / "2026-10.html").exists() and (tmp_path / "reports" / "2026-10.pdf").exists()
    (email,) = env["emails"]
    assert "October 2026" in email["subject"]
    assert len(email["attachments"]) == 3 and any(a["filename"].endswith(".pdf") for a in email["attachments"])
    assert base64.b64decode(email["attachments"][0]["content"])  # attachments are base64 text
    assert _rows(tmp_path, "SELECT kind, period FROM sent_reports") == [("monthly", "2026-10")]
    assert _rows(tmp_path, "SELECT cost_usd FROM runs") == [(0.01,)]


def test_second_real_run_for_the_same_month_does_nothing(tmp_path: Path, env: dict[str, Any]) -> None:
    monthly.run_monthly(_settings(tmp_path), "2026-10", dry_run=False, now=NOW)
    again = monthly.run_monthly(_settings(tmp_path), "2026-10", dry_run=False, now=NOW)
    assert "already sent" in again.status and len(env["emails"]) == 1 and env["ai_calls"] == 1


def test_failed_send_is_reported_and_the_month_stays_open(tmp_path: Path, env: dict[str, Any]) -> None:
    env["send_error"] = True
    with pytest.raises(RuntimeError, match="FAILED"):
        monthly.run_monthly(_settings(tmp_path), "2026-10", dry_run=False, now=NOW)
    assert _rows(tmp_path, "SELECT COUNT(*) FROM sent_reports") == [(0,)]
    assert "boom" in _rows(tmp_path, "SELECT errors FROM runs")[0][0]


def test_missing_browser_still_sends_the_email_with_a_note(tmp_path: Path, env: dict[str, Any]) -> None:
    env["assets"] = (None, {}, "PDF and charts unavailable: browser not installed.")
    monthly.run_monthly(_settings(tmp_path), "2026-10", dry_run=False, now=NOW)
    (email,) = env["emails"]
    assert email["attachments"] == [] and "browser not installed" in email["text"]


def test_spending_cap_uses_the_template_without_calling_the_ai(tmp_path: Path, env: dict[str, Any]) -> None:
    result = monthly.run_monthly(_settings(tmp_path, max_daily_llm_usd=0.0), "2026-10", dry_run=True, now=NOW)
    assert result.narrative is not None and result.narrative.source == "template_budget" and env["ai_calls"] == 0


def test_empty_month_needs_no_ai(tmp_path: Path, env: dict[str, Any]) -> None:
    result = monthly.run_monthly(_settings(tmp_path), "2025-01", dry_run=True, now=NOW)
    assert result.narrative is not None and result.narrative.source == "template" and env["ai_calls"] == 0


def test_bad_month_text_is_refused(tmp_path: Path, env: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="2026-10"):
        monthly.run_monthly(_settings(tmp_path), "October", dry_run=True, now=NOW)


def test_real_run_without_email_setup_stops_early(tmp_path: Path, env: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        monthly.run_monthly(Settings(db_path=tmp_path / "t.db"), "2026-10", dry_run=False, now=NOW)


# ---- the report and email pages ----


def _insights(tmp_path: Path, with_event: bool) -> Any:
    conn = connect(tmp_path / "r.db")
    seed_october(conn)
    if with_event:
        _event(conn, "Oldco", "2026-10-03")
    return compute_insights(conn, "2026-10")


def _page(i: Any, **changes: Any) -> str:
    text = template_narrative(i)
    text.update(changes)
    return render_report_html(i, text, "template", 0.0, 1, 12)


def test_report_shows_headwinds_only_when_there_are_events(tmp_path: Path) -> None:
    assert "Oldco" in _page(_insights(tmp_path, True))
    assert 'class="m-headwinds"' not in _page(_insights(tmp_path, False))


def test_report_lists_every_deal_and_flags_the_short_history(tmp_path: Path) -> None:
    page = _page(_insights(tmp_path, False))
    for company in ("Alpha Robotics", "Beta Works", "Gamma Labs"):
        assert company in page
    assert "Hidden Co" not in page and "2 months" in page


def test_largest_rounds_table_has_a_what_it_does_column(tmp_path: Path) -> None:
    page = _page(_insights(tmp_path, False))
    assert "<th>What it does</th>" in page and "Builds warehouse robots." in page
    assert "AI Funding Monthly" in page and "AI Capital" not in page


def test_report_escapes_text_from_the_commentary(tmp_path: Path) -> None:
    page = _page(_insights(tmp_path, False), headline="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_email_embeds_chart_images_by_content_id(tmp_path: Path) -> None:
    i = _insights(tmp_path, False)
    email = render_monthly_email(i, template_narrative(i), "template", 0.0, 1, b"%PDF", CHARTS, "")
    assert email.html.count("cid:") == 2 and len(email.attachments) == 3
    assert "October 2026" in email.subject and "Alpha Robotics" in email.text


def test_email_without_assets_has_no_images(tmp_path: Path) -> None:
    i = _insights(tmp_path, False)
    email = render_monthly_email(i, template_narrative(i), "template", 0.0, 1, None, {}, "Charts unavailable.")
    assert "cid:" not in email.html and email.attachments == [] and "Charts unavailable." in email.text
