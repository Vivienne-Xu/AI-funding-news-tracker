"""Creates data/demo.db: INVENTED deals (every company name starts with 'Demo') to preview the monthly report.

Usage:  python scripts/make_demo_data.py
Then:   set DB_PATH=data/demo.db and run `app monthly --month 2026-10 --dry-run`.
The real database is never touched."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from app.config import PROJECT_ROOT, load_categories
from app.db import connect

ROUNDS = [("pre_seed", 2e6), ("seed", 6e6), ("series_a", 22e6), ("series_b", 60e6), ("series_c", 130e6), ("series_d", 250e6), ("growth", 400e6)]
REGION_WEIGHTS = {"US": 6, "Europe": 2, "Asia": 2, "RoW": 1}
INVESTORS = ["Sequoia Capital", "Accel", "Lightspeed", "Index Ventures", "Local Angels Fund", "Northwind Capital", "Atlas Partners", "Khosla Ventures"]
SUB_SECTORS = ["coding agents", "legal", "healthcare", "chips", "robotics", "data tooling", "voice"]
WORDS = ["Nimbus", "Orchid", "Quartz", "Lumen", "Vector", "Harbor", "Falcon", "Atlas", "Willow", "Cobalt", "Ember", "Juniper", "Praxis", "Tessera"]


def main() -> None:
    path = PROJECT_ROOT / "data" / "demo.db"
    path.unlink(missing_ok=True)
    conn = connect(path)
    rng = random.Random(7)
    categories = list(load_categories())
    deal_id = 0
    for month in range(1, 11):
        for n in range(rng.randint(10, 16)):
            round_type, base = rng.choice(ROUNDS)
            amount = round(base * rng.uniform(0.5, 2.5), -5)
            if month == 10 and n == 0:
                amount, round_type = 900e6, "growth"  # one very large round
            region = rng.choices(list(REGION_WEIGHTS), weights=list(REGION_WEIGHTS.values()))[0]
            name = f"Demo {rng.choice(WORDS)} {rng.choice(WORDS)}"
            day = date(2026, month, rng.randint(1, 27)) if month < 10 else date(2026, 10, rng.randint(1, 8))
            leads = rng.sample(INVESTORS, rng.randint(1, 2))
            valuation = amount * rng.uniform(4, 9) if round_type in ("series_b", "series_c", "series_d", "growth") else None
            sub_sector = rng.choice(SUB_SECTORS)
            deal_id += 1
            conn.execute(
                "INSERT INTO deals (company, company_norm, round_type, amount, currency, amount_usd, fx_rate, valuation_usd, lead_investors, "
                "other_investors, announced_date, region, category, sub_sector, status, confidence, needs_review, evidence, created_at, description) "
                "VALUES (?, ?, ?, ?, 'USD', ?, 1.0, ?, ?, '[]', ?, ?, ?, ?, 'confirmed', 0.9, 0, '{}', ?, ?)",
                (name, name.lower(), round_type, amount, amount, valuation, json.dumps(leads), day.isoformat(), region,
                 rng.choice(categories), sub_sector, day.isoformat(), f"Invented demo company building {sub_sector} products."),
            )  # fmt: skip
            conn.execute(
                "INSERT INTO sources_link VALUES ('deal', ?, ?, 'TechCrunch AI', 2)", (deal_id, f"https://example.com/demo-deal-{deal_id}")
            )
    for k, (kind, company, region, when, prior) in enumerate([
        ("shutdown", "Demo Pixelwise", "US", "2026-10-03", 30e6),
        ("major_layoffs", "Demo Lumen Labs", "Europe", "2026-10-05", 80e6),
        ("down_round", "Demo Harbor AI", "US", "2026-10-07", 120e6),
    ], start=1):  # fmt: skip
        conn.execute(
            "INSERT INTO events (company, event_type, event_date, summary, prior_funding_usd, region, evidence, confidence, needs_review) "
            "VALUES (?, ?, ?, ?, ?, ?, '{}', 0.9, 0)",
            (company, kind, when, f"{company} is affected ({kind.replace('_', ' ')}).", prior, region),
        )  # fmt: skip
        conn.execute("INSERT INTO sources_link VALUES ('event', ?, ?, 'TechCrunch AI', 2)", (k, f"https://example.com/demo-event-{k}"))
    conn.commit()
    conn.close()
    print(f"Created {path} with invented demo deals. The real database was not touched.")


if __name__ == "__main__":
    main()
