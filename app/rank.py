"""Step 8: score deals in code (no AI) and pick the top 3 for the daily email."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

MEGA_ROUND_USD = 100_000_000
MIN_SWAP_USD = 10_000_000  # a deal swapped in for regional mix must be at least this big
STAGE_POINTS = {
    "pre_seed": 0.0, "seed": 0.1, "bridge": 0.2, "series_a": 0.3, "series_b": 0.5, "series_c": 0.7,
    "series_d": 0.8, "series_e_plus": 0.9, "growth": 0.9, "other": 0.0,
}  # fmt: skip
INVESTOR_POINTS = {"tier_1": 1.0, "tier_2": 0.5}
SOURCE_POINTS = {1: 0.5, 2: 0.25, 3: 0.0}  # by the best source that reported the deal
EXTRA_OUTLET_POINTS = 0.1  # per additional outlet, up to 3 outlets
NEW_CATEGORY_WINDOW_DAYS = 90


@dataclass(frozen=True)
class Ranked:
    deal_id: int
    company: str
    region: str
    amount_usd: float | None
    score: float
    breakdown: dict[str, float]


def _investor_points(leads: list[str], others: list[str], tiers: dict[str, list[str]]) -> float:
    best = 0.0
    for name in leads + others:
        for tier, points in INVESTOR_POINTS.items():
            if any(re.search(rf"\b{re.escape(v)}\b", name, re.IGNORECASE) for v in tiers.get(tier, [])):
                best = max(best, points)
    return best


def score_deal(conn: sqlite3.Connection, row: sqlite3.Row, tiers: dict[str, list[str]]) -> Ranked:
    """The score is a plain sum, so every point can be explained."""
    amount = row["amount_usd"]
    parts: dict[str, float] = {
        "amount": round(math.log10(amount / 1_000_000), 3) if amount and amount >= 1_000_000 else 0.0,
        "investor": _investor_points(json.loads(row["lead_investors"] or "[]"), json.loads(row["other_investors"] or "[]"), tiers),
        "stage": STAGE_POINTS.get(row["round_type"] or "other", 0.0),
    }  # fmt: skip
    signals = 0.0
    if amount and amount >= MEGA_ROUND_USD:
        signals += 0.5
    earlier = conn.execute(
        "SELECT COUNT(*) FROM deals WHERE company_norm = ? AND id != ? AND announced_date < ?",
        (row["company_norm"], row["id"], row["announced_date"]),
    ).fetchone()[0]
    if earlier:
        signals += 0.3  # repeat raise
    if row["category"] and _is_new_category(conn, row):
        signals += 0.3
    parts["signals"] = signals
    links = conn.execute(
        "SELECT MIN(tier) AS best, COUNT(*) AS n FROM sources_link WHERE entity_type='deal' AND entity_id=?", (row["id"],)
    ).fetchone()
    best_tier = int(links["best"]) if links["best"] is not None else 3
    parts["source"] = SOURCE_POINTS[best_tier] + EXTRA_OUTLET_POINTS * min(max((links["n"] or 1) - 1, 0), 3)
    total = round(sum(parts.values()), 3)
    return Ranked(int(row["id"]), row["company"], row["region"] or "", amount, total, parts)


def _is_new_category(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """No deal in this category in the previous 90 days. Only counts once the database has some history."""
    today = date.fromisoformat(row["announced_date"])
    window_start = (today - timedelta(days=NEW_CATEGORY_WINDOW_DAYS)).isoformat()
    has_history = conn.execute("SELECT 1 FROM deals WHERE announced_date < ? LIMIT 1", (row["announced_date"],)).fetchone()
    if not has_history:
        return False
    seen = conn.execute(
        "SELECT 1 FROM deals WHERE category = ? AND id != ? AND announced_date >= ? AND announced_date < ? LIMIT 1",
        (row["category"], row["id"], window_start, row["announced_date"]),
    ).fetchone()
    return seen is None


def rank_deals(conn: sqlite3.Connection, deal_ids: list[int], tiers: dict[str, list[str]]) -> list[Ranked]:
    """Scores the given deals (flagged `needs_review` ones are left out) and stores the breakdown with each deal."""
    ranked: list[Ranked] = []
    for deal_id in deal_ids:
        row = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
        if row is None or row["needs_review"]:
            continue
        item = score_deal(conn, row, tiers)
        evidence: dict[str, Any] = json.loads(row["evidence"] or "{}")
        evidence["score"] = {"total": item.score, **item.breakdown}
        conn.execute("UPDATE deals SET evidence = ? WHERE id = ?", (json.dumps(evidence), deal_id))
        ranked.append(item)
    ranked.sort(key=lambda r: (-r.score, r.company.casefold()))
    return ranked


def pick_top(ranked: list[Ranked], n: int = 3, ensure_regional_mix: bool = True) -> list[Ranked]:
    """Top n by score. If all are US, swap the weakest for the best Europe/Asia deal of at least $10M."""
    top = ranked[:n]
    if ensure_regional_mix and top and all(r.region == "US" for r in top):
        for candidate in ranked[n:]:
            if candidate.region in ("Europe", "Asia") and (candidate.amount_usd or 0) >= MIN_SWAP_USD:
                return top[:-1] + [candidate]
    return top
