"""Fills the email templates (HTML with inlined styles, plus plain text)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jinja2 import Environment, FileSystemLoader
from premailer import Premailer

from app.config import PROJECT_ROOT
from app.digest import Digest
from app.fmt import fmt_usd

TEMPLATE_DIR = PROJECT_ROOT / "templates"
REGION_COLOURS = {"US": "#0B1F33", "Europe": "#1F8A8A", "Asia": "#C9A227", "RoW": "#A7B0BC"}


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)  # Resend format: filename, content (base64), content_id


def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR), trim_blocks=True, lstrip_blocks=True,
        autoescape=lambda name: bool(name and name.endswith(".html.j2")),  # escape in HTML, never in plain text
    )  # fmt: skip
    env.globals["colour"] = lambda region: REGION_COLOURS.get(region, "#A7B0BC")
    env.filters["usd"] = lambda value: fmt_usd(value) if value else "$0"
    return env


def theme_css() -> str:
    return (TEMPLATE_DIR / "theme.css").read_text(encoding="utf-8")


def inline_styles(page: str) -> str:
    return Premailer(page, keep_style_tags=False, remove_classes=True, cssutils_logging_level=50).transform()


def render_daily(digest: Digest) -> RenderedEmail:
    env = environment()
    page = env.get_template("daily.html.j2").render(d=digest, css=theme_css())
    text = env.get_template("daily.txt.j2").render(d=digest)
    return RenderedEmail(digest.subject, inline_styles(page), text)
