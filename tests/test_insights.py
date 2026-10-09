import json
import sqlite3
from pathlib import Path

import pytest

from app.db import connect
from app.insights import compute_insights, month_bounds, shift_month
from app.merge import insert_deal, merge_event
from factories import make_cluster, make_deal, make_item


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def add(conn: sqlite3.Connection, **deal: object) -> int:
    name = str(deal.get("company_norm", "acme"))
    cluster = make_cluster("t", name, items=[make_item("t", source="TechCrunch", url=f"https://news.example/{name}-{deal.get('announced_date', '')}")])
    return insert_deal(conn, make_deal(**deal), cluster)


def seed_october(conn: sqlite3.Connection) -> None:
    add(conn, company="Alpha Robotics", company_norm="alpha", announced_date="2026-10-05", amount_usd=100e6, round_type="series_c",
        category="robotics", region="US", valuation_usd=1.5e9, lead_investors=json.dumps(["Sequoia Capital"]), sub_sector="robotics", description="Builds warehouse robots.")  # fmt: skip
    add(conn, company="Beta Works", company_norm="beta", announced_date="2026-10-06", amount_usd=50e6, round_type="series_b",
        category="enterprise", region="Europe", lead_investors=json.dumps(["Accel"]), other_investors=json.dumps(["Sequoia Capital"]))  # fmt: skip
    add(conn, company="Gamma Labs", company_norm="gamma", announced_date="2026-10-07", amount_usd=10e6, round_type="seed",
        category="frontier_labs", region="Asia")  # fmt: skip
    add(conn, company="Hidden Co", company_norm="hidden", announced_date="2026-10-07", amount_usd=900e6, needs_review=1)  # must be ignored
    add(conn, company="Old Month", company_norm="old", announced_date="2026-09-10", amount_usd=100e6, region="US")


def test_month_helpers_cross_year_boundaries() -> None:
    assert month_bounds("2026-12") == ("2026-12-01", "2027-01-01")
    assert shift_month("2026-01", -1) == "2025-12" and shift_month("2026-10", -11) == "2025-11"


