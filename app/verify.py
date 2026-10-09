"""Step 6: code checks on the AI's answer. Evidence or null: unsupported values are removed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.parse import Money, find_amounts, round_from_text
from app.schemas import QUOTED_FIELDS, Extraction

AMOUNT_TOLERANCE = 0.005  # 0.5%: "$1.5B" vs 1_500_000_000
LOW_CONFIDENCE = 0.6
MAX_DESCRIPTION_WORDS = 25
MAX_SUMMARY_WORDS = 30
# Largest amounts (in USD) that look normal for the round. Bigger ones are flagged, not removed.
PLAUSIBLE_MAX_USD = {"pre_seed": 25e6, "seed": 150e6, "series_a": 500e6}

HEDGE_RE = re.compile(
    r"\b(reportedly|in talks|rumou?r(?:ed|s)?|according to (?:sources|people)|people familiar|sources say|"
    r"is said to|said to be|expected to (?:raise|close))\b",
    re.IGNORECASE,
)
EVENT_CUES = {
    "shutdown": r"shut(?:s|ting)?\s+down|shutdown|ceas(?:e|es|ed|ing)\s+operations|wind(?:s|ing)?\s+down|clos(?:e|es|ed|ing)\s+(?:its\s+)?doors",
    "down_round": r"down\s+round|lower\s+valuation|valuation\s+(?:cut|drop|fell|slash)|valued\s+at\s+less",
    "flat_round": r"flat\s+round|flat\s+valuation|same\s+valuation|valuation\s+(?:unchanged|flat)",
    "acquihire": r"acqui-?hire|joins?\s+(?:google|microsoft|meta|amazon|apple|nvidia|openai|anthropic)|hir(?:e|es|ed|ing)\s+(?:its\s+)?(?:founders|team|staff)",
    "major_layoffs": r"laid\s+off|lays?\s+off|layoffs?|job\s+cuts|redundanc|cutting\s+(?:about\s+)?\d+",
    "distressed_sale": r"distressed|fire\s+sale|insolven|bankrupt|administration|receivership",
}  # fmt: skip
NUMBER_RE = re.compile(r"\d[\d,.]*\d|\d")


@dataclass
class Verified:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)  # final values; unsupported ones are None / empty
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # model values dropped for lack of evidence
    confidence: float = 0.0  # the model's own 0-1 confidence
    evidence: dict[str, str] = field(default_factory=dict)  # word-for-word quotes for the values that survived


def squash(text: str) -> str:
    return " ".join(text.split())


def _contains(quote: str, value: str) -> bool:
    return squash(value).casefold() in squash(quote).casefold()


def _matching_money(quote: str, value: float, currency: str | None, bare_dollar: str = "USD") -> Money | None:
    for money in find_amounts(quote, bare_dollar):
        if abs(money.amount - value) <= AMOUNT_TOLERANCE * value and (currency is None or money.currency == currency):
            return money
    return None


def _numbers_supported(text: str, source: str) -> bool:
    return all(n.rstrip(".,") in source for n in NUMBER_RE.findall(text))


def _clean_sentence(data: dict[str, Any], key: str, max_words: int, source: str, reasons: list[str], removed: list[str]) -> None:
    value = data.get(key)
    if not value:
        return
    problem = None
    if len(value.split()) > max_words:
        problem = f"{key}_too_long"
    elif "http" in value.lower() or "www." in value.lower():
        problem = f"{key}_has_url"
    elif not _numbers_supported(value, source):
        problem = f"{key}_has_unsupported_number"
    if problem:
        data[key] = None
        reasons.append(problem)
        removed.append(key)


def verify(
    ex: Extraction,
    source_text: str,
    announced: date | None = None,
    today: date | None = None,
    bare_dollar: str = "USD",
) -> Verified:
    """`source_text` is exactly the article text the model was shown (without code hints).
    `bare_dollar` is what a plain '$' means for this source (USD, or CAD for BetaKit)."""
    result = Verified(kind=ex.kind, confidence=ex.confidence)
    if ex.kind == "neither":
        return result
    data = ex.model_dump(exclude={"evidence", "confidence", "kind"})
    reasons, removed = result.reasons, result.removed
    haystack = squash(source_text)

    def drop(name: str, reason: str) -> None:
        data[name] = [] if isinstance(data[name], list) else None
        reasons.append(reason)
        removed.append(name)

    for name in QUOTED_FIELDS:
        value = data[name]
        if value in (None, []):
            continue
        quote = ex.evidence.get(name, "")
        if not quote.strip():
            drop(name, f"no_evidence:{name}")
        elif squash(quote) not in haystack:
            drop(name, f"evidence_not_in_text:{name}")
        elif name in ("company", "website", "hq_city", "hq_country") and not _contains(quote, value):
            drop(name, f"value_not_in_quote:{name}")
        elif name == "round_type" and round_from_text(quote) != value:
            drop(name, "round_not_in_quote")
        elif name == "amount":
            money = _matching_money(quote, value, data["currency"], bare_dollar)
            if money is None:
                drop(name, "amount_mismatch")
            else:
                data["currency"] = money.currency
        elif name in ("valuation", "prior_funding"):
            money = _matching_money(quote, value, None, bare_dollar)
            if money is None:
                drop(name, f"{name}_mismatch")
            else:
                data[f"{name}_currency"] = money.currency
        elif name in ("lead_investors", "other_investors"):
            kept = [n for n in value if _contains(quote, n)]
            if len(kept) < len(value):
                reasons.append(f"investor_not_in_quote:{name}")
                removed.append(name)
            data[name] = kept
        elif name == "event_type" and not re.search(EVENT_CUES[value], quote, re.IGNORECASE):
            drop(name, "event_cue_not_in_quote")

    if data["amount"] is None:
        data["currency"] = None  # a currency without a verified amount means nothing

    _clean_sentence(data, "description", MAX_DESCRIPTION_WORDS, source_text, reasons, removed)
    _clean_sentence(data, "summary", MAX_SUMMARY_WORDS, source_text, reasons, removed)

    if ex.kind == "funding" and HEDGE_RE.search(source_text):
        data["status"] = "reported"

    # Sanity checks: these flag the item for review but keep the values.
    amount, currency, round_type = data["amount"], data["currency"], data["round_type"]
    if amount and currency == "USD" and round_type in PLAUSIBLE_MAX_USD and amount > PLAUSIBLE_MAX_USD[round_type]:
        reasons.append("amount_unusually_large_for_round")
    valuation = data["valuation"]
    if amount and valuation and data.get("valuation_currency") == currency and valuation < amount:
        reasons.append("valuation_below_amount")
    if announced and announced > (today or date.today()):
        reasons.append("date_in_future")
    data["announced_date"] = announced
    if ex.confidence < LOW_CONFIDENCE:
        reasons.append("low_confidence")
    if not data["company"]:
        reasons.append("no_company")
    if ex.kind == "headwind" and not data["event_type"]:
        reasons.append("no_confirmed_event")

    result.data = data
    result.evidence = {k: q for k, q in ex.evidence.items() if data.get(k) not in (None, [])}
    result.needs_review = bool(reasons)
    return result
