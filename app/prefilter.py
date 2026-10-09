"""Step 2: keep only items whose headline or summary look like funding news or bad news."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.collect import RawItem
from app.config import Source

Kind = Literal["funding", "headwind"]

BIG_TECH = "Google|Microsoft|Meta|Amazon|Apple|Nvidia|OpenAI|Anthropic"

FUNDING_RE = re.compile(
    r"\b(raises?|raised|raising|funding|seed|(?-i:Series\s+[A-J])|round|led\s+by|valuation)\b",
    re.IGNORECASE,
)
HEADWIND_RE = re.compile(
    r"\b(shuts?\s+down|shutting\s+down|shut\s+down|shutdown|wind(?:s|ing)?\s+down|layoffs?|"
    r"laid\s+off|lays?\s+off|down\s+round|acqui-?hire)\b"
    rf"|\bjoins?\b.*\b(?:{BIG_TECH})\b",
    re.IGNORECASE,
)
AI_RE = re.compile(
    r"\b((?-i:AI)|artificial\s+intelligence|machine\s+learning|LLMs?|generative|GenAI|"
    r"agentic|foundation\s+models?|robotics|robots?|autonomous)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    item: RawItem
    kind: Kind


def classify(title: str, summary: str, needs_ai_match: bool) -> Kind | None:
    """Returns 'headwind', 'funding', or None (drop). Headwind terms are checked first."""
    text = f"{title}. {summary}"
    if needs_ai_match and not AI_RE.search(text):
        return None
    if HEADWIND_RE.search(text):
        return "headwind"
    if FUNDING_RE.search(text):
        return "funding"
    return None


def prefilter(items: list[RawItem], sources: dict[str, Source]) -> list[Candidate]:
    """`sources` maps source name to its config, to know which feeds need an AI match."""
    kept: list[Candidate] = []
    for item in items:
        kind = classify(item.title, item.summary, sources[item.source].needs_ai_match)
        if kind:
            kept.append(Candidate(item=item, kind=kind))
    return kept
