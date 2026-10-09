"""`app backfill`: read older articles that the news feeds still list, and store the deals. Sends no email."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.robotparser import RobotFileParser

import httpx

from app.budget import estimate_call_usd
from app.cluster import cluster
from app.collect import USER_AGENT, collect_all, fetch_text_lead, load_seen_urls, store_items
from app.config import Settings, load_sources
from app.db import connect
from app.extract import load_system_prompt, open_cache
from app.llm import make_client
from app.pipeline import FETCH_PAUSE, PAUSE_BETWEEN_AI_CALLS, process_clusters
from app.pipeline_types import Processed
from app.prefilter import prefilter

log = logging.getLogger("app.backfill")

CONFIRM_ABOVE_USD = 5.0  # brief Section 11: estimates above this need a yes


@dataclass
class BackfillResult:
    start: date
    articles_found: int
    oldest_article: str | None
    events: int
    estimated_usd: float
    estimated_minutes: int
    processed: Processed | None  # None when nothing was read (preview only, or nothing to read)
    status: str


def run_backfill(
    settings: Settings, start: date, approve: Callable[[float], bool], dry_run: bool = False, now: datetime | None = None
) -> BackfillResult:
    """Reads every article the feeds still list from `start` on. Estimates the cost first; asks `approve` above $5.

    The daily/monthly spending caps do not apply: the approved estimate is the limit for this run."""
    now = now or datetime.now(timezone.utc)
    if start > now.date():
        raise ValueError("The start date is in the future.")
    window_hours = (now - datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)).total_seconds() / 3600
    conn = connect(settings.db_path)
    sources = load_sources()
    by_name = {s.name: s for s in sources}
    results = collect_all(sources, now, load_seen_urls(conn), window_hours=window_hours)
    items = [i for r in results for i in r.items]
    clusters = cluster(prefilter(items, by_name))

    per_call = estimate_call_usd(settings.llm_model_fast, load_system_prompt())
    estimate = per_call * len(clusters)  # high side: ignores answers already remembered and companies already known
    minutes = round(len(clusters) * (PAUSE_BETWEEN_AI_CALLS + FETCH_PAUSE) / 60)
    oldest = min((i.published_at for i in items), default=None)
    base = dict(
        start=start, articles_found=len(items), oldest_article=oldest.date().isoformat() if oldest else None, events=len(clusters),
        estimated_usd=estimate, estimated_minutes=minutes,
    )  # fmt: skip
    if dry_run or not clusters:
        conn.close()
        status = "Preview only: nothing was read or saved." if dry_run else "Nothing new to read."
        return BackfillResult(**base, processed=None, status=status)  # type: ignore[arg-type]
    if estimate > CONFIRM_ABOVE_USD and not approve(estimate):
        conn.close()
        raise RuntimeError(f"Estimated cost ${estimate:.2f} is above ${CONFIRM_ABOVE_USD:.0f} and was not confirmed. Nothing was read.")

    cache = open_cache(settings.db_path.with_name("ai_cache.db"))
    robots_cache: dict[str, RobotFileParser] = {}
    started = now.isoformat()
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True) as http:

            def get_lead(url: str) -> str:
                text = fetch_text_lead(http, url, robots_cache)
                time.sleep(FETCH_PAUSE)
                return text

            processed = process_clusters(
                conn, cache, clusters, client=make_client(settings.gemini_api_key), model=settings.llm_model_fast,
                budget_usd=max(estimate, per_call), sources=by_name, now=now, get_lead=get_lead,
            )  # fmt: skip
        store_items(conn, items, now)  # also commits the deals; marks the articles as seen
    except Exception as exc:  # noqa: BLE001 - undo everything, then report it loudly
        conn.rollback()
        conn.close()
        cache.close()
        raise RuntimeError(f"Backfill failed, nothing was saved: {type(exc).__name__}: {exc}") from exc

    u = processed.usage
    stats = {"from": start.isoformat(), "articles": len(items), "events": len(clusters), **processed.counts, "deferred": len(processed.deferred)}
    conn.execute(
        "INSERT INTO runs (kind, started_at, finished_at, stats, tokens_in, tokens_out, cached_tokens, cost_usd, errors) "
        "VALUES ('backfill', ?, ?, ?, ?, ?, ?, ?, ?)",
        (started, datetime.now(timezone.utc).isoformat(), json.dumps(stats), u.input_tokens, u.output_tokens, u.cached_tokens, u.cost_usd,
         json.dumps({r.source.name: r.error for r in results if r.error})),
    )  # fmt: skip
    conn.commit()
    conn.close()
    cache.close()
    return BackfillResult(**base, processed=processed, status="Backfill finished.")  # type: ignore[arg-type]
