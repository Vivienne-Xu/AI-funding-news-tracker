"""The daily pipeline: collect, filter, cluster, read with AI, verify, merge, rank, write the email, send."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.robotparser import RobotFileParser

import httpx
from google import genai

from app.budget import estimate_call_usd, room_left
from app.cluster import Cluster, cluster
from app.collect import (
    USER_AGENT, SourceResult, collect_all, fetch_text_lead, load_seen_urls, store_items,
)  # fmt: skip
from app.config import (
    Settings, Source, load_aliases, load_country_regions, load_fx_rates, load_investor_tiers, load_sources,
)  # fmt: skip
from app.db import connect
from app.digest import build_digest
from app.enrich import build_deal, build_event, company_key
from app.extract import extract_item, load_system_prompt, open_cache
from app.llm import make_client
from app.merge import find_deal_for_company, link_sources, merge_deal, merge_event
from app.parse import parse_headline
from app.pipeline_types import Processed
from app.prefilter import Candidate, prefilter
from app.rank import pick_top, rank_deals
from app.render import RenderedEmail, render_daily
from app.send import check_email_settings, recipients, send_email
from app.verify import verify

log = logging.getLogger("app.pipeline")

PAUSE_BETWEEN_AI_CALLS = 4.0  # seconds; keeps us under the free tier's per-minute limits
FETCH_PAUSE = 1.0  # seconds between article downloads


@dataclass
class DailyResult:
    results: list[SourceResult]
    candidates: list[Candidate]
    clusters: list[Cluster]
    processed: Processed
    dry_run: bool
    email: RenderedEmail | None = None
    status: str = ""  # what happened to the email, in plain words


def _priority(c: Cluster, sources: dict[str, Source], rates: dict[str, float]) -> tuple[int, int, float]:
    """Most outlets first, then the most trusted source, then the biggest amount in the headline."""
    hint = parse_headline(c.best.title, sources[c.best.source].bare_dollar).money
    size = hint.amount * rates.get(hint.currency, 1.0) if hint else 0.0
    return (-len(c.items), c.best.tier, -size)


def process_clusters(
    conn: sqlite3.Connection,
    cache: sqlite3.Connection,
    clusters: list[Cluster],
    *,
    client: genai.Client,
    model: str,
    budget_usd: float,
    sources: dict[str, Source],
    now: datetime,
    get_lead: Callable[[str], str],
    pause_seconds: float = PAUSE_BETWEEN_AI_CALLS,
) -> Processed:
    """Steps 4-8 for a list of clusters. `conn` should be inside a transaction the caller can undo."""
    aliases, rates = load_aliases(), load_fx_rates()
    countries, tiers = load_country_regions(), load_investor_tiers()
    system_prompt = load_system_prompt()
    per_call = estimate_call_usd(model, system_prompt)
    out = Processed()
    spent = 0.0

    for c in sorted(clusters, key=lambda c: _priority(c, sources, rates)):
        label = c.company or c.best.title[:70]
        announced = c.best.published_at.date().isoformat()
        if c.kind == "funding" and c.company:
            known_id = find_deal_for_company(conn, company_key(c.company, aliases), announced)
            if known_id is not None:
                link_sources(conn, "deal", known_id, c)
                out.known.append(label)
                continue
        if spent + per_call > budget_usd:
            out.deferred.append(f"{label} ({c.best.source})")
            continue

        source = sources[c.best.source]
        lead = get_lead(c.best.url)
        outcome = extract_item(
            client, model, system_prompt, c.best.title, c.best.summary, lead,
            parse_headline(c.best.title, source.bare_dollar), cache=cache, url=c.best.url,
            bare_dollar=source.bare_dollar,
        )  # fmt: skip
        out.usage += outcome.usage
        spent += outcome.usage.cost_usd
        if outcome.from_cache:
            out.cache_hits += 1
        else:
            time.sleep(pause_seconds)
        if outcome.extraction is None:
            out.counts["failed"] += 1
            out.review.append(f"{label} ({c.best.source}): {outcome.error}")
            continue

        verified = verify(outcome.extraction, outcome.source_text, c.best.published_at.date(), now.date(), source.bare_dollar)
        if verified.kind == "neither":
            out.counts["not_relevant"] += 1
            continue
        if verified.data["company"] is None or (verified.kind == "headwind" and verified.data["event_type"] is None):
            out.counts["unusable"] += 1
            out.review.append(f"{label} ({c.best.source}): not stored, {', '.join(verified.reasons)}")
            continue
        if verified.kind == "funding":
            deal = build_deal(verified, c, outcome.source_text, source.bare_dollar, aliases, rates, countries, now)
            deal_id, action = merge_deal(conn, deal, c)
            out.counts[f"deal_{action}"] += 1
            if deal_id not in out.deal_ids:  # two clusters can describe one deal
                out.deal_ids.append(deal_id)
            if deal["needs_review"]:
                reasons = json.loads(deal["evidence"])["reasons"]
                out.review.append(f"{deal['company']} ({c.best.source}): {', '.join(reasons)}")
        else:
            event = build_event(verified, c, outcome.source_text, source.bare_dollar, rates, countries)
            event_id, action = merge_event(conn, event, c)
            out.counts[f"event_{action}"] += 1
            if event_id not in out.event_ids:
                out.event_ids.append(event_id)
            if event["needs_review"]:
                out.review.append(f"{event['company']} ({c.best.source}): {', '.join(verified.reasons)}")

    out.ranked = rank_deals(conn, out.deal_ids, tiers)
    out.top = pick_top(out.ranked)
    return out


def _already_sent(conn: sqlite3.Connection, day: str) -> bool:
    return conn.execute("SELECT 1 FROM sent_reports WHERE kind = 'daily' AND period = ?", (day,)).fetchone() is not None


def run_daily(settings: Settings, dry_run: bool, now: datetime | None = None) -> DailyResult:
    """Real run: stores deals and sends the email. Dry run: builds the email, changes nothing, sends nothing.

    If sending fails, everything is undone, so the next run starts from the same news (answers are cached)."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    if not dry_run:
        check_email_settings(settings)  # fail before any AI work is paid for
    conn = connect(settings.db_path)
    if not dry_run and _already_sent(conn, day):
        conn.close()
        return DailyResult([], [], [], Processed(), dry_run, status=f"Today's briefing ({day}) was already sent. Nothing to do.")
    cache = open_cache(settings.db_path.with_name("ai_cache.db"))
    sources = load_sources()
    by_name = {s.name: s for s in sources}
    started = now.isoformat()

    results = collect_all(sources, now, load_seen_urls(conn))
    items = [i for r in results for i in r.items]
    candidates = prefilter(items, by_name)
    clusters = cluster(candidates)

    robots_cache: dict[str, RobotFileParser] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True) as http:

        def get_lead(url: str) -> str:
            text = fetch_text_lead(http, url, robots_cache)
            time.sleep(FETCH_PAUSE)
            return text

        budget = room_left(conn, now, settings.max_daily_llm_usd, settings.max_monthly_llm_usd)
        client = make_client(settings.gemini_api_key) if clusters else None
        processed = process_clusters(
            conn, cache, clusters, client=client, model=settings.llm_model_fast, budget_usd=budget,
            sources=by_name, now=now, get_lead=get_lead,
        )  # type: ignore[arg-type]  # client is only None when there are no clusters to read

    issue = conn.execute("SELECT COUNT(*) FROM sent_reports WHERE kind = 'daily'").fetchone()[0] + 1
    digest = build_digest(conn, processed, results, now, issue)
    email = render_daily(digest)
    error: str | None = None
    if dry_run:
        conn.rollback()  # undo every change: a dry run leaves the database untouched
        status = "Dry run: nothing was sent or saved."
    else:
        try:
            if digest.quiet and not settings.send_quiet_day_note:
                status = "Quiet day: no email sent (SEND_QUIET_DAY_NOTE is false)."
            else:
                send_email(settings, email.subject, email.html, email.text)
                conn.execute("INSERT INTO sent_reports (kind, period, sent_at) VALUES ('daily', ?, ?)", (day, started))
                status = f"Email sent to {len(recipients(settings))} recipient(s)."
            store_items(conn, items, now)  # also commits the deals; marks these articles as seen
        except Exception as exc:  # noqa: BLE001 - any failure: undo, log, then report it loudly below
            conn.rollback()
            error = f"{type(exc).__name__}: {exc}"
            status = f"FAILED, nothing was saved: {error}"

    stats = {
        "new_items": len(items), "kept": len(candidates), "clusters": len(clusters),
        "dry_run": dry_run, **processed.counts, "deferred": len(processed.deferred),
        "already_known": len(processed.known), "cache_hits": processed.cache_hits, "email": status,
    }  # fmt: skip
    errors = {r.source.name: r.error for r in results if r.error} | ({"email": error} if error else {})
    u = processed.usage
    conn.execute(
        "INSERT INTO runs (kind, started_at, finished_at, stats, tokens_in, tokens_out, cached_tokens, cost_usd, errors) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("daily_dry" if dry_run else "daily", started, datetime.now(timezone.utc).isoformat(), json.dumps(stats),
         u.input_tokens, u.output_tokens, u.cached_tokens, u.cost_usd, json.dumps(errors)),
    )  # fmt: skip
    conn.commit()
    conn.close()
    cache.close()
    if error:
        raise RuntimeError(status)
    return DailyResult(results, candidates, clusters, processed, dry_run, email, status)
