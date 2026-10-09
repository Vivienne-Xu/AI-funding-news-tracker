"""Step 1: pull recent items from news feeds. One broken source never stops the run."""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
import trafilatura

from app.config import Source

log = logging.getLogger("app.collect")

USER_AGENT = "AIFundingBrief/0.1 (personal research tool)"
WINDOW_HOURS = 36
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class RawItem:
    url: str
    source: str
    title: str
    summary: str
    published_at: datetime
    region: str
    tier: int


@dataclass
class SourceResult:
    source: Source
    items: list[RawItem] = field(default_factory=list)  # new, in-window items
    entries_in_feed: int = 0
    in_window: int = 0
    already_seen: int = 0
    undated: int = 0
    error: str | None = None


def clean_text(raw: str) -> str:
    """Removes HTML tags and extra whitespace."""
    breaks_as_spaces = re.sub(r"</?(?:p|br|div|li|ul|ol|h[1-6])\b[^>]*>", " ", raw or "", flags=re.IGNORECASE)
    no_tags = re.sub(r"<[^>]+>", "", breaks_as_spaces)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def canonical_url(url: str) -> str:
    """Drops tracking parameters and #fragments so the same article has one address."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def parse_feed(content: bytes | str, source: Source, now: datetime, window_hours: float = WINDOW_HOURS) -> SourceResult:
    """Turns feed text into items published in the last 36 hours (or `window_hours`). No network."""
    result = SourceResult(source=source)
    cutoff = now - timedelta(hours=window_hours)
    feed = feedparser.parse(content)
    result.entries_in_feed = len(feed.entries)
    for entry in feed.entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        url = entry.get("link")
        title = clean_text(entry.get("title", ""))
        if not url or not title:
            continue
        if not stamp:
            result.undated += 1
            continue
        published = datetime(*stamp[:6], tzinfo=timezone.utc)
        if published < cutoff or published > now + timedelta(hours=1):
            continue
        result.in_window += 1
        result.items.append(
            RawItem(
                url=canonical_url(url),
                source=source.name,
                title=title,
                summary=clean_text(entry.get("summary", "")),
                published_at=published,
                region=source.region,
                tier=source.tier,
            )
        )
    return result


def robots_allows(client: httpx.Client, url: str, cache: dict[str, RobotFileParser]) -> bool:
    """Checks the site's robots.txt. 404 means no rules. Other failures raise an error."""
    parts = urlparse(url)
    host = f"{parts.scheme}://{parts.netloc}"
    if host not in cache:
        parser = RobotFileParser()
        response = client.get(f"{host}/robots.txt")
        if response.status_code == 200:
            parser.parse(response.text.splitlines())
        elif 400 <= response.status_code < 500:
            parser.parse([])  # no usable rules: everything allowed
        else:
            raise RuntimeError(f"robots.txt returned HTTP {response.status_code}")
        cache[host] = parser
    return cache[host].can_fetch(USER_AGENT, url)


def fetch_source(
    client: httpx.Client, source: Source, now: datetime, robots_cache: dict[str, RobotFileParser],
    window_hours: float = WINDOW_HOURS,
) -> SourceResult:
    """Downloads one feed. Any problem is recorded in `error`, never raised."""
    try:
        if source.type != "rss":
            raise NotImplementedError(f"source type '{source.type}' is not supported yet")
        if not robots_allows(client, source.url, robots_cache):
            raise PermissionError("robots.txt does not allow fetching this feed")
        response = client.get(source.url)
        response.raise_for_status()
        return parse_feed(response.content, source, now, window_hours)
    except Exception as exc:  # noqa: BLE001 - isolate every source failure
        log.warning("Source %s failed: %s", source.name, exc)
        return SourceResult(source=source, error=f"{type(exc).__name__}: {exc}")


def collect_all(
    sources: list[Source], now: datetime, seen_urls: set[str], delay_seconds: float = 1.0, window_hours: float = WINDOW_HOURS
) -> list[SourceResult]:
    """Fetches every enabled source, one at a time, pausing between requests."""
    results: list[SourceResult] = []
    robots_cache: dict[str, RobotFileParser] = {}
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        for source in (s for s in sources if s.enabled):
            result = fetch_source(client, source, now, robots_cache, window_hours)
            fresh = [i for i in result.items if i.url not in seen_urls]
            result.already_seen = len(result.items) - len(fresh)
            result.items = fresh
            results.append(result)
            time.sleep(delay_seconds)
    return results


def fetch_text_lead(client: httpx.Client, url: str, robots_cache: dict[str, RobotFileParser], max_words: int = 600) -> str:
    """First ~600 words of an article, boilerplate removed. Returns '' on any problem."""
    try:
        if not robots_allows(client, url, robots_cache):
            return ""
        response = client.get(url)
        response.raise_for_status()
        text = trafilatura.extract(response.text, include_comments=False, include_tables=False) or ""
        return " ".join(text.split()[:max_words])
    except Exception as exc:  # noqa: BLE001 - article text is optional; headline and summary still work
        log.warning("Could not read article %s: %s", url, exc)
        return ""


def load_seen_urls(conn: sqlite3.Connection) -> set[str]:
    return {row["url"] for row in conn.execute("SELECT url FROM raw_items")}


def store_items(conn: sqlite3.Connection, items: list[RawItem], fetched_at: datetime) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO raw_items (url, source, title, summary, published_at, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (i.url, i.source, i.title, i.summary, i.published_at.isoformat(), fetched_at.isoformat())
            for i in items
        ],
    )
    conn.commit()
