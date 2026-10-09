"""The 'Why it matters' line: fixed templates filled from the database (brief Section 8.2). No AI model."""

from __future__ import annotations

import sqlite3
from datetime import date

from app.fmt import REGION_LABELS, ROUND_LABELS, plural

MIN_COMPARABLE_DEALS = 3  # a "largest ever" claim needs at least this many other comparable deals to mean anything
MEGA_ROUND_USD = 100_000_000
MIN_STEP_UP = 1.2


def _earlier_raises(conn: sqlite3.Connection, row: sqlite3.Row) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT announced_date, valuation_usd FROM deals WHERE company_norm = ? AND id != ? AND needs_review = 0 "
        "AND announced_date < ? ORDER BY announced_date DESC",
        (row["company_norm"], row["id"], row["announced_date"]),
    ).fetchall()


def _tracked_since(conn: sqlite3.Connection, start: str) -> bool:
    """True if the database has deals from before `start`, so a claim about the period since then has full data."""
    first = conn.execute("SELECT MIN(announced_date) FROM deals").fetchone()[0]
    return first is not None and first <= start


def _largest_of_its_kind(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    label, region = ROUND_LABELS.get(row["round_type"] or "other"), REGION_LABELS.get(row["region"] or "")
    if not (row["amount_usd"] and label and region and _tracked_since(conn, row["announced_date"][:4] + "-01-01")):
        return None
    others = conn.execute(
        "SELECT amount_usd FROM deals WHERE id != ? AND round_type = ? AND region = ? AND needs_review = 0 "
        "AND amount_usd IS NOT NULL AND substr(announced_date, 1, 4) = substr(?, 1, 4)",
        (row["id"], row["round_type"], row["region"], row["announced_date"]),
    ).fetchall()
    if len(others) >= MIN_COMPARABLE_DEALS and all(row["amount_usd"] > o[0] for o in others):
        return f"Largest {label} in {region} this year"
    return None


def _second_raise(earlier: list[sqlite3.Row], row: sqlite3.Row) -> str | None:
    if len(earlier) != 1:
        return None
    days = (date.fromisoformat(row["announced_date"]) - date.fromisoformat(earlier[0]["announced_date"])).days
    months = round(days / 30.4)
    return f"Second raise in {plural(months, 'month')}" if months >= 1 else None


def _first_mega_round(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    region = REGION_LABELS.get(row["region"] or "")
    if not (row["amount_usd"] and row["amount_usd"] >= MEGA_ROUND_USD and region):
        return None
    if not _tracked_since(conn, row["announced_date"][:7] + "-01"):
        return None  # we have not tracked the whole month, so "first this month" could be wrong
    other = conn.execute(
        "SELECT 1 FROM deals WHERE id != ? AND region = ? AND needs_review = 0 AND amount_usd >= ? "
        "AND substr(announced_date, 1, 7) = substr(?, 1, 7) LIMIT 1",
        (row["id"], row["region"], MEGA_ROUND_USD, row["announced_date"]),
    ).fetchone()
    return None if other else f"First $100M+ round in {region} this month"


def _valuation_step_up(earlier: list[sqlite3.Row], row: sqlite3.Row) -> str | None:
    previous = next((e["valuation_usd"] for e in earlier if e["valuation_usd"]), None)
    if previous and row["valuation_usd"] and row["valuation_usd"] / previous >= MIN_STEP_UP:
        return f"Valuation up {row['valuation_usd'] / previous:.1f}× since last round"
    return None


def why_it_matters(conn: sqlite3.Connection, row: sqlite3.Row, amounts_today: list[float]) -> str | None:
    """The first template that applies, in the brief's order. None if none applies (the line is then left out)."""
    earlier = _earlier_raises(conn, row)
    amount = row["amount_usd"]
    largest_today = len(amounts_today) >= 2 and amount is not None and amount >= max(amounts_today)
    return (
        _largest_of_its_kind(conn, row)
        or _second_raise(earlier, row)
        or _first_mega_round(conn, row)
        or _valuation_step_up(earlier, row)
        or ("Largest round today" if largest_today else None)
    )
