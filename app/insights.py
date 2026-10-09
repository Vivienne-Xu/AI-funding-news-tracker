"""Monthly numbers, all computed in code from the database (brief Section 9.3). No AI model, no network."""

from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.config import load_categories
from app.fmt import EVENT_LABELS, ROUND_LABELS, fmt_usd

REGIONS = ("US", "Europe", "Asia", "RoW")
MEGA_ROUND_USD = 100_000_000
UNICORN_USD = 1_000_000_000
TOP_N_INVESTORS = 8
TOP_N_ROUNDS = 5
MIN_HISTORY_FOR_HEADWIND_TREND = 6  # months of data needed before comparing headwinds with earlier months
MIN_SUBSECTOR_USD = 10_000_000


@dataclass
class Insights:
    month: str
    month_label: str
    history_months: int
    deal_count: int
    undisclosed_count: int
    total_usd: float
    prev_total_usd: float | None
    mom_change_pct: float | None
    median_round_usd: float | None
    top5_share_pct: float | None
    top2_share_pct: float | None
    mega_rounds: int
    new_unicorns: int
    layers: list[dict[str, Any]] = field(default_factory=list)
    regions: list[dict[str, Any]] = field(default_factory=list)
    region_trend: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    top_investors: list[dict[str, Any]] = field(default_factory=list)
    largest_rounds: list[dict[str, Any]] = field(default_factory=list)
    repeat_raisers: list[dict[str, Any]] = field(default_factory=list)
    step_ups: list[dict[str, Any]] = field(default_factory=list)
    fastest_subsectors: list[dict[str, Any]] = field(default_factory=list)
    headwinds: list[dict[str, Any]] = field(default_factory=list)
    headwind_counts: dict[str, int] = field(default_factory=dict)
    headwind_prior_avg: dict[str, float] | None = None
    deals: list[dict[str, Any]] = field(default_factory=list)  # every deal of the month, for the searchable table
    notable_facts: list[str] = field(default_factory=list)


