"""Plain-text report of one daily run (the polished email comes in Phase 5)."""

from __future__ import annotations

from collections import Counter

from app.pipeline import DailyResult
from app.rank import Ranked


REGIONS = ("US", "Europe", "Asia", "RoW")


def _source_health(result: DailyResult) -> list[str]:
    kept_by_source = Counter(c.item.source for c in result.candidates)
    lines = ["SOURCE HEALTH (items from the last 36 hours)", ""]
    lines.append(f"{'Region':<7} {'Source':<18} {'In feed':>7} {'Recent':>6} {'New':>4} {'Kept':>4}  Status")
    by_region: dict[str, Counter[str]] = {r: Counter() for r in REGIONS}
    for r in sorted(result.results, key=lambda r: (REGIONS.index(r.source.region), r.source.name)):
        kept = kept_by_source[r.source.name]
        status = f"FAILED: {r.error}" if r.error else "ok"
        if r.undated:
            status += f" ({r.undated} undated items skipped)"
        lines.append(
            f"{r.source.region:<7} {r.source.name:<18} {r.entries_in_feed:>7} {r.in_window:>6} "
            f"{len(r.items):>4} {kept:>4}  {status}"
        )
        totals = by_region[r.source.region]
        totals["sources"] += 1
        totals["failed"] += 1 if r.error else 0
        totals["new"] += len(r.items)
        totals["kept"] += kept
    lines += ["", "PER REGION", ""]
    for region in REGIONS:
        t = by_region[region]
        note = "  <-- no working source: coverage gap" if t["sources"] - t["failed"] == 0 else ""
        lines.append(
            f"{region:<7} sources {t['sources'] - t['failed']}/{t['sources']} working, "
            f"{t['new']} new items, {t['kept']} kept after filter{note}"
        )
    return lines


def _amount(r: Ranked) -> str:
    if r.amount_usd is None:
        return "amount n/a"
    return f"${r.amount_usd / 1e9:.2f}B" if r.amount_usd >= 1e9 else f"${r.amount_usd / 1e6:.1f}M"


def _deal_line(position: int, r: Ranked) -> str:
    parts = ", ".join(f"{k} {v:g}" for k, v in r.breakdown.items())
    return f"{position:>2}. {r.company} | {_amount(r)} | {r.region} | score {r.score:g} ({parts})"


def format_report(result: DailyResult) -> str:
    p = result.processed
    kinds = Counter(c.kind for c in result.clusters)
    lines = _source_health(result)
    lines += [
        "",
        f"CLUSTERS (one per event): {len(result.clusters)} "
        f"({kinds['funding']} funding, {kinds['headwind']} headwind) from {len(result.candidates)} kept items",
        "",
        "TOP 3 FOR THE EMAIL",
    ]
    lines += [_deal_line(i, r) for i, r in enumerate(p.top, 1)] or ["  (no ranked deals today)"]
    lines += ["", f"ALL RANKED DEALS ({len(p.ranked)}; items flagged for review are listed further down)"]
    lines += [_deal_line(i, r) for i, r in enumerate(p.ranked, 1)]
    lines += ["", f"ALREADY IN THE DATABASE, NO AI CALL NEEDED ({len(p.known)})"] + [f"  {n}" for n in p.known]
    lines += ["", f"NEEDS REVIEW ({len(p.review)})"] + [f"  {n}" for n in p.review]
    lines += ["", f"NOT READ BECAUSE THE SPENDING CAP WAS REACHED ({len(p.deferred)})"] + [f"  {n}" for n in p.deferred]
    counts = ", ".join(f"{k} {v}" for k, v in sorted(p.counts.items())) or "nothing processed"
    u = p.usage
    lines += [
        "",
        f"RESULTS: {counts}",
        f"AI USE: {u.input_tokens:,} tokens in, {u.output_tokens:,} out, {p.cache_hits} answers reused from the cache; "
        f"cost ${u.cost_usd:.4f} (paid-equivalent; $0 on the free tier)",
        "",
        result.status,
    ]
    return "\n".join(lines)
