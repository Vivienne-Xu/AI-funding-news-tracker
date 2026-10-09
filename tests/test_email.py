import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from app.collect import SourceResult
from app.config import Settings, Source
from app.db import connect
from app.digest import build_digest
from app.fmt import fmt_amount, fmt_usd
from app.merge import merge_deal, merge_event
from app.pipeline_types import Processed
from app.rank import pick_top, rank_deals
from app.render import render_daily
from app.send import SendError, check_email_settings, recipients, send_email
from app.why_it_matters import why_it_matters
from factories import NOW, make_cluster, make_deal, make_item

TIERS = {"tier_1": ["Sequoia"], "tier_2": []}
OK = SourceResult(source=Source(name="TechCrunch AI", type="rss", url="https://x", tier=2, region="US"))
BAD = SourceResult(source=Source(name="Broken Feed", type="rss", url="https://y", tier=2, region="Asia"), error="HTTPError: 500")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "t.db")


def _add(conn: sqlite3.Connection, **deal: object) -> int:
    url = f"https://news.example/{deal.get('company_norm', 'acme')}"
    cluster = make_cluster("t", "x", items=[make_item("t", source="TechCrunch", url=url)])
    return merge_deal(conn, make_deal(**deal), cluster)[0]


def _processed(conn: sqlite3.Connection, ids: list[int]) -> Processed:
    ranked = rank_deals(conn, ids, TIERS)
    return Processed(deal_ids=ids, ranked=ranked, top=pick_top(ranked))


def _digest(conn: sqlite3.Connection, processed: Processed, results: list[SourceResult] | None = None):
    return build_digest(conn, processed, results or [OK], NOW, issue=7)


# --- number formatting -------------------------------------------------------------------------------------------------

def test_money_formatting_keeps_the_original_currency() -> None:
    assert fmt_usd(1_200_000_000) == "$1.2B" and fmt_usd(45_000_000) == "$45M" and fmt_usd(850_000) == "$850K"
    assert fmt_amount(20e6, "USD", 20e6) == "$20M"
    assert fmt_amount(22e6, "EUR", 25e6) == "€22M (~$25M)"
    assert fmt_amount(5e6, "XYZ", None) == "XYZ 5M"
    assert fmt_amount(None, None, None) == "Amount undisclosed"


# --- "why it matters" templates ------------------------------------------------------------------------------------------

