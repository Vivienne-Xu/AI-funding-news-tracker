"""Small builders shared by the Phase 4 tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.cluster import Cluster
from app.collect import RawItem

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def make_item(title: str, source: str = "TechCrunch", tier: int = 2, region: str = "US", summary: str = "", url: str = "") -> RawItem:
    return RawItem(
        url=url or f"https://example.com/{abs(hash((title, source)))}",
        source=source, title=title, summary=summary, published_at=NOW, region=region, tier=tier,
    )  # fmt: skip


def make_cluster(title: str, company: str | None, kind: str = "funding", items: list[RawItem] | None = None) -> Cluster:
    items = items or [make_item(title)]
    best = min(items, key=lambda i: i.tier)
    return Cluster(kind=kind, company=company, best=best, items=tuple(items))  # type: ignore[arg-type]


def make_deal(**overrides: Any) -> dict[str, Any]:
    """A complete row for the `deals` table."""
    deal: dict[str, Any] = {
        "company": "Acme", "company_norm": "acme", "website": None, "description": None, "round_type": "series_a",
        "amount": 20e6, "currency": "USD", "amount_usd": 20e6, "fx_rate": 1.0, "valuation_usd": None,
        "lead_investors": "[]", "other_investors": "[]", "announced_date": "2026-10-08", "hq_city": None,
        "hq_country": None, "region": "US", "category": "enterprise_ai", "sub_sector": None, "status": "confirmed",
        "confidence": 0.9, "needs_review": 0, "evidence": json.dumps({"quotes": {}, "reasons": []}),
        "created_at": NOW.isoformat(),
    }  # fmt: skip
    return deal | overrides
