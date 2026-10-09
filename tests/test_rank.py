import json
import sqlite3
from pathlib import Path

import pytest

from app.db import connect
from app.merge import merge_deal
from app.rank import Ranked, pick_top, rank_deals, score_deal
from factories import make_cluster, make_deal, make_item

TIERS = {"tier_1": ["Sequoia", "a16z"], "tier_2": ["Local Fund"]}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def _add(conn: sqlite3.Connection, n: int = 1, tier: int = 2, **deal: object) -> int:
    items = [make_item("t", source=f"S{i}", tier=tier, url=f"https://x/{deal.get('company_norm', 'a')}/{i}") for i in range(n)]
    return merge_deal(conn, make_deal(**deal), make_cluster("t", "Acme", items=items))[0]


def test_amount_score_is_log_scale(conn: sqlite3.Connection) -> None:
    small = score_deal(conn, conn.execute("SELECT * FROM deals WHERE id=?", (_add(conn, amount_usd=10e6, company_norm="a"),)).fetchone(), TIERS)
    large = score_deal(conn, conn.execute("SELECT * FROM deals WHERE id=?", (_add(conn, amount_usd=1e9, company_norm="b"),)).fetchone(), TIERS)
    assert small.breakdown["amount"] == pytest.approx(1.0) and large.breakdown["amount"] == pytest.approx(3.0)


def test_investor_stage_signal_and_source_points(conn: sqlite3.Connection) -> None:
    deal_id = _add(conn, n=2, tier=1, amount_usd=150e6, round_type="series_c", lead_investors=json.dumps(["Sequoia Capital"]))
    scored = score_deal(conn, conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone(), TIERS)
    assert scored.breakdown["investor"] == 1.0 and scored.breakdown["stage"] == 0.7
    assert scored.breakdown["signals"] == 0.5  # mega-round
    assert scored.breakdown["source"] == pytest.approx(0.5 + 0.1)  # best source tier 1, plus one extra outlet


def test_repeat_raise_and_new_category_signals(conn: sqlite3.Connection) -> None:
    _add(conn, company_norm="acme", announced_date="2026-06-01", amount_usd=5e6, round_type="seed", category="enterprise")
    deal_id = _add(conn, company_norm="acme", announced_date="2026-10-08", amount_usd=5e6, category="robotics")
    scored = score_deal(conn, conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone(), TIERS)
    assert scored.breakdown["signals"] == pytest.approx(0.6)  # repeat raise 0.3 + new category 0.3


def test_new_category_ignored_when_there_is_no_history(conn: sqlite3.Connection) -> None:
    deal_id = _add(conn, amount_usd=5e6)
    assert score_deal(conn, conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone(), TIERS).breakdown["signals"] == 0


def test_rank_orders_excludes_review_items_and_stores_breakdown(conn: sqlite3.Connection) -> None:
    big = _add(conn, company_norm="big", company="Big", amount_usd=300e6)
    small = _add(conn, company_norm="small", company="Small", amount_usd=5e6)
    flagged = _add(conn, company_norm="flag", company="Flag", amount_usd=900e6, needs_review=1)
    ranked = rank_deals(conn, [small, big, flagged], TIERS)
    assert [r.company for r in ranked] == ["Big", "Small"]
    stored = json.loads(conn.execute("SELECT evidence FROM deals WHERE id=?", (big,)).fetchone()["evidence"])
    assert stored["score"]["total"] == ranked[0].score


def _r(name: str, region: str, usd: float, score: float) -> Ranked:
    return Ranked(1, name, region, usd, score, {})


def test_all_us_top_three_gets_the_best_european_deal_swapped_in() -> None:
    ranked = [_r("A", "US", 90e6, 5), _r("B", "US", 80e6, 4), _r("C", "US", 70e6, 3), _r("D", "Asia", 30e6, 2), _r("E", "Europe", 40e6, 1)]
    assert [r.company for r in pick_top(ranked)] == ["A", "B", "D"]


def test_no_swap_when_mix_exists_or_candidate_is_too_small() -> None:
    mixed = [_r("A", "US", 90e6, 5), _r("B", "Europe", 80e6, 4), _r("C", "US", 70e6, 3), _r("D", "Asia", 30e6, 2)]
    assert [r.company for r in pick_top(mixed)] == ["A", "B", "C"]
    tiny = [_r("A", "US", 90e6, 5), _r("B", "US", 80e6, 4), _r("C", "US", 70e6, 3), _r("D", "Asia", 2e6, 2)]
    assert [r.company for r in pick_top(tiny)] == ["A", "B", "C"]
    assert [r.company for r in pick_top(tiny, ensure_regional_mix=False)] == ["A", "B", "C"]
