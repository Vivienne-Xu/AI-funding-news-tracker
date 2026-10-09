"""Step 9a: gathers everything the daily email shows, from the database and run results only.

No AI model is used here: every sentence is a template filled with stored values (brief Section 3.4)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from app.collect import SourceResult
from app.fmt import EVENT_LABELS, ROUND_LABELS, fmt_amount, fmt_usd, plural
from app.pipeline_types import Processed
from app.review_text import plain_review
from app.why_it_matters import why_it_matters

REGIONS = ("US", "Europe", "Asia", "RoW")
ALSO_ANNOUNCED_SHOWN = 4
REVIEW_SHOWN = 5
SOURCES_SHOWN_PER_DEAL = 3
WORDS_PER_MINUTE = 200


@dataclass
class DealView:
    company: str
    region: str
    amount_text: str
    round_label: str
    valuation_text: str | None
    description: str | None
    why: str | None
    investors: str | None
    sources: list[tuple[str, str]]  # (outlet name, link), links come from the database only
    amount_usd: float | None


@dataclass
class EventView:
    label: str
    company: str
    region: str
    summary: str
    prior_funding: str | None
    sources: list[tuple[str, str]]


@dataclass
class RegionShare:
    name: str
    count: int
    amount_text: str
    percent: int  # width of its bar segment


@dataclass
class Digest:
    day: date
    date_text: str
    issue: int
    summary: str
    deal_count: int
    total_text: str
    regions: list[RegionShare]
    top: list[DealView]
    also: list[DealView]
    more_count: int
    events: list[EventView]
    month_to_date: str | None
    sources_ok: int
    sources_total: int
    failed_sources: list[str]
    review: list[str]
    review_total: int
    cost_text: str
    quiet: bool
    read_time: str = "1 min read"
    subject: str = ""


def _sources(conn: sqlite3.Connection, entity: str, entity_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT source, url FROM sources_link WHERE entity_type = ? AND entity_id = ? ORDER BY tier, source", (entity, entity_id)
    ).fetchall()
    seen: set[str] = set()
    chosen = []
    for r in rows:
        if r["source"] not in seen:
            seen.add(r["source"])
            chosen.append((r["source"], r["url"]))
    return chosen[:SOURCES_SHOWN_PER_DEAL]


def _deal_view(conn: sqlite3.Connection, deal_id: int, amounts_today: list[float]) -> DealView:
    row = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    leads = json.loads(row["lead_investors"] or "[]")
    others = json.loads(row["other_investors"] or "[]")
    investors = ("Led by " + ", ".join(leads)) if leads else ("With " + ", ".join(others[:3]) if others else None)
    return DealView(
        company=row["company"], region=row["region"] or "",
        amount_text=fmt_amount(row["amount"], row["currency"], row["amount_usd"]),
        round_label=ROUND_LABELS.get(row["round_type"] or "other", ""),
        valuation_text=f"{fmt_usd(row['valuation_usd'])} valuation" if row["valuation_usd"] else None,
        description=row["description"], why=why_it_matters(conn, row, amounts_today), investors=investors,
        sources=_sources(conn, "deal", deal_id), amount_usd=row["amount_usd"],
    )  # fmt: skip


def _event_view(conn: sqlite3.Connection, event_id: int) -> EventView:
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    prior = f"Previously raised {fmt_usd(row['prior_funding_usd'])}" if row["prior_funding_usd"] else None
    return EventView(
        EVENT_LABELS.get(row["event_type"], row["event_type"]), row["company"], row["region"] or "",
        row["summary"] or "", prior, _sources(conn, "event", event_id),
    )  # fmt: skip


def _region_shares(rows: list[sqlite3.Row]) -> list[RegionShare]:
    total_usd = sum(r["amount_usd"] or 0 for r in rows)
    shares = []
    for region in REGIONS:
        mine = [r for r in rows if r["region"] == region]
        usd = sum(r["amount_usd"] or 0 for r in mine)
        weight = usd / total_usd if total_usd else len(mine) / len(rows)
        shares.append(RegionShare(region, len(mine), fmt_usd(usd) if usd else "–", round(weight * 100)))
    return shares


def _month_to_date(conn: sqlite3.Connection, day: date) -> str | None:
    month_start = day.replace(day=1)
    last_start = (month_start.replace(year=month_start.year - 1, month=12) if month_start.month == 1
                  else month_start.replace(month=month_start.month - 1))  # fmt: skip

    def total(start: date, end: date) -> float:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) FROM deals WHERE needs_review = 0 AND announced_date >= ? AND announced_date < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        return float(row[0])

    so_far = total(month_start, date.max)
    text = f"{day.strftime('%B')} so far: {fmt_usd(so_far)}"
    first = conn.execute("SELECT MIN(announced_date) FROM deals").fetchone()[0]
    previous = total(last_start, month_start)
    covers_last_month = first is not None and first <= last_start.isoformat()  # only compare with a month we fully tracked
    if covers_last_month and previous > 0:
        text += f", {round(so_far / previous * 100)}% of last month's total"
    return text + "."


def build_digest(
    conn: sqlite3.Connection, processed: Processed, results: list[SourceResult], now: datetime, issue: int
) -> Digest:
    """Reads the deals stored by this run (inside its still-open transaction) and fills the email's data."""
    day = now.date()
    ranked_ids = [r.deal_id for r in processed.ranked]
    rows = [conn.execute("SELECT * FROM deals WHERE id = ?", (i,)).fetchone() for i in ranked_ids]
    amounts = [r["amount_usd"] for r in rows if r["amount_usd"]]
    top_ids = [r.deal_id for r in processed.top]
    rest = sorted((r for r in processed.ranked if r.deal_id not in top_ids), key=lambda r: -(r.amount_usd or 0))
    top = [_deal_view(conn, i, amounts) for i in top_ids]
    also = [_deal_view(conn, r.deal_id, amounts) for r in rest[:ALSO_ANNOUNCED_SHOWN]]
    events = [_event_view(conn, i) for i in processed.event_ids if not conn.execute(
        "SELECT needs_review FROM events WHERE id = ?", (i,)).fetchone()[0]]  # fmt: skip

    undisclosed = sum(1 for r in rows if not r["amount_usd"])
    total_text = fmt_usd(sum(amounts))
    summary = ""
    if top:
        lead = max(top, key=lambda d: d.amount_usd or 0)
        outside_us = sum(1 for r in rows if r["region"] != "US")
        summary = f"{plural(len(rows), 'AI funding deal')} today, {total_text} in total. Largest: {lead.company}, {lead.amount_text}."
        if outside_us:
            summary += f" {outside_us} outside the US."
    ok = [r for r in results if not r.error]
    cost = processed.usage.cost_usd
    digest = Digest(
        day=day, date_text=f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}", issue=issue, summary=summary,
        deal_count=len(rows), total_text=total_text + (f" (+{undisclosed} undisclosed)" if undisclosed else ""),
        regions=_region_shares(rows) if rows else [], top=top, also=also, more_count=max(len(rest) - len(also), 0),
        events=events, month_to_date=_month_to_date(conn, day) if rows else None,
        sources_ok=len(ok), sources_total=len(results), failed_sources=[r.source.name for r in results if r.error],
        review=[plain_review(x) for x in processed.review[:REVIEW_SHOWN]], review_total=len(processed.review),
        cost_text="under $0.01" if cost < 0.01 else f"${cost:.2f}", quiet=not rows and not events,
    )  # fmt: skip
    words = len((summary + " ".join(f"{d.company} {d.description or ''}" for d in top + also)).split()) + 60 * len(top)
    digest.read_time = f"{max(1, round(words / WORDS_PER_MINUTE))} min read"
    if digest.quiet:
        digest.subject = f"AI Funding Daily · {digest.date_text} · quiet day"
    else:
        digest.subject = f"AI Funding Daily · {digest.date_text} · {plural(len(rows), 'deal')}, {total_text}"
    return digest
