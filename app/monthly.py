"""The monthly pipeline: numbers, commentary, report (HTML, PDF), email."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.assets import render_assets
from app.budget import room_left
from app.config import PROJECT_ROOT, Settings, load_sources
from app.db import connect
from app.insights import Insights, compute_insights
from app.llm import Usage, cost_usd, make_client
from app.narrative import PROMPT_PATH, MAX_OUTPUT_TOKENS, NarrativeResult, generate_narrative, narrative_facts, template_narrative
from app.narrative_check import facts_to_prompt
from app.render import RenderedEmail
from app.render_monthly import render_monthly_email, render_report_html
from app.send import check_email_settings, recipients, send_email

CHARS_PER_TOKEN = 4


@dataclass
class MonthlyResult:
    insights: Insights | None
    narrative: NarrativeResult | None
    email: RenderedEmail | None
    files: list[Path]
    status: str


def _plan_narrative(conn: sqlite3.Connection, settings: Settings, i: Insights, now: datetime) -> NarrativeResult:
    """Writes the commentary with the AI model, unless there is nothing to write about or the spending cap forbids it."""
    if not i.deal_count:
        return NarrativeResult(template_narrative(i), "template")
    prompt_chars = len(PROMPT_PATH.read_text(encoding="utf-8")) + len(facts_to_prompt(narrative_facts(i)))
    estimate = 2 * cost_usd(settings.llm_model_writer, prompt_chars // CHARS_PER_TOKEN, MAX_OUTPUT_TOKENS)  # two drafts at most
    if estimate > room_left(conn, now, settings.max_daily_llm_usd, settings.max_monthly_llm_usd):
        return NarrativeResult(template_narrative(i), "template_budget")
    return generate_narrative(make_client(settings.gemini_api_key), settings.llm_model_writer, i)


def _preview_email_html(email: RenderedEmail) -> str:
    """For the saved preview only: embeds the chart pictures so a browser can show them (mail apps use the attachments)."""
    html = email.html
    for att in email.attachments:
        if att.get("content_id"):
            html = html.replace(f"cid:{att['content_id']}", f"data:image/png;base64,{att['content']}")
    return html


def run_monthly(settings: Settings, month: str | None, dry_run: bool, now: datetime | None = None) -> MonthlyResult:
    """Real run: saves the report to reports/ and sends the email once per month. Dry run: previews in out/, sends nothing."""
    now = now or datetime.now(timezone.utc)
    month = month or now.strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must look like 2026-10")
    if not dry_run:
        check_email_settings(settings)
    conn = connect(settings.db_path)
    if not dry_run and conn.execute("SELECT 1 FROM sent_reports WHERE kind = 'monthly' AND period = ?", (month,)).fetchone():
        conn.close()
        return MonthlyResult(None, None, None, [], f"The {month} report was already sent. Nothing to do.")

    insights = compute_insights(conn, month)
    narrative = _plan_narrative(conn, settings, insights, now)
    issue = conn.execute("SELECT COUNT(*) FROM sent_reports WHERE kind = 'monthly'").fetchone()[0] + 1
    cost = narrative.usage.cost_usd
    out_dir = PROJECT_ROOT / ("out" if dry_run else "reports")
    out_dir.mkdir(exist_ok=True)
    stem = f"monthly_{month}" if dry_run else month
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(
        render_report_html(insights, narrative.text, narrative.source, cost, issue, sum(s.enabled for s in load_sources())), encoding="utf-8"
    )
    pdf, charts, asset_note = render_assets(html_path)
    files = [html_path]
    if pdf is not None:
        (out_dir / f"{stem}.pdf").write_bytes(pdf)
        files.append(out_dir / f"{stem}.pdf")
    email = render_monthly_email(insights, narrative.text, narrative.source, cost, issue, pdf, charts, asset_note)

    error: str | None = None
    if dry_run:
        preview = out_dir / f"{stem}_email.html"
        preview.write_text(_preview_email_html(email), encoding="utf-8")
        files.append(preview)
        status = "Dry run: nothing was sent."
    else:
        try:
            send_email(settings, email.subject, email.html, email.text, attachments=email.attachments)
            conn.execute("INSERT INTO sent_reports (kind, period, sent_at) VALUES ('monthly', ?, ?)", (month, now.isoformat()))
            status = f"Email sent to {len(recipients(settings))} recipient(s)."
        except Exception as exc:  # noqa: BLE001 - log the failure, leave the month open for a retry, report it below
            error = f"{type(exc).__name__}: {exc}"
            status = f"FAILED: {error}"
    u: Usage = narrative.usage
    stats = {"month": month, "narrative": narrative.source, "narrative_problems": narrative.problems[:10], "pdf": pdf is not None,
             "asset_note": asset_note, "deals": insights.deal_count, "status": status}  # fmt: skip
    conn.execute(
        "INSERT INTO runs (kind, started_at, finished_at, stats, tokens_in, tokens_out, cached_tokens, cost_usd, errors) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("monthly_dry" if dry_run else "monthly", now.isoformat(), datetime.now(timezone.utc).isoformat(), json.dumps(stats),
         u.input_tokens, u.output_tokens, u.cached_tokens, u.cost_usd, json.dumps({"email": error} if error else {})),
    )  # fmt: skip
    conn.commit()
    conn.close()
    if error:
        raise RuntimeError(status)
    return MonthlyResult(insights, narrative, email, files, status)
