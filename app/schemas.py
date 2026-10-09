"""Strict shapes for what the AI model may return. Anything outside the closed lists is rejected."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator

from app.config import load_categories

Kind = Literal["funding", "headwind", "neither"]
RoundType = Literal[
    "pre_seed", "seed", "series_a", "series_b", "series_c", "series_d", "series_e_plus", "growth", "bridge", "other"
]
EventType = Literal["shutdown", "down_round", "flat_round", "acquihire", "major_layoffs", "distressed_sale"]
Status = Literal["confirmed", "reported"]

ROUND_TYPES = get_args(RoundType)
EVENT_TYPES = get_args(EventType)

# Facts that must come with a word-for-word quote from the article.
QUOTED_FIELDS = (
    "company", "website", "round_type", "amount", "valuation", "lead_investors", "other_investors",
    "hq_city", "hq_country", "event_type", "prior_funding",
)  # fmt: skip


@lru_cache
def category_codes() -> tuple[str, ...]:
    return tuple(load_categories())


class Extraction(BaseModel):
    kind: Kind
    company: str | None = None
    website: str | None = None
    description: str | None = None  # paraphrase, at most 25 words
    round_type: RoundType | None = None
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    valuation: float | None = Field(default=None, gt=0)
    lead_investors: list[str] = Field(default_factory=list)
    other_investors: list[str] = Field(default_factory=list)
    hq_city: str | None = None
    hq_country: str | None = None
    category: str | None = None
    sub_sector: str | None = None
    status: Status | None = None
    event_type: EventType | None = None
    summary: str | None = None  # headwinds: one-line paraphrase
    prior_funding: float | None = Field(default=None, gt=0)
    evidence: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)

    @field_validator("category")
    @classmethod
    def category_in_closed_list(cls, value: str | None) -> str | None:
        if value is not None and value not in category_codes():
            raise ValueError(f"category '{value}' is not one of {category_codes()}")
        return value


def tool_schema() -> dict[str, Any]:
    """The JSON shape given to the model, with the closed lists spelled out."""
    text = {"type": ["string", "null"]}
    number = {"type": ["number", "null"]}

    def nullable_choice(values: tuple[str, ...]) -> dict[str, Any]:
        return {"anyOf": [{"type": "string", "enum": list(values)}, {"type": "null"}]}

    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(get_args(Kind))},
            "company": text,
            "website": text,
            "description": text,
            "round_type": nullable_choice(ROUND_TYPES),
            "amount": number,
            "currency": text,
            "valuation": number,
            "lead_investors": {"type": "array", "items": {"type": "string"}},
            "other_investors": {"type": "array", "items": {"type": "string"}},
            "hq_city": text,
            "hq_country": text,
            "category": nullable_choice(category_codes()),
            "sub_sector": text,
            "status": nullable_choice(("confirmed", "reported")),
            "event_type": nullable_choice(EVENT_TYPES),
            "summary": text,
            "prior_funding": number,
            "evidence": {"type": "object", "additionalProperties": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": ["kind", "evidence", "confidence"],
    }
