import json
import sqlite3
from pathlib import Path

import pytest

from app.db import connect
from app.merge import find_deal_for_company, merge_deal, merge_event
from factories import make_cluster, make_deal, make_item


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def _cluster(source: str = "TechCrunch", tier: int = 2, url: str = "https://a.example/1"):
    return make_cluster("t", "Acme", items=[make_item("Acme raises", source=source, tier=tier, url=url)])


def test_new_deal_is_inserted_with_its_sources(conn: sqlite3.Connection) -> None:
    deal_id, action = merge_deal(conn, make_deal(), _cluster())
    assert action == "inserted"
    assert conn.execute("SELECT url FROM sources_link WHERE entity_id=?", (deal_id,)).fetchone()["url"] == "https://a.example/1"


def test_same_deal_from_another_outlet_is_merged_and_fills_gaps(conn: sqlite3.Connection) -> None:
    first, _ = merge_deal(conn, make_deal(hq_city=None, lead_investors=json.dumps(["Sequoia"])), _cluster())
    second, action = merge_deal(
        conn,
        make_deal(hq_city="Paris", announced_date="2026-10-10", lead_investors=json.dumps(["Accel"])),
        _cluster("Sifted", 2, "https://b.example/2"),
    )
    assert (second, action) == (first, "merged")
    row = conn.execute("SELECT * FROM deals").fetchone()
    assert row["hq_city"] == "Paris" and json.loads(row["lead_investors"]) == ["Accel", "Sequoia"]
    assert conn.execute("SELECT COUNT(*) FROM sources_link").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 1


def test_different_round_or_far_apart_dates_are_separate_deals(conn: sqlite3.Connection) -> None:
    merge_deal(conn, make_deal(), _cluster())
    _, a = merge_deal(conn, make_deal(round_type="seed"), _cluster(url="https://x/2"))
    _, b = merge_deal(conn, make_deal(announced_date="2026-10-20"), _cluster(url="https://x/3"))
    _, c = merge_deal(conn, make_deal(amount_usd=100e6), _cluster(url="https://x/4"))  # 5x bigger: another round
    assert (a, b, c) == ("inserted", "inserted", "inserted")


def test_same_website_matches_even_if_the_name_differs(conn: sqlite3.Connection) -> None:
    merge_deal(conn, make_deal(website="https://www.acme.ai"), _cluster())
    _, action = merge_deal(conn, make_deal(company_norm="acme labs", website="acme.ai"), _cluster(url="https://x/2"))
    assert action == "merged"


def test_small_amount_difference_is_not_a_conflict(conn: sqlite3.Connection) -> None:
    merge_deal(conn, make_deal(amount_usd=20e6), _cluster())
    _, action = merge_deal(conn, make_deal(amount_usd=20.2e6), _cluster(url="https://x/2"))
    assert action == "merged"


def test_conflicting_amounts_are_flagged_and_keep_the_first_unless_source_is_better(conn: sqlite3.Connection) -> None:
    deal_id, _ = merge_deal(conn, make_deal(amount_usd=20e6), _cluster(tier=2))
    _, action = merge_deal(conn, make_deal(amount_usd=25e6), _cluster("Blog", 3, "https://x/2"))
    row = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    assert action == "conflict" and row["amount_usd"] == 20e6 and row["needs_review"] == 1
    assert json.loads(row["evidence"])["conflicts"][0]["other"] == 25e6

    _, action = merge_deal(conn, make_deal(amount_usd=30e6), _cluster("Reuters", 1, "https://x/3"))
    row = conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    assert action == "conflict" and row["amount_usd"] == 30e6  # the more trusted source wins, and it is recorded
    assert len(json.loads(row["evidence"])["conflicts"]) == 2


def test_find_deal_for_company_uses_seven_day_window(conn: sqlite3.Connection) -> None:
    deal_id, _ = merge_deal(conn, make_deal(), _cluster())
    assert find_deal_for_company(conn, "acme", "2026-10-12") == deal_id
    assert find_deal_for_company(conn, "acme", "2026-10-30") is None
    assert find_deal_for_company(conn, "other", "2026-10-08") is None


def test_events_are_stored_once(conn: sqlite3.Connection) -> None:
    event = {
        "company": "Oldco Inc", "event_type": "shutdown", "event_date": "2026-10-08", "summary": "s",
        "prior_funding_usd": None, "region": "US", "evidence": "{}", "confidence": 0.9, "needs_review": 0,
    }  # fmt: skip
    first, a = merge_event(conn, event, _cluster())
    second, b = merge_event(conn, event | {"company": "Oldco", "event_date": "2026-10-09"}, _cluster(url="https://x/2"))
    assert (a, b, first == second) == ("inserted", "merged", True)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
