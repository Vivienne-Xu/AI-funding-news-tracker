import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.config import Source
from app.db import connect
from app.extract import open_cache
from app.pipeline import process_clusters
from factories import NOW, make_cluster, make_item
from test_extract import MODEL, FakeClient, reply

SOURCES = {
    "TechCrunch": Source(name="TechCrunch", type="rss", url="https://x", tier=2, region="US"),
    "Sifted": Source(name="Sifted", type="rss", url="https://y", tier=2, region="Europe"),
}
TITLE = "Acme raises $20M Series A led by Sequoia"
SUMMARY = "Acme, an AI agents startup, raised $20 million."
FUNDING = {
    "kind": "funding", "company": "Acme", "round_type": "series_a", "amount": 20000000, "currency": "USD",
    "lead_investors": ["Sequoia"], "category": "enterprise", "status": "confirmed", "confidence": 0.9,
    "evidence": {"company": "Acme", "round_type": "Series A", "amount": "$20M", "lead_investors": "led by Sequoia"},
}  # fmt: skip


@pytest.fixture
def dbs(tmp_path: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    return connect(tmp_path / "t.db"), open_cache(tmp_path / "cache.db")


def _cluster(title: str = TITLE, company: str | None = "Acme", source: str = "TechCrunch", kind: str = "funding"):
    item = make_item(title, source=source, region=SOURCES[source].region, summary=SUMMARY)
    return make_cluster(title, company, kind=kind, items=[item])


def run(dbs: tuple[sqlite3.Connection, sqlite3.Connection], clusters: list[Any], client: FakeClient, budget: float = 1.0):
    return process_clusters(
        dbs[0], dbs[1], clusters, client=client, model=MODEL, budget_usd=budget,  # type: ignore[arg-type]
        sources=SOURCES, now=NOW, get_lead=lambda url: "", pause_seconds=0,
    )  # fmt: skip


def test_funding_cluster_becomes_a_ranked_deal_with_measured_cost(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    out = run(dbs, [_cluster()], FakeClient(reply(FUNDING)))
    assert out.counts["deal_inserted"] == 1 and [r.company for r in out.top] == ["Acme"]
    assert out.usage.cost_usd == pytest.approx(0.0008) and not out.review
    row = dbs[0].execute("SELECT * FROM deals").fetchone()
    assert row["amount_usd"] == 20e6 and row["region"] == "US" and row["announced_date"] == "2026-10-08"
    assert "amount" in json.loads(row["evidence"])["quotes"]


def test_same_question_twice_is_answered_from_the_cache_for_free(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    client = FakeClient(reply(FUNDING))
    run(dbs, [_cluster()], client)
    dbs[0].execute("DELETE FROM deals")  # as after a dry run: database undone, cache kept
    again = run(dbs, [_cluster()], client)
    assert client.calls == 1 and again.cache_hits == 1 and again.usage.cost_usd == 0
    assert again.counts["deal_inserted"] == 1


def test_two_clusters_for_one_deal_are_stored_and_ranked_once(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    other_outlet = _cluster("Acme secures $20M Series A", "Acme Labs", source="Sifted")  # different guessed name
    out = run(dbs, [_cluster(), other_outlet], FakeClient(reply(FUNDING), reply(FUNDING)))
    assert out.counts["deal_inserted"] == 1 and out.counts["deal_merged"] == 1
    assert len(out.ranked) == 1 and dbs[0].execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 1


def test_company_already_in_database_skips_the_ai_call(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    run(dbs, [_cluster()], FakeClient(reply(FUNDING)))
    client = FakeClient()
    second = run(dbs, [_cluster("Acme lands $20M from Sequoia", source="Sifted")], client)
    assert client.calls == 0 and second.known == ["Acme"]
    assert dbs[0].execute("SELECT COUNT(*) FROM sources_link").fetchone()[0] == 2  # new outlet still recorded


def test_budget_cap_defers_clusters_and_lists_them(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    client = FakeClient()
    out = run(dbs, [_cluster()], client, budget=0.0)
    assert client.calls == 0 and len(out.deferred) == 1 and out.deferred[0].startswith("Acme")


def test_biggest_story_is_read_first_when_budget_is_tight(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    two_outlets = make_cluster(
        "Bigco raises $5M", "Bigco",
        items=[make_item("Bigco raises $5M", "TechCrunch", url="https://a/1"), make_item("Bigco raises $5M", "Sifted", region="Europe", url="https://a/2")],
    )  # fmt: skip
    one_outlet = _cluster("Smallco raises $900M", "Smallco")
    from app.budget import estimate_call_usd
    from app.extract import load_system_prompt

    room_for_one = estimate_call_usd(MODEL, load_system_prompt()) + 0.0001  # a second call would not fit
    out = run(dbs, [one_outlet, two_outlets], FakeClient(reply({"kind": "neither", "confidence": 0.9})), budget=room_for_one)
    assert out.counts["not_relevant"] == 1 and out.deferred == ["Smallco (TechCrunch)"]


def test_failed_ai_call_goes_to_review_and_does_not_stop_the_run(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    client = FakeClient(reply("x", finish="MAX_TOKENS"), reply("y", finish="MAX_TOKENS"), reply(FUNDING))
    out = run(dbs, [_cluster("Foo raises $900M", "Foo"), _cluster()], client)
    assert out.counts["failed"] == 1 and out.counts["deal_inserted"] == 1
    assert out.review[0].startswith("Foo") and "ai_call_failed" in out.review[0]


def test_unsupported_values_flag_the_deal_and_keep_it_out_of_the_ranking(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    invented = FUNDING | {"evidence": {"company": "Acme"}}  # amount etc. have no quote: dropped
    out = run(dbs, [_cluster()], FakeClient(reply(invented)))
    row = dbs[0].execute("SELECT * FROM deals").fetchone()
    assert row["needs_review"] == 1 and row["amount"] is None
    assert out.ranked == [] and "no_evidence:amount" in out.review[0]


def test_headwind_is_stored_as_an_event(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    title = "Oldco shuts down after failing to raise"
    answer = {
        "kind": "headwind", "company": "Oldco", "event_type": "shutdown", "summary": "Oldco shuts down.",
        "confidence": 0.9, "evidence": {"company": "Oldco", "event_type": "shuts down"},
    }  # fmt: skip
    out = run(dbs, [_cluster(title, "Oldco", kind="headwind")], FakeClient(reply(answer)))
    assert out.counts["event_inserted"] == 1
    assert dbs[0].execute("SELECT event_type FROM events").fetchone()["event_type"] == "shutdown"


def test_item_without_company_is_listed_not_stored(dbs: tuple[sqlite3.Connection, sqlite3.Connection]) -> None:
    out = run(dbs, [_cluster()], FakeClient(reply({"kind": "funding", "confidence": 0.9, "evidence": {}})))
    assert out.counts["unusable"] == 1 and "no_company" in out.review[0]
    assert dbs[0].execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 0
