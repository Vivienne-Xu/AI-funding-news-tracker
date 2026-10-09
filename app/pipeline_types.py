"""Result containers shared by the pipeline, the email builder and the text report."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.llm import Usage
from app.rank import Ranked


@dataclass
class Processed:
    """What happened to the clusters in steps 4-8."""

    usage: Usage = field(default_factory=Usage)
    counts: Counter[str] = field(default_factory=Counter)
    deal_ids: list[int] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)
    ranked: list[Ranked] = field(default_factory=list)
    top: list[Ranked] = field(default_factory=list)
    review: list[str] = field(default_factory=list)  # flagged or failed items, with the reason
    deferred: list[str] = field(default_factory=list)  # not read because the budget ran out
    known: list[str] = field(default_factory=list)  # already in the database: no AI call needed
    cache_hits: int = 0
