"""Step 4: regex parsing of headlines and money amounts. No AI model, no network."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.cluster import company_from_title

UNIT_FACTORS = {
    "k": 1e3,
    "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "lakh": 1e5, "cr": 1e7, "crore": 1e7,
}  # fmt: skip
SYMBOLS = {"US$": "USD", "C$": "CAD", "A$": "AUD", "S$": "SGD", "$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
CODES = "USD|EUR|GBP|INR|CAD|AUD|SGD|CHF|SEK|NOK|DKK|AED|ILS|JPY|CNY|RMB"
WORDS = {"dollar": "USD", "dollars": "USD", "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP"}

_NUM = r"(?P<num>\d[\d,]*(?:\.\d+)?)"
_UNIT = r"(?:\s?-?\s?(?P<unit>billion|bn|b|million|mn|mm|m|crore|cr|lakh|k)(?![a-z]))?"
_SYMBOL_FIRST = re.compile(
    rf"(?P<cur>US\$|C\$|A\$|S\$|\$|€|£|₹|\b(?:{CODES}))\s?{_NUM}{_UNIT}(?:\s?(?P<tcur>USD|CAD|AUD|SGD|EUR|GBP)\b)?",
    re.IGNORECASE,
)
_AMOUNT_THEN_CURRENCY = re.compile(
    rf"(?<![\w$€£₹.]){_NUM}\s?-?\s?(?P<unit>billion|bn|million|mn|mm|m|crore|cr|lakh)(?![a-z])"
    rf"\s+(?P<cur>{CODES}|dollars?|euros?|pounds?)\b",
    re.IGNORECASE,
)
# Text between an amount and a stated USD equivalent: "₹32 Cr (around $3.7 Mn)"
_EQUIVALENT_GAP = re.compile(r"\s*\(\s*(?:around|about|approximately|roughly|~)?\s*", re.IGNORECASE)

ROUND_PATTERNS: list[tuple[str, str]] = [
    ("pre_seed", r"\bpre-?seed\b"),
    ("seed", r"\bseed\b"),
    ("series_a", r"\bseries\s+a\b"),
    ("series_b", r"\bseries\s+b\b"),
    ("series_c", r"\bseries\s+c\b"),
    ("series_d", r"\bseries\s+d\b"),
    ("series_e_plus", r"\bseries\s+[e-j]\b"),
    ("growth", r"\bgrowth\s+(?:round|equity|funding)\b"),
    ("bridge", r"\bbridge\s+(?:round|funding|financing)\b"),
]
LEAD_RE = re.compile(
    r"\bled\s+by\s+(?P<lead>[^,.;]+?)(?=\s+(?:to|for|with|as|in|at|after|amid|and\s+will)\b|[,.;]|$)", re.IGNORECASE
)


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str


@dataclass(frozen=True)
class Found:
    start: int
    end: int
    money: Money


@dataclass(frozen=True)
class ParsedHeadline:
    company: str | None = None
    money: Money | None = None
    round_type: str | None = None
    lead_investors: list[str] = field(default_factory=list)


def _currency(raw: str) -> str:
    key = raw.strip()
    if key.upper() in SYMBOLS:
        return SYMBOLS[key.upper()]
    if key in SYMBOLS:
        return SYMBOLS[key]
    if key.lower() in WORDS:
        return WORDS[key.lower()]
    return "CNY" if key.upper() == "RMB" else key.upper()


def find_money(text: str, bare_dollar: str = "USD") -> list[Found]:
    """Every money amount with its position. A bare '$' means `bare_dollar` (BetaKit writes CAD this way);
    an explicit code after the amount ('$60-million USD') always wins."""
    found: list[Found] = []
    for m in _SYMBOL_FIRST.finditer(text):
        unit = (m.group("unit") or "").lower()
        if not unit and m.group("cur").isalpha():
            continue  # "USD 5" alone is too vague; code-first amounts need a unit
        currency = bare_dollar if m.group("cur") == "$" else _currency(m.group("cur"))
        if m.group("tcur"):
            currency = _currency(m.group("tcur"))
        number = float(m.group("num").replace(",", ""))
        found.append(Found(m.start(), m.end(), Money(number * UNIT_FACTORS.get(unit, 1.0), currency)))
    for m in _AMOUNT_THEN_CURRENCY.finditer(text):
        if any(abs(m.start() - f.start) <= 1 for f in found):
            continue
        number = float(m.group("num").replace(",", ""))
        found.append(Found(m.start(), m.end(), Money(number * UNIT_FACTORS[m.group("unit").lower()], _currency(m.group("cur")))))
    return sorted(found, key=lambda f: f.start)


def find_amounts(text: str, bare_dollar: str = "USD") -> list[Money]:
    return [f.money for f in find_money(text, bare_dollar)]


def stated_usd_equivalent(text: str, target: Money, bare_dollar: str = "USD") -> Money | None:
    """If the text gives a USD figure right after `target` ('€22 million ($25 million)'), returns it."""
    found = find_money(text, bare_dollar)
    for current, following in zip(found, found[1:]):
        close = _EQUIVALENT_GAP.fullmatch(text[current.end : following.start])
        if current.money == target and close and following.money.currency == "USD" and target.currency != "USD":
            return following.money
    return None


def round_from_text(text: str) -> str | None:
    """The round named in the text ('Series B' -> 'series_b'). None if none or more than one."""
    hits = [name for name, pattern in ROUND_PATTERNS if re.search(pattern, text, re.IGNORECASE)]
    if "pre_seed" in hits:
        hits = [h for h in hits if h != "seed"]
    return hits[0] if len(hits) == 1 else None


def parse_headline(title: str, bare_dollar: str = "USD") -> ParsedHeadline:
    """Reads patterns like 'Acme raises $20M Series A led by Fund'. Fills only what is clear."""
    amounts = find_amounts(title, bare_dollar)
    lead_match = LEAD_RE.search(title)
    leads = re.split(r"\s+(?:and|&)\s+", lead_match.group("lead").strip()) if lead_match else []
    return ParsedHeadline(
        company=company_from_title(title),
        money=amounts[0] if len(amounts) == 1 else None,  # two amounts in a headline: do not guess
        round_type=round_from_text(title),
        lead_investors=[name for name in leads if name],
    )
