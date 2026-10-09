"""The monthly commentary: one AI call (the writer model), checked by code, with a plain template as the fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from app.config import PROJECT_ROOT
from app.fmt import fmt_usd
from app.insights import Insights
from app.llm import LLMError, Usage, extract_json
from app.narrative_check import facts_to_prompt, verify_narrative

log = logging.getLogger("app.narrative")

PROMPT_PATH = PROJECT_ROOT / "prompts" / "narrative.md"
SECTIONS = ("layers", "global", "stage", "rounds", "headwinds")
PLAIN_HEADLINES = {
    "layers": "Capital by stack layer", "global": "Regional view", "stage": "Stage mix and most active investors",
    "rounds": "Largest rounds", "headwinds": "Headwinds",
}  # fmt: skip
MAX_OUTPUT_TOKENS = 1800


class Narrative(BaseModel):
    headline: str
    takeaways: list[str]
    section_headlines: dict[str, str]
    section_notes: dict[str, str]

    @field_validator("takeaways")
    @classmethod
    def three_takeaways(cls, value: list[str]) -> list[str]:
        if len(value) != 3:
            raise ValueError("exactly 3 takeaways are required")
        return value

    @field_validator("section_headlines", "section_notes")
    @classmethod
    def all_sections(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != set(SECTIONS):
            raise ValueError(f"sections must be exactly {SECTIONS}")
        return value


@dataclass
class NarrativeResult:
    text: dict[str, Any]
    source: str  # "model", "model_retry" or "template"
    usage: Usage = field(default_factory=Usage)
    problems: list[str] = field(default_factory=list)  # what the checks found in the last rejected draft


def narrative_schema() -> dict[str, Any]:
    text = {"type": "string"}
    per_section = {"type": "object", "properties": {s: text for s in SECTIONS}, "required": list(SECTIONS)}
    return {
        "type": "object",
        "properties": {
            "headline": text, "takeaways": {"type": "array", "items": text}, "section_headlines": per_section, "section_notes": per_section,
        },
        "required": ["headline", "takeaways", "section_headlines", "section_notes"],
    }  # fmt: skip


def _clean(node: Any) -> Any:
    """Drops empty values so the model sees only what exists."""
    if isinstance(node, dict):
        cleaned = {k: _clean(v) for k, v in node.items()}
        return {k: v for k, v in cleaned.items() if v is not None and v != [] and v != {}}
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node


def narrative_facts(i: Insights) -> dict[str, Any]:
    """The compact facts the writer sees: every figure is already computed, and money is pre-formatted."""
    money = lambda v: fmt_usd(v) if v else None  # noqa: E731
    facts = {
        "month": i.month_label, "months_of_data_available": i.history_months, "total_raised": money(i.total_usd), "deal_count": i.deal_count,
        "deals_without_disclosed_amount": i.undisclosed_count or None, "previous_month_total_raised": money(i.prev_total_usd),
        "change_in_total_vs_previous_month_pct": i.mom_change_pct, "median_round": money(i.median_round_usd),
        "five_largest_rounds_share_of_capital_pct": i.top5_share_pct, "two_largest_rounds_share_of_capital_pct": i.top2_share_pct,
        "rounds_of_100m_or_more": i.mega_rounds, "new_unicorns": i.new_unicorns,
        "layers": [{"layer": x["layer"], "capital": money(x["all_usd"]), "share_pct": x["all_pct"],
                    "capital_excluding_two_largest_rounds": money(x["ex_top2_usd"]), "share_excluding_two_largest_rounds_pct": x["ex_top2_pct"]}
                   for x in i.layers],
        "regions": [{"region": x["region"], "capital": money(x["usd"]), "share_pct": x["pct"], "deals": x["count"],
                     "share_change_vs_previous_month_pts": x["shift_pts"],
                     "largest_deal": f"{x['largest_company']} {fmt_usd(x['largest_usd'])}" if x["largest_company"] else None}
                    for x in i.regions],
        "stages": [{"stage": x["stage"], "share_of_capital_pct": x["capital_pct"], "share_of_deals_pct": x["deal_pct"]} for x in i.stages],
        "most_active_investors": [{"name": x["name"], "deals": x["deals"], "capital_led": money(x["led_usd"])} for x in i.top_investors[:5]],
        "largest_rounds": [{"company": x["company"], "layer": x["layer"], "region": x["region"], "round": x["round"],
                            "raised": money(x["usd"]), "valuation": money(x["valuation_usd"])} for x in i.largest_rounds[:5]],
        "repeat_raisers": [x["company"] for x in i.repeat_raisers[:5]],
        "valuation_step_ups": [{"company": x["company"], "multiple": x["multiple"]} for x in i.step_ups[:3]],
        "fastest_growing_sub_sectors": [{"sub_sector": x["sub_sector"], "growth_vs_3_month_average_pct": x["growth_pct"]} for x in i.fastest_subsectors],
        "headwinds": {"count_by_type": i.headwind_counts, "events": [{"type": h["type"], "company": h["company"], "region": h["region"]} for h in i.headwinds[:8]]},
        "notable_facts": i.notable_facts,
    }  # fmt: skip
    return _clean(facts)


def template_narrative(i: Insights) -> dict[str, Any]:
    """Plain, fully code-written commentary. Used when the AI text fails its checks."""
    if not i.deal_count:
        headline = f"No funding deals were recorded for {i.month_label}."
        takeaways = ["No deals were tracked this month.", "No regional comparison is available.", "No stage comparison is available."]
    else:
        headline = f"AI companies raised {fmt_usd(i.total_usd)} across {i.deal_count} deals in {i.month_label}."
        if i.mom_change_pct is not None:
            headline = headline[:-1] + f", {'up' if i.mom_change_pct >= 0 else 'down'} {abs(i.mom_change_pct):g}% on the previous month."
        top = i.largest_rounds[0] if i.largest_rounds else None
        lead_region = max(i.regions, key=lambda r: r["usd"])
        takeaways = [
            f"The largest round was {top['company']} with {fmt_usd(top['usd'])}." if top else "No round amounts were disclosed.",
            f"The {min(5, i.deal_count)} largest rounds make up {i.top5_share_pct:g}% of capital raised." if i.top5_share_pct is not None
            else "Concentration could not be measured.",
            f"{lead_region['region']} led with {lead_region['pct']:g}% of capital raised.",
        ]
    return {
        "headline": headline, "takeaways": takeaways, "section_headlines": dict(PLAIN_HEADLINES),
        "section_notes": {s: "" for s in SECTIONS},
    }  # fmt: skip


def generate_narrative(client: Any, model: str, i: Insights, retry_wait_seconds: float = 20.0, prompt_path: Path = PROMPT_PATH) -> NarrativeResult:
    """Up to two drafts. A draft is used only if every number and name in it is backed by the facts."""
    facts = narrative_facts(i)
    system = prompt_path.read_text(encoding="utf-8")
    base = f"FACTS:\n{facts_to_prompt(facts)}"
    usage, problems = Usage(), []
    for attempt in (1, 2):
        message = base if not problems else base + "\n\nYour previous draft used items that are not in FACTS: " + "; ".join(problems[:10]) + ". Write it again without them."
        try:
            raw, used = extract_json(client, model, system, message, narrative_schema(), MAX_OUTPUT_TOKENS, retry_wait_seconds)
        except LLMError as exc:
            usage += exc.usage
            problems = [f"ai_call_failed: {exc}"]
            break
        usage += used
        try:
            draft = Narrative.model_validate(raw).model_dump()
        except ValidationError as exc:
            problems = [f"invalid_shape: {exc.errors()[0]['msg']}"]
            continue
        problems = verify_narrative(draft, facts)
        if not problems:
            return NarrativeResult(draft, "model" if attempt == 1 else "model_retry", usage)
    log.warning("Narrative draft rejected, using the template: %s", problems[:5])
    return NarrativeResult(template_narrative(i), "template", usage, problems)