def test_headline_numbers(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    i = compute_insights(conn, "2026-10")
    assert i.month_label == "October 2026" and i.deal_count == 3 and i.total_usd == 160e6
    assert i.prev_total_usd == 100e6 and i.mom_change_pct == 60.0
    assert i.median_round_usd == 50e6 and i.mega_rounds == 1 and i.new_unicorns == 1
    assert i.top5_share_pct == 100.0 and i.top2_share_pct == 93.8
    assert i.history_months == 2  # September and October


def test_layers_compare_all_deals_with_excluding_the_top_two(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    layers = {x["layer"]: x for x in compute_insights(conn, "2026-10").layers}
    assert layers["Physical AI"]["all_pct"] == 62.5 and layers["Physical AI"]["ex_top2_usd"] == 0
    assert layers["Models"]["ex_top2_pct"] == 100.0  # only the smallest deal is left


def test_regions_include_shift_against_previous_month(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    regions = {x["region"]: x for x in compute_insights(conn, "2026-10").regions}
    assert regions["US"]["pct"] == 62.5 and regions["US"]["prev_pct"] == 100.0 and regions["US"]["shift_pts"] == -37.5
    assert regions["Europe"]["largest_company"] == "Beta Works" and regions["RoW"]["count"] == 0


def test_region_trend_lists_available_months_only(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    trend = compute_insights(conn, "2026-10").region_trend
    assert [t["month"] for t in trend] == ["2026-09", "2026-10"] and trend[1]["Europe"] == 50e6


def test_stages_and_investors(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    i = compute_insights(conn, "2026-10")
    assert {s["stage"]: s["capital_pct"] for s in i.stages}["Series C"] == 62.5
    top = {x["name"]: x for x in i.top_investors}
    assert top["Sequoia Capital"]["deals"] == 2 and top["Sequoia Capital"]["led_usd"] == 100e6
    assert top["Accel"]["led_usd"] == 50e6 and i.top_investors[0]["name"] == "Sequoia Capital"


def test_largest_rounds_are_the_top_five_with_a_description(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    for n in range(5):
        add(conn, company=f"Filler {n}", company_norm=f"filler{n}", announced_date="2026-10-09", amount_usd=(20 + n) * 1e6)
    top = compute_insights(conn, "2026-10").largest_rounds
    assert len(top) == 5 and top[0]["company"] == "Alpha Robotics" and top[0]["description"] == "Builds warehouse robots."
    assert top[1]["company"] == "Beta Works" and top[1]["description"] == ""  # no description stored: shown as a dash on the page


def test_flagged_deals_never_count(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    i = compute_insights(conn, "2026-10")
    assert all(d["company"] != "Hidden Co" for d in i.deals) and i.total_usd == 160e6


def test_unicorn_only_counts_the_first_time(conn: sqlite3.Connection) -> None:
    seed_october(conn)
    add(conn, company="Alpha Robotics", company_norm="alpha", announced_date="2026-11-10", amount_usd=200e6, valuation_usd=3e9)
    assert compute_insights(conn, "2026-11").new_unicorns == 0


def test_repeat_raisers_and_step_ups(conn: sqlite3.Connection) -> None:
    add(conn, company_norm="up", company="Upco", announced_date="2026-03-01", valuation_usd=100e6)
    add(conn, company_norm="up", company="Upco", announced_date="2026-10-02", valuation_usd=300e6)
    i = compute_insights(conn, "2026-10")
    assert i.repeat_raisers == [{"company": "Upco", "raises_tracked": 2}] and i.step_ups == [{"company": "Upco", "multiple": 3.0}]


def test_fastest_growing_sub_sector_compares_with_three_month_average(conn: sqlite3.Connection) -> None:
    for m in ("2026-07", "2026-08", "2026-09"):
        add(conn, company_norm=f"v{m}", announced_date=f"{m}-10", amount_usd=10e6, sub_sector="voice")
    add(conn, company_norm="v-now", announced_date="2026-10-10", amount_usd=40e6, sub_sector="voice")
    add(conn, company_norm="new-sector", announced_date="2026-10-11", amount_usd=40e6, sub_sector="legal")  # no history: skipped
    assert compute_insights(conn, "2026-10").fastest_subsectors == [{"sub_sector": "voice", "usd": 40e6, "avg3m_usd": 10e6, "growth_pct": 300}]


def _event(conn: sqlite3.Connection, company: str, when: str, kind: str = "shutdown") -> None:
    event = {
        "company": company, "event_type": kind, "event_date": when, "summary": f"{company} closes.", "prior_funding_usd": 5e6,
        "region": "US", "evidence": "{}", "confidence": 0.9, "needs_review": 0,
    }  # fmt: skip
    merge_event(conn, event, make_cluster("t", company, "headwind", [make_item("t", url=f"https://news.example/{company}")]))


def test_headwinds_ledger_counts_and_trend_only_with_enough_history(conn: sqlite3.Connection) -> None:
    _event(conn, "Oldco", "2026-10-03")
    _event(conn, "Dimco", "2026-10-04", "down_round")
    i = compute_insights(conn, "2026-10")
    assert i.headwind_counts == {"Shut down": 1, "Down round": 1} and i.headwind_prior_avg is None  # no history yet
    assert i.headwinds[0]["company"] == "Oldco" and i.headwinds[0]["url"] == "https://news.example/Oldco"
    add(conn, announced_date="2026-03-01")  # now six months of history
    _event(conn, "Earlier", "2026-06-01")
    assert compute_insights(conn, "2026-10").headwind_prior_avg == {"Shut down": 0.2}


def test_empty_month_does_not_crash(conn: sqlite3.Connection) -> None:
    i = compute_insights(conn, "2026-10")
    assert i.deal_count == 0 and i.total_usd == 0 and i.mom_change_pct is None and i.median_round_usd is None
    assert i.largest_rounds == [] and i.notable_facts == [] and i.history_months == 0