def month_bounds(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    start, end = date(year, mon, 1), date(year + (mon == 12), mon % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def shift_month(month: str, delta: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:7]) - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _deals(conn: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    start, end = month_bounds(month)
    return conn.execute(
        "SELECT * FROM deals WHERE needs_review = 0 AND announced_date >= ? AND announced_date < ? "
        "ORDER BY COALESCE(amount_usd, 0) DESC, company",
        (start, end),
    ).fetchall()


def _sum(rows: list[sqlite3.Row]) -> float:
    return float(sum(r["amount_usd"] or 0 for r in rows))


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _layer(row: sqlite3.Row, layers: dict[str, str]) -> str:
    return layers.get(row["category"] or "", "Unclassified")


def _layer_shares(rows: list[sqlite3.Row], layers: dict[str, str]) -> list[dict[str, Any]]:
    without_top2 = rows[2:]  # rows are sorted by amount, largest first
    names = list(dict.fromkeys(list(layers.values()) + ["Unclassified"]))
    total_all, total_ex = _sum(rows), _sum(without_top2)
    out = []
    for name in names:
        all_usd = _sum([r for r in rows if _layer(r, layers) == name])
        ex_usd = _sum([r for r in without_top2 if _layer(r, layers) == name])
        if all_usd or name != "Unclassified":
            out.append({"layer": name, "all_usd": all_usd, "all_pct": _pct(all_usd, total_all),
                        "ex_top2_usd": ex_usd, "ex_top2_pct": _pct(ex_usd, total_ex)})  # fmt: skip
    return out


def _region_rows(rows: list[sqlite3.Row], prev_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    total, prev_total = _sum(rows), _sum(prev_rows)
    out = []
    for region in REGIONS:
        mine = [r for r in rows if r["region"] == region]
        usd = _sum(mine)
        prev_pct = _pct(_sum([r for r in prev_rows if r["region"] == region]), prev_total) if prev_total else None
        biggest = max(mine, key=lambda r: r["amount_usd"] or 0, default=None)
        out.append({
            "region": region, "usd": usd, "pct": _pct(usd, total), "count": len(mine),
            "prev_pct": prev_pct, "shift_pts": round(_pct(usd, total) - prev_pct, 1) if prev_pct is not None else None,
            "largest_company": biggest["company"] if biggest and biggest["amount_usd"] else None,
            "largest_usd": biggest["amount_usd"] if biggest and biggest["amount_usd"] else None,
        })  # fmt: skip
    return out


def _region_trend(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    first = shift_month(month, -11)
    start, _ = month_bounds(first)
    _, end = month_bounds(month)
    rows = conn.execute(
        "SELECT substr(announced_date, 1, 7) AS m, region, SUM(amount_usd) AS usd FROM deals "
        "WHERE needs_review = 0 AND announced_date >= ? AND announced_date < ? GROUP BY m, region",
        (start, end),
    ).fetchall()
    by_month: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_month.setdefault(r["m"], {"month": r["m"], **{x: 0.0 for x in REGIONS}})
        if r["region"] in REGIONS:
            by_month[r["m"]][r["region"]] = float(r["usd"] or 0)
    return [by_month[m] for m in sorted(by_month)]


def _stages(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    total, count = _sum(rows), len(rows)
    out = []
    for key, label in ROUND_LABELS.items():
        mine = [r for r in rows if (r["round_type"] or "other") == key]
        if mine:
            out.append({"stage": label or "Other", "capital_pct": _pct(_sum(mine), total), "deal_pct": _pct(len(mine), count), "count": len(mine)})
    return out


def _investors(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    book: dict[str, dict[str, Any]] = {}
    for r in rows:
        leads, others = json.loads(r["lead_investors"] or "[]"), json.loads(r["other_investors"] or "[]")
        for name in dict.fromkeys(leads + others):
            entry = book.setdefault(name.casefold(), {"name": name, "deals": 0, "led_usd": 0.0, "led_deals": 0})
            entry["deals"] += 1
            if name in leads:
                entry["led_deals"] += 1
                entry["led_usd"] += r["amount_usd"] or 0
    ranked = sorted(book.values(), key=lambda e: (-e["deals"], -e["led_usd"], e["name"].casefold()))
    return ranked[:TOP_N_INVESTORS]


def _source_of(conn: sqlite3.Connection, entity: str, entity_id: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT source, url FROM sources_link WHERE entity_type = ? AND entity_id = ? ORDER BY tier LIMIT 1", (entity, entity_id)
    ).fetchone()
    return (row["source"], row["url"]) if row else ("", "")


def _repeats(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repeats, step_ups = [], []
    for r in rows:
        earlier = conn.execute(
            "SELECT valuation_usd FROM deals WHERE company_norm = ? AND id != ? AND needs_review = 0 AND announced_date < ? "
            "ORDER BY announced_date DESC", (r["company_norm"], r["id"], r["announced_date"]),
        ).fetchall()  # fmt: skip
        if earlier:
            repeats.append({"company": r["company"], "raises_tracked": len(earlier) + 1})
            before = next((e["valuation_usd"] for e in earlier if e["valuation_usd"]), None)
            if before and r["valuation_usd"] and r["valuation_usd"] / before >= 1.2:
                step_ups.append({"company": r["company"], "multiple": round(r["valuation_usd"] / before, 1)})
    return repeats, sorted(step_ups, key=lambda s: -s["multiple"])


def _subsectors(conn: sqlite3.Connection, month: str, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    start, _ = month_bounds(shift_month(month, -3))
    this_start, _ = month_bounds(month)
    prior = conn.execute(
        "SELECT lower(sub_sector) AS s, SUM(amount_usd) AS usd FROM deals WHERE needs_review = 0 AND sub_sector IS NOT NULL "
        "AND announced_date >= ? AND announced_date < ? GROUP BY s", (start, this_start),
    ).fetchall()  # fmt: skip
    averages = {r["s"]: float(r["usd"] or 0) / 3 for r in prior}
    now: dict[str, float] = {}
    for r in rows:
        if r["sub_sector"]:
            now[r["sub_sector"].lower()] = now.get(r["sub_sector"].lower(), 0) + (r["amount_usd"] or 0)
    out = [{"sub_sector": s, "usd": usd, "avg3m_usd": averages[s], "growth_pct": round((usd / averages[s] - 1) * 100)}
           for s, usd in now.items() if usd >= MIN_SUBSECTOR_USD and averages.get(s)]  # fmt: skip
    return sorted(out, key=lambda x: -x["growth_pct"])[:3]


def _headwinds(conn: sqlite3.Connection, month: str, history_months: int) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, float] | None]:
    start, end = month_bounds(month)
    events = conn.execute(
        "SELECT * FROM events WHERE needs_review = 0 AND event_date >= ? AND event_date < ? ORDER BY event_date, company", (start, end)
    ).fetchall()
    ledger = []
    for e in events:
        source, url = _source_of(conn, "event", e["id"])
        ledger.append({
            "type": EVENT_LABELS.get(e["event_type"], e["event_type"]), "company": e["company"], "region": e["region"] or "",
            "date": e["event_date"], "summary": e["summary"] or "", "prior_funding_usd": e["prior_funding_usd"], "source": source, "url": url,
        })  # fmt: skip
    counts: dict[str, int] = {}
    for item in ledger:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    prior = None
    if history_months >= MIN_HISTORY_FOR_HEADWIND_TREND and ledger:
        first, _ = month_bounds(shift_month(month, -6))
        rows = conn.execute(
            "SELECT event_type, COUNT(*) AS n FROM events WHERE needs_review = 0 AND event_date >= ? AND event_date < ? GROUP BY event_type",
            (first, start),
        ).fetchall()  # fmt: skip
        prior = {EVENT_LABELS.get(r["event_type"], r["event_type"]): round(r["n"] / 6, 1) for r in rows}
    return ledger, counts, prior


def _facts(i: Insights) -> list[str]:
    facts = []
    if i.largest_rounds:
        top = i.largest_rounds[0]
        facts.append(f"Largest round: {top['company']} raised {fmt_usd(top['usd'])} ({top['round']}, {top['region']}).")
    if i.top2_share_pct is not None:
        facts.append(f"The two largest rounds make up {i.top2_share_pct:g}% of capital raised.")
    if i.top5_share_pct is not None:
        facts.append(f"The five largest rounds make up {i.top5_share_pct:g}% of capital raised.")
    for r in i.regions:
        if r["shift_pts"] is not None and abs(r["shift_pts"]) >= 5:
            facts.append(f"{r['region']} share of capital moved {r['shift_pts']:+g} percentage points versus the previous month.")
    if i.step_ups:
        facts.append(f"{i.step_ups[0]['company']} raised at a valuation {i.step_ups[0]['multiple']:g}x its previous tracked valuation.")
    return facts


def compute_insights(conn: sqlite3.Connection, month: str) -> Insights:
    layers = load_categories()
    rows = _deals(conn, month)
    prev_rows = _deals(conn, shift_month(month, -1))
    known = [r for r in rows if r["amount_usd"]]
    total, prev_total = _sum(rows), _sum(prev_rows)
    first = conn.execute("SELECT MIN(announced_date) FROM deals WHERE needs_review = 0").fetchone()[0]
    history = (int(month[:4]) * 12 + int(month[5:7])) - (int(first[:4]) * 12 + int(first[5:7])) + 1 if first else 0
    year, mon = int(month[:4]), int(month[5:7])
    unicorns = {
        r["company_norm"] for r in rows if (r["valuation_usd"] or 0) >= UNICORN_USD and not conn.execute(
            "SELECT 1 FROM deals WHERE company_norm = ? AND id != ? AND announced_date < ? AND valuation_usd >= ?",
            (r["company_norm"], r["id"], r["announced_date"], UNICORN_USD)).fetchone()  # fmt: skip
    }
    repeats, step_ups = _repeats(conn, rows)
    ledger, counts, prior = _headwinds(conn, month, max(history, 0))
    insights = Insights(
        month=month, month_label=date(year, mon, 1).strftime("%B %Y"), history_months=max(history, 0), deal_count=len(rows),
        undisclosed_count=len(rows) - len(known), total_usd=total, prev_total_usd=prev_total or None,
        mom_change_pct=round((total / prev_total - 1) * 100, 1) if prev_total else None,
        median_round_usd=statistics.median([r["amount_usd"] for r in known]) if known else None,
        top5_share_pct=_pct(_sum(known[:5]), total) if known else None, top2_share_pct=_pct(_sum(known[:2]), total) if known else None,
        mega_rounds=sum(1 for r in known if r["amount_usd"] >= MEGA_ROUND_USD), new_unicorns=len(unicorns),
        layers=_layer_shares(rows, layers), regions=_region_rows(rows, prev_rows), region_trend=_region_trend(conn, month),
        stages=_stages(rows), top_investors=_investors(rows), repeat_raisers=repeats, step_ups=step_ups,
        fastest_subsectors=_subsectors(conn, month, rows), headwinds=ledger, headwind_counts=counts, headwind_prior_avg=prior,
    )  # fmt: skip
    for r in rows:
        source, url = _source_of(conn, "deal", r["id"])
        insights.deals.append({
            "company": r["company"], "layer": _layer(r, layers), "region": r["region"] or "", "round": ROUND_LABELS.get(r["round_type"] or "other", "") or "Other",
            "usd": r["amount_usd"], "valuation_usd": r["valuation_usd"], "date": r["announced_date"], "source": source, "url": url,
            "investors": ", ".join(json.loads(r["lead_investors"] or "[]")), "description": r["description"] or "",
        })  # fmt: skip
    insights.largest_rounds = [d for d in insights.deals if d["usd"]][:TOP_N_ROUNDS]
    insights.notable_facts = _facts(insights)
    return insights
