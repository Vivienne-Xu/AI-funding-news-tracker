"""Fills the monthly templates: the interactive HTML report and the email."""

from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Any

from markupsafe import Markup

from app.fmt import fmt_usd
from app.insights import Insights, shift_month
from app.render import TEMPLATE_DIR, RenderedEmail, environment, inline_styles, theme_css

CHARTS_IN_EMAIL = (("layers", "Capital by stack layer"), ("global", "Capital by region"))
NARRATIVE_NOTES = {
    "model": "written by an AI model from the computed figures, and every number and name checked against them by code",
    "model_retry": "written by an AI model from the computed figures (second draft; the first failed the checks), and every number and name checked by code",
    "template": "written by code from the computed figures (the AI draft did not pass the checks, so a plain template was used)",
    "template_budget": "written by code from the computed figures (the AI spending cap would have been exceeded)",
}  # fmt: skip


def _kpis(i: Insights) -> list[tuple[str, str]]:
    return [
        (fmt_usd(i.median_round_usd) if i.median_round_usd else "–", "Median round"),
        (f"{i.top5_share_pct:g}%" if i.top5_share_pct is not None else "–", "Top-5 share"),
        (str(i.mega_rounds), "$100M+ rounds"),
        (str(i.new_unicorns), "New unicorns"),
    ]


def _common(i: Insights, narrative: dict[str, Any], source: str, cost_usd: float, issue: int) -> dict[str, Any]:
    prev = shift_month(i.month, -1)
    return {
        "i": i, "n": narrative, "issue": issue, "prev_label": prev, "kpis": _kpis(i),
        "narrative_note": NARRATIVE_NOTES.get(source, source), "cost_text": "under $0.01" if cost_usd < 0.01 else f"${cost_usd:.2f}",
    }  # fmt: skip


def _css() -> str:
    return theme_css() + "\n" + (TEMPLATE_DIR / "monthly.css").read_text(encoding="utf-8")


def render_report_html(i: Insights, narrative: dict[str, Any], source: str, cost_usd: float, issue: int, source_count: int) -> str:
    """The interactive report (loads Chart.js from a CDN, so charts need an internet connection)."""
    env = environment()
    ctx = _common(i, narrative, source, cost_usd, issue)
    ctx |= {
        "css": Markup(_css()), "charts_js": Markup((TEMPLATE_DIR / "charts.js").read_text(encoding="utf-8")),
        "data": {k: v for k, v in asdict(i).items() if k in ("layers", "stages", "region_trend")},
        "sources_note": f"{source_count} sources",
    }  # fmt: skip
    return env.get_template("monthly_report.html.j2").render(**ctx)


def render_monthly_email(
    i: Insights, narrative: dict[str, Any], source: str, cost_usd: float, issue: int,
    pdf: bytes | None, charts: dict[str, bytes], asset_note: str,
) -> RenderedEmail:  # fmt: skip
    env = environment()
    shown = [(k, t) for k, t in CHARTS_IN_EMAIL if k in charts]
    ctx = _common(i, narrative, source, cost_usd, issue)
    ctx |= {
        "css": _css(), "subject": f"AI Funding Monthly · {i.month_label} · {fmt_usd(i.total_usd)} raised",
        "has_pdf": pdf is not None, "charts": shown, "asset_note": asset_note,
    }  # fmt: skip
    html = inline_styles(env.get_template("monthly_email.html.j2").render(**ctx))
    text = env.get_template("monthly_email.txt.j2").render(**ctx)
    attachments = [{"filename": f"chart_{k}.png", "content": base64.b64encode(charts[k]).decode(), "content_id": f"chart_{k}"} for k, _ in shown]
    if pdf is not None:
        attachments.append({"filename": f"AI-Capital-Monthly-{i.month}.pdf", "content": base64.b64encode(pdf).decode()})
    return RenderedEmail(ctx["subject"], html, text, attachments)
