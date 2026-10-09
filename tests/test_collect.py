from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.collect import (
    RawItem,
    canonical_url,
    clean_text,
    collect_all,
    fetch_source,
    load_seen_urls,
    parse_feed,
    robots_allows,
    store_items,
)
from app.config import Source
from app.db import connect

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"
SOURCE = Source(name="Sample", type="rss", url="https://news.example/feed", tier=2, region="Europe")


def test_clean_text_strips_html_and_spaces() -> None:
    assert clean_text("<p>Hello&nbsp;<b>world</b> &amp; \n all</p>") == "Hello world & all"


def test_canonical_url_drops_tracking_and_fragment() -> None:
    url = "https://News.Example/a?utm_source=x&id=7#comments"
    assert canonical_url(url) == "https://news.example/a?id=7"


def test_parse_feed_keeps_only_recent_dated_items() -> None:
    result = parse_feed(FIXTURE.read_bytes(), SOURCE, NOW)
    titles = [i.title for i in result.items]
    assert titles == ["Acme AI raises $20M Series A led by Example Ventures", "Older story still inside the window"]
    assert result.entries_in_feed == 6
    assert result.in_window == 2
    assert result.undated == 1


def test_parse_feed_cleans_url_and_summary_and_copies_source_info() -> None:
    first = parse_feed(FIXTURE.read_bytes(), SOURCE, NOW).items[0]
    assert first.url == "https://news.example/acme-raises"
    assert first.summary == "Acme AI, a startup building agents, closed a round."
    assert (first.source, first.region, first.tier) == ("Sample", "Europe", 2)


def test_parse_feed_handles_garbage() -> None:
    result = parse_feed(b"this is not a feed", SOURCE, NOW)
    assert result.items == [] and result.entries_in_feed == 0


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, follow_redirects=True)


def test_robots_allows_when_file_missing() -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(404)))
    assert robots_allows(client, "https://news.example/feed", {}) is True


def test_robots_blocks_disallowed_path() -> None:
    robots = "User-agent: *\nDisallow: /feed"
    client = _client(httpx.MockTransport(lambda request: httpx.Response(200, text=robots)))
    assert robots_allows(client, "https://news.example/feed", {}) is False


def test_fetch_source_reports_error_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if "robots" in request.url.path else 500)

    result = fetch_source(_client(httpx.MockTransport(handler)), SOURCE, NOW, {})
    assert result.error is not None and "500" in result.error
    assert result.items == []


def test_fetch_source_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "robots" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    result = fetch_source(_client(httpx.MockTransport(handler)), SOURCE, NOW, {})
    assert result.error is None and len(result.items) == 2


def test_unsupported_source_type_is_reported() -> None:
    edgar = Source(name="SEC", type="edgar", url="https://sec.example", tier=1, region="US")
    result = fetch_source(_client(httpx.MockTransport(lambda r: httpx.Response(404))), edgar, NOW, {})
    assert result.error is not None and "not supported" in result.error


def test_collect_all_skips_disabled_sources() -> None:
    disabled = SOURCE.model_copy(update={"enabled": False})
    assert collect_all([disabled], NOW, set(), delay_seconds=0) == []


def test_store_and_seen_urls_roundtrip(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    item = RawItem("https://a.test/1", "Sample", "T", "S", NOW, "US", 1)
    store_items(conn, [item], NOW)
    store_items(conn, [item], NOW)  # storing twice must not duplicate or fail
    assert load_seen_urls(conn) == {"https://a.test/1"}
    assert conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0] == 1
