"""Step 7: match a new deal against the database so one real deal is stored once."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

from app.cluster import Cluster, normalise_name

DATE_WINDOW_DAYS = 7
AMOUNT_RATIO_RANGE = (0.5, 2.0)  # outside this, treat as a different round
CONFLICT_TOLERANCE = 0.02  # amounts differing by more than 2% are a conflict
FILL_COLUMNS = (
    "website", "description", "round_type", "amount", "currency", "amount_usd", "fx_rate", "valuation_usd",
    "hq_city", "hq_country", "category", "sub_sector", "status",
)  # fmt: skip
CHECKED_NUMBERS = ("amount_usd", "valuation_usd")


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "//" in url else f"//{url}").netloc.casefold()
    return host.removeprefix("www.") or None


def _near_in_time(a: str, b: str) -> bool:
    return abs(date.fromisoformat(a) - date.fromisoformat(b)) <= timedelta(days=DATE_WINDOW_DAYS)


def _compatible(existing: sqlite3.Row, deal: dict[str, Any]) -> bool:
    if existing["round_type"] and deal["round_type"] and existing["round_type"] != deal["round_type"]:
        return False
    old, new = existing["amount_usd"], deal["amount_usd"]
    if old and new:
        low, high = AMOUNT_RATIO_RANGE
        return low <= new / old <= high
    return True


def find_match(conn: sqlite3.Connection, deal: dict[str, Any]) -> sqlite3.Row | None:
    """Same company (name or website) within 7 days, with a compatible round and amount."""
    start = (date.fromisoformat(deal["announced_date"]) - timedelta(days=DATE_WINDOW_DAYS)).isoformat()
    end = (date.fromisoformat(deal["announced_date"]) + timedelta(days=DATE_WINDOW_DAYS)).isoformat()
    domain = _domain(deal["website"])
    rows = conn.execute("SELECT * FROM deals WHERE announced_date BETWEEN ? AND ?", (start, end)).fetchall()
    for row in rows:
        same_company = row["company_norm"] == deal["company_norm"] or (domain and _domain(row["website"]) == domain)
        if same_company and _compatible(row, deal):
            return row
    return None


def find_deal_for_company(conn: sqlite3.Connection, company_norm: str, announced: str) -> int | None:
    """Used before calling the AI: does a deal for this company already exist this week?"""
    for row in conn.execute("SELECT id, announced_date FROM deals WHERE company_norm = ?", (company_norm,)):
        if _near_in_time(row["announced_date"], announced):
            return int(row["id"])
    return None


def link_sources(conn: sqlite3.Connection, entity_type: str, entity_id: int, cluster: Cluster) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO sources_link (entity_type, entity_id, url, source, tier) VALUES (?, ?, ?, ?, ?)",
        [(entity_type, entity_id, i.url, i.source, i.tier) for i in cluster.items],
    )


def _best_tier(conn: sqlite3.Connection, deal_id: int) -> int:
    row = conn.execute("SELECT MIN(tier) AS t FROM sources_link WHERE entity_type='deal' AND entity_id=?", (deal_id,)).fetchone()
    return int(row["t"]) if row and row["t"] is not None else 3


def insert_deal(conn: sqlite3.Connection, deal: dict[str, Any], cluster: Cluster) -> int:
    columns = ", ".join(deal)
    marks = ", ".join(f":{c}" for c in deal)
    cursor = conn.execute(f"INSERT INTO deals ({columns}) VALUES ({marks})", deal)
    deal_id = int(cursor.lastrowid or 0)
    link_sources(conn, "deal", deal_id, cluster)
    return deal_id


def merge_deal(conn: sqlite3.Connection, deal: dict[str, Any], cluster: Cluster) -> tuple[int, str]:
    """Stores the deal. Returns (deal id, 'inserted' | 'merged' | 'conflict')."""
    existing = find_match(conn, deal)
    if existing is None:
        return insert_deal(conn, deal, cluster), "inserted"

    deal_id = int(existing["id"])
    updates: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    better_source = cluster.best.tier < _best_tier(conn, deal_id)
    for column in FILL_COLUMNS:
        if existing[column] is None and deal[column] is not None:
            updates[column] = deal[column]
    for column in CHECKED_NUMBERS:
        old, new = existing[column], deal[column]
        if old and new and abs(new - old) / old > CONFLICT_TOLERANCE:
            kept = new if better_source else old
            conflicts.append({"field": column, "kept": kept, "other": old if better_source else new})
            updates[column] = kept
    if existing["round_type"] and deal["round_type"] and existing["round_type"] != deal["round_type"]:
        conflicts.append({"field": "round_type", "kept": existing["round_type"], "other": deal["round_type"]})
    for column in ("lead_investors", "other_investors"):
        union = sorted(set(json.loads(existing[column] or "[]")) | set(json.loads(deal[column])), key=str.casefold)
        updates[column] = json.dumps(union)

    evidence = json.loads(existing["evidence"] or "{}")
    if conflicts:
        evidence.setdefault("conflicts", []).extend(conflicts)
        updates["needs_review"] = 1
    updates["evidence"] = json.dumps(evidence)
    assignments = ", ".join(f"{c} = :{c}" for c in updates)
    conn.execute(f"UPDATE deals SET {assignments} WHERE id = :id", {**updates, "id": deal_id})
    link_sources(conn, "deal", deal_id, cluster)
    return deal_id, "conflict" if conflicts else "merged"


def merge_event(conn: sqlite3.Connection, event: dict[str, Any], cluster: Cluster) -> tuple[int, str]:
    """Stores a headwind event once: same company and type within 7 days is the same event."""
    wanted = normalise_name(event["company"])
    for row in conn.execute("SELECT id, company, event_date FROM events WHERE event_type = ?", (event["event_type"],)):
        if normalise_name(row["company"]) == wanted and _near_in_time(row["event_date"], event["event_date"]):
            link_sources(conn, "event", int(row["id"]), cluster)
            return int(row["id"]), "merged"
    columns = ", ".join(event)
    marks = ", ".join(f":{c}" for c in event)
    event_id = int(conn.execute(f"INSERT INTO events ({columns}) VALUES ({marks})", event).lastrowid or 0)
    link_sources(conn, "event", event_id, cluster)
    return event_id, "inserted"
