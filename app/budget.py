"""Spending caps and the `app costs` report. Costs are paid-equivalent (see app/llm.py)."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.llm import cost_usd

EST_INPUT_TOKENS_PER_ITEM = 1_200  # the hard cap per item, excluding the system prompt
EST_OUTPUT_TOKENS_PER_ITEM = 300  # typical answer, thinking included
CHARS_PER_TOKEN = 4


def estimate_call_usd(model: str, system_prompt: str) -> float:
    """Cautious (high-side) price of one extraction, before making the call."""
    input_tokens = EST_INPUT_TOKENS_PER_ITEM + len(system_prompt) // CHARS_PER_TOKEN
    return cost_usd(model, input_tokens, EST_OUTPUT_TOKENS_PER_ITEM)


def _spent(conn: sqlite3.Connection, since: str) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE started_at >= ?", (since,)).fetchone()
    return float(row[0])


def spent_today(conn: sqlite3.Connection, now: datetime) -> float:
    return _spent(conn, now.date().isoformat())


def spent_this_month(conn: sqlite3.Connection, now: datetime) -> float:
    return _spent(conn, now.strftime("%Y-%m-01"))


def room_left(conn: sqlite3.Connection, now: datetime, daily_cap: float, monthly_cap: float) -> float:
    """How much more may be spent today, given earlier runs today and this month."""
    return max(0.0, min(daily_cap - spent_today(conn, now), monthly_cap - spent_this_month(conn, now)))


def costs_report(conn: sqlite3.Connection, month: str, monthly_cap: float) -> str:
    """AI usage for one month ('YYYY-MM'), one line per run."""
    rows = conn.execute(
        "SELECT kind, started_at, tokens_in, tokens_out, cost_usd FROM runs "
        "WHERE substr(started_at, 1, 7) = ? AND (tokens_in > 0 OR tokens_out > 0 OR cost_usd > 0) ORDER BY id",
        (month,),
    ).fetchall()
    total = sum(r["cost_usd"] for r in rows)
    lines = [
        f"AI usage for {month} (paid-equivalent prices; on the Gemini free tier the real bill is $0)",
        f"  Total: ${total:.4f} of the ${monthly_cap:.2f} monthly cap, {len(rows)} run(s)",
        "",
    ]
    if not rows:
        lines.append("No AI calls recorded for this month.")
    for r in rows:
        lines.append(
            f"  {r['started_at'][:16]}  {r['kind']:<10} in {r['tokens_in']:>7,}  out {r['tokens_out']:>6,}  ${r['cost_usd']:.4f}"
        )
    return "\n".join(lines)