def _row(conn: sqlite3.Connection, deal_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()


def test_second_raise_template(conn: sqlite3.Connection) -> None:
    _add(conn, company_norm="acme", announced_date="2026-04-08", amount_usd=5e6, round_type="seed")
    new = _add(conn, company_norm="acme", announced_date="2026-10-08", amount_usd=20e6)
    assert why_it_matters(conn, _row(conn, new), [20e6]) == "Second raise in 6 months"


def _history(conn: sqlite3.Connection) -> None:
    """One old deal, so the database counts as covering the whole year."""
    _add(conn, company_norm="old", announced_date="2026-01-01", amount_usd=1e6, round_type="seed")


def test_period_claims_are_skipped_when_the_period_was_not_fully_tracked(conn: sqlite3.Connection) -> None:
    for i in range(3):
        _add(conn, company_norm=f"peer{i}", amount_usd=(5 + i) * 1e6)
    big = _add(conn, company_norm="big", amount_usd=300e6)
    assert why_it_matters(conn, _row(conn, big), []) is None  # no "largest this year" / "first this month" yet


def test_largest_round_needs_enough_comparable_deals(conn: sqlite3.Connection) -> None:
    _history(conn)
    for i in range(3):
        _add(conn, company_norm=f"peer{i}", amount_usd=(5 + i) * 1e6)
    big = _add(conn, company_norm="big", amount_usd=50e6)
    assert why_it_matters(conn, _row(conn, big), []) == "Largest Series A in the US this year"
    small = _add(conn, company_norm="small", amount_usd=1e6)
    assert why_it_matters(conn, _row(conn, small), []) is None


def test_first_mega_round_and_step_up_and_largest_today(conn: sqlite3.Connection) -> None:
    _history(conn)
    mega = _add(conn, company_norm="mega", amount_usd=300e6, region="Europe")
    assert why_it_matters(conn, _row(conn, mega), []) == "First $100M+ round in Europe this month"
    _add(conn, company_norm="mega2", amount_usd=150e6, region="Europe")
    assert why_it_matters(conn, _row(conn, mega), [300e6]) is None  # no longer the first, and only one deal today
    _add(conn, company_norm="up", announced_date="2026-01-15", amount_usd=2e6, round_type="seed")
    _add(conn, company_norm="up", announced_date="2026-03-01", valuation_usd=100e6, amount_usd=5e6)
    up = _add(conn, company_norm="up", valuation_usd=250e6, amount_usd=40e6)
    assert why_it_matters(conn, _row(conn, up), []) == "Valuation up 2.5× since last round"
    assert why_it_matters(conn, _row(conn, mega), [300e6, 150e6]) == "Largest round today"


# --- building and rendering the email -----------------------------------------------------------------------------------

def test_email_contains_the_day_and_escapes_text_from_articles(conn: sqlite3.Connection) -> None:
    a = _add(conn, company_norm="alpha", company="Alpha", amount_usd=200e6, amount=200e6, description="Builds <script>alert(1)</script> agents.",
             lead_investors=json.dumps(["Sequoia Capital"]), valuation_usd=1.2e9)  # fmt: skip
    b = _add(conn, company_norm="beta", company="Beta", amount_usd=25e6, amount=22e6, currency="EUR", region="Europe", round_type="seed")
    digest = _digest(conn, _processed(conn, [a, b]), [OK, BAD])
    mail = render_daily(digest)
    assert digest.deal_count == 2 and digest.total_text == "$225M" and digest.issue == 7
    assert "Alpha" in mail.html and "€22M (~$25M)" in mail.html and "$1.2B valuation" in mail.html
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html
    assert "Led by Sequoia Capital" in mail.html and "https://news.example/alpha" in mail.html
    assert "Headwinds" not in mail.html  # omitted entirely without events
    assert "style=" in mail.html and "<style" not in mail.html  # styles are inlined
    assert "1 of 2 working today; not working: Broken Feed" in mail.text and "Nothing today: every item passed the checks." in mail.text
    assert "(paid-plan estimate)" in mail.html and "AI Funding" in mail.html and "AI Capital" not in mail.html
    assert mail.subject.endswith("2 deals, $225M") and "Alpha" in mail.text and "&lt;" not in mail.text


def test_flagged_deals_stay_out_of_the_sections_and_appear_in_the_footer(conn: sqlite3.Connection) -> None:
    good = _add(conn, company_norm="good", company="Good Co", amount_usd=30e6)
    flagged = _add(conn, company_norm="shaky", company="Shaky Co", amount_usd=900e6, needs_review=1)
    processed = _processed(conn, [good, flagged])
    processed.review.append("Shaky Co (TechCrunch): amount_mismatch")
    mail = render_daily(_digest(conn, processed))
    assert "Good Co" in mail.html and "the amount did not match the article" in mail.html and "amount_mismatch" not in mail.html
    assert "1 item could not be fully confirmed" in mail.html
    assert "$900M" not in mail.html and "Shaky Co</" not in mail.html.split("Held back for checking")[0]


def test_more_deals_are_counted_not_listed(conn: sqlite3.Connection) -> None:
    ids = [_add(conn, company_norm=f"c{i}", company=f"Company{i}", amount_usd=(10 + i) * 1e6) for i in range(9)]
    digest = _digest(conn, _processed(conn, ids))
    assert len(digest.top) == 3 and len(digest.also) == 4 and digest.more_count == 2
    assert "+ 2 more" in render_daily(digest).html


def test_headwinds_panel_appears_only_with_events(conn: sqlite3.Connection) -> None:
    event = {
        "company": "Oldco", "event_type": "shutdown", "event_date": "2026-10-08", "summary": "Oldco is shutting down.",
        "prior_funding_usd": 12e6, "region": "US", "evidence": "{}", "confidence": 0.9, "needs_review": 0,
    }  # fmt: skip
    event_id, _ = merge_event(conn, event, make_cluster("t", "Oldco", "headwind", [make_item("t", url="https://news.example/oldco")]))
    mail = render_daily(_digest(conn, Processed(event_ids=[event_id])))
    assert "Headwinds" in mail.html and "Shut down" in mail.html and "Previously raised $12M" in mail.html
    assert "No new AI funding deals" not in mail.html  # a day with only headwinds is not a quiet day


def test_quiet_day_is_a_short_note(conn: sqlite3.Connection) -> None:
    digest = _digest(conn, Processed())
    mail = render_daily(digest)
    assert digest.quiet and mail.subject.endswith("quiet day")
    assert "No new AI funding deals were found today." in mail.html and "The big" not in mail.html


def test_month_to_date_compares_only_with_a_fully_tracked_month(conn: sqlite3.Connection) -> None:
    today = _add(conn, company_norm="today", amount_usd=40e6)
    assert _digest(conn, _processed(conn, [today])).month_to_date == "October so far: $40M."
    _add(conn, company_norm="sept", announced_date="2026-09-01", amount_usd=80e6)
    assert _digest(conn, _processed(conn, [today])).month_to_date == "October so far: $40M, 50% of last month's total."


# --- sending -------------------------------------------------------------------------------------------------------------

def _settings(**extra: str) -> Settings:
    base = {"resend_api_key": "re_secret", "email_from": "onboarding@resend.dev", "email_to": "a@example.com, b@example.com"}
    return Settings(**(base | extra))


def test_send_posts_to_resend_with_every_recipient() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"], seen["body"] = request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={"id": "msg_1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert send_email(_settings(), "Subject", "<p>hi</p>", "hi", client) == "msg_1"
    assert seen["auth"] == "Bearer re_secret"
    assert seen["body"]["to"] == ["a@example.com", "b@example.com"] and seen["body"]["subject"] == "Subject"  # type: ignore[index]


def test_send_failure_is_loud_and_never_leaks_the_key() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="domain not verified")))
    with pytest.raises(SendError) as err:
        send_email(_settings(), "s", "h", "t", client)
    assert "403" in str(err.value) and "domain not verified" in str(err.value) and "re_secret" not in str(err.value)


def test_incomplete_email_settings_are_reported_before_anything_runs() -> None:
    with pytest.raises(RuntimeError, match="EMAIL_TO"):
        check_email_settings(_settings(email_to=" "))
    assert recipients(_settings(email_to="a@example.com,,  b@example.com ")) == ["a@example.com", "b@example.com"]
