"""SQLite database: plain SQL, no ORM. Tables follow brief Section 5.3."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_items (
    url           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT,
    published_at  TEXT,
    text_lead     TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company         TEXT NOT NULL,
    company_norm    TEXT NOT NULL,
    website         TEXT,
    description     TEXT,
    round_type      TEXT,
    amount          REAL,
    currency        TEXT,
    amount_usd      REAL,
    fx_rate         REAL,
    valuation_usd   REAL,
    lead_investors  TEXT,
    other_investors TEXT,
    announced_date  TEXT,
    hq_city         TEXT,
    hq_country      TEXT,
    region          TEXT,
    category        TEXT,
    sub_sector      TEXT,
    status          TEXT,
    confidence      REAL,
    needs_review    INTEGER NOT NULL DEFAULT 0,
    evidence        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deals_company_norm ON deals(company_norm);
CREATE INDEX IF NOT EXISTS idx_deals_announced ON deals(announced_date);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company          TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    event_date       TEXT,
    summary          TEXT,
    prior_funding_usd REAL,
    region           TEXT,
    evidence         TEXT,
    confidence       REAL,
    needs_review     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources_link (
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER NOT NULL,
    url          TEXT NOT NULL,
    source       TEXT,
    tier         INTEGER,
    PRIMARY KEY (entity_type, entity_id, url)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    stats         TEXT,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    errors        TEXT
);

CREATE TABLE IF NOT EXISTS sent_reports (
    kind     TEXT NOT NULL,
    period   TEXT NOT NULL,
    sent_at  TEXT NOT NULL,
    PRIMARY KEY (kind, period)
);
"""

TABLES = ("raw_items", "deals", "events", "sources_link", "runs", "sent_reports")


def connect(db_path: Path) -> sqlite3.Connection:
    """Opens the database, creating the folder and any missing tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]
