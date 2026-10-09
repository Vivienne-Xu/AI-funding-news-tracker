"""Turns a verified extraction into database rows: USD conversion, region, standard company name."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.cluster import Cluster, normalise_name
from app.parse import Money, stated_usd_equivalent
from app.verify import Verified


def company_key(name: str, aliases: dict[str, str]) -> str:
    """Standard lower-case name used to recognise the same company: alias table first, then cleanup."""
    return normalise_name(aliases.get(name.casefold(), name))


def region_for(hq_country: str | None, source_region: str, countries: dict[str, str]) -> str:
    """Region of the headquarters country; falls back to the news source's region."""
    if hq_country:
        return countries.get(hq_country.strip().casefold(), source_region)
    return source_region


def to_usd(
    amount: float | None, currency: str | None, text: str, bare_dollar: str, rates: dict[str, float]
) -> tuple[float | None, float | None, str]:
    """Returns (amount in USD, rate used, how). 'article' = the article states its own USD figure."""
    if amount is None or currency is None:
        return None, None, "none"
    if currency == "USD":
        return amount, 1.0, "usd"
    stated = stated_usd_equivalent(text, Money(amount, currency), bare_dollar)
    if stated:
        return stated.amount, stated.amount / amount, "article"
    if currency in rates:
        return amount * rates[currency], rates[currency], "estimated"
    return None, None, "unknown_currency"


def build_deal(
    verified: Verified,
    cluster: Cluster,
    source_text: str,
    bare_dollar: str,
    aliases: dict[str, str],
    rates: dict[str, float],
    countries: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    """One row for the `deals` table (lists and evidence stored as JSON text)."""
    d = verified.data
    amount_usd, fx_rate, fx_how = to_usd(d["amount"], d["currency"], source_text, bare_dollar, rates)
    valuation_usd, _, val_how = to_usd(d["valuation"], d.get("valuation_currency"), source_text, bare_dollar, rates)
    reasons = list(verified.reasons)
    if "unknown_currency" in (fx_how, val_how):
        reasons.append("fx_unknown_currency")
    notes = {"fx": fx_how, "valuation_fx": val_how}
    return {
        "company": d["company"],
        "company_norm": company_key(d["company"], aliases),
        "website": d["website"],
        "description": d["description"],
        "round_type": d["round_type"],
        "amount": d["amount"],
        "currency": d["currency"],
        "amount_usd": amount_usd,
        "fx_rate": fx_rate,
        "valuation_usd": valuation_usd,
        "lead_investors": json.dumps(d["lead_investors"]),
        "other_investors": json.dumps(d["other_investors"]),
        "announced_date": cluster.best.published_at.date().isoformat(),
        "hq_city": d["hq_city"],
        "hq_country": d["hq_country"],
        "region": region_for(d["hq_country"], cluster.best.region, countries),
        "category": d["category"],
        "sub_sector": d["sub_sector"],
        "status": d["status"],
        "confidence": verified.confidence,
        "needs_review": 1 if reasons else 0,
        "evidence": json.dumps({"quotes": verified.evidence, "reasons": reasons, "removed": verified.removed, **notes}),
        "created_at": now.isoformat(),
    }


def build_event(
    verified: Verified,
    cluster: Cluster,
    source_text: str,
    bare_dollar: str,
    rates: dict[str, float],
    countries: dict[str, str],
) -> dict[str, Any]:
    """One row for the `events` table (headwinds)."""
    d = verified.data
    prior_usd, _, _ = to_usd(d["prior_funding"], d.get("prior_funding_currency"), source_text, bare_dollar, rates)
    return {
        "company": d["company"],
        "event_type": d["event_type"],
        "event_date": cluster.best.published_at.date().isoformat(),
        "summary": d["summary"],
        "prior_funding_usd": prior_usd,
        "region": region_for(d["hq_country"], cluster.best.region, countries),
        "evidence": json.dumps({"quotes": verified.evidence, "reasons": verified.reasons}),
        "confidence": verified.confidence,
        "needs_review": 1 if verified.needs_review else 0,
    }
