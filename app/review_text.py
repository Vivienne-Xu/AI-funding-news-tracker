"""Turns the internal "needs review" codes into plain sentences for the email footer."""

from __future__ import annotations

import re

FIELDS = {
    "round_type": "the funding round", "amount": "the amount", "valuation": "the valuation", "prior_funding": "the earlier funding",
    "company": "the company name", "website": "the website", "hq_city": "the headquarters city", "hq_country": "the headquarters country",
    "lead_investors": "the lead investors", "other_investors": "the other investors", "event_type": "the type of event",
}  # fmt: skip

FIXED = {
    "round_not_in_quote": "the funding round could not be confirmed from the article",
    "amount_mismatch": "the amount did not match the article",
    "valuation_mismatch": "the valuation did not match the article",
    "prior_funding_mismatch": "the earlier funding amount did not match the article",
    "event_cue_not_in_quote": "the type of event could not be confirmed in the article",
    "amount_unusually_large_for_round": "the amount looks unusually large for this kind of round",
    "valuation_below_amount": "the valuation is lower than the amount raised",
    "date_in_future": "the date is in the future",
    "low_confidence": "the AI was not confident about it",
    "no_company": "no company name could be confirmed",
    "no_confirmed_event": "no clear event could be confirmed",
}  # fmt: skip

WITH_FIELD = {
    "no_evidence": "no supporting text was found in the article for {}",
    "evidence_not_in_text": "the supporting quote for {} was not found in the article",
    "value_not_in_quote": "{} did not match its supporting quote",
    "investor_not_in_quote": "some of {} could not be confirmed in the article",
}  # fmt: skip

CODE_RE = re.compile(r"^[a-z_]+(:[a-z_]+)?$")
SHORT_TEXT = "the short description was left out because it failed a check"


def _one(code: str) -> str:
    if code in FIXED:
        return FIXED[code]
    if re.match(r"^(description|summary)_", code):
        return SHORT_TEXT
    name, _, field = code.partition(":")
    if name in WITH_FIELD:
        return WITH_FIELD[name].format(FIELDS.get(field, field.replace("_", " ")))
    return f"a check failed ({code.replace('_', ' ')})"


def plain_review(item: str) -> str:
    """'Acme (TechCrunch): round_not_in_quote' becomes 'Acme (TechCrunch): the funding round did not match ...'."""
    head, sep, body = item.partition("): ")
    if not sep:
        return item
    prefix = ""
    if body.startswith("not stored, "):
        prefix, body = "not saved: ", body[len("not stored, ") :]
    codes = [c.strip() for c in body.split(",") if c.strip()]
    if not codes or not all(CODE_RE.match(c) for c in codes):
        return f"{head}): the AI could not read this article reliably."  # an error message, not a list of checks
    sentences = list(dict.fromkeys(_one(c) for c in codes))  # drops repeats, keeps order
    return f"{head}): {prefix}{'; '.join(sentences)}."
