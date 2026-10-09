import json

import pytest

from app.enrich import build_deal, build_event, company_key, region_for, to_usd
from app.verify import Verified
from factories import NOW, make_cluster

RATES = {"USD": 1.0, "EUR": 1.15, "CAD": 0.72}
COUNTRIES = {"united states": "US", "france": "Europe", "canada": "RoW"}


def test_usd_needs_no_conversion() -> None:
    assert to_usd(5e6, "USD", "text", "USD", RATES) == (5e6, 1.0, "usd")


def test_article_stated_dollar_figure_beats_the_estimated_rate() -> None:
    usd, rate, how = to_usd(22e6, "EUR", "Acme raised EUR 22 million ($25 million) today.", "USD", RATES)
    assert how == "article" and usd == pytest.approx(25e6) and rate == pytest.approx(25 / 22)


def test_estimated_rate_is_marked_and_unknown_currency_is_not_guessed() -> None:
    assert to_usd(10e6, "EUR", "no usd figure here", "USD", RATES) == (pytest.approx(11.5e6), 1.15, "estimated")
    assert to_usd(10e6, "XYZ", "text", "USD", RATES) == (None, None, "unknown_currency")
    assert to_usd(None, None, "text", "USD", RATES)[2] == "none"


def test_region_uses_headquarters_country_then_the_source_region() -> None:
    assert region_for("France", "US", COUNTRIES) == "Europe"
    assert region_for(None, "Asia", COUNTRIES) == "Asia"
    assert region_for("Atlantis", "Europe", COUNTRIES) == "Europe"


def test_company_key_applies_aliases() -> None:
    assert company_key("Mistral AI", {}) == "mistral"
    assert company_key("Old Name Inc", {"old name inc": "New Name"}) == "new name"


def _funding_data(**extra: object) -> dict[str, object]:
    data: dict[str, object] = {
        "company": "Acme AI", "website": None, "description": "Builds agents.", "round_type": "series_a",
        "amount": 12e6, "currency": "CAD", "valuation": None, "lead_investors": ["Sequoia"], "other_investors": [],
        "hq_city": None, "hq_country": "Canada", "category": "enterprise", "sub_sector": None, "status": "confirmed",
    }  # fmt: skip
    return data | extra


def test_build_deal_converts_flags_and_stores_evidence() -> None:
    verified = Verified(kind="funding", data=_funding_data(), confidence=0.9, evidence={"company": "Acme AI"})
    deal = build_deal(verified, make_cluster("t", "Acme AI"), "text", "CAD", {}, RATES, COUNTRIES, NOW)
    assert deal["company_norm"] == "acme"
    assert deal["amount_usd"] == pytest.approx(12e6 * 0.72) and deal["region"] == "RoW"
    assert deal["announced_date"] == "2026-10-08" and deal["needs_review"] == 0
    assert json.loads(deal["lead_investors"]) == ["Sequoia"]
    assert json.loads(deal["evidence"])["fx"] == "estimated"


def test_build_deal_flags_unknown_currency_for_review() -> None:
    verified = Verified(kind="funding", data=_funding_data(currency="XYZ"))
    deal = build_deal(verified, make_cluster("t", "Acme"), "text", "USD", {}, RATES, COUNTRIES, NOW)
    assert deal["amount_usd"] is None and deal["needs_review"] == 1
    assert "fx_unknown_currency" in json.loads(deal["evidence"])["reasons"]


def test_build_event_row() -> None:
    data = {
        "company": "Oldco", "event_type": "shutdown", "summary": "Oldco shuts down.", "prior_funding": 5e6,
        "prior_funding_currency": "USD", "hq_country": "France",
    }  # fmt: skip
    event = build_event(Verified(kind="headwind", data=data), make_cluster("t", "Oldco", "headwind"), "text", "USD", RATES, COUNTRIES)
    assert event["prior_funding_usd"] == 5e6 and event["region"] == "Europe" and event["event_type"] == "shutdown"
