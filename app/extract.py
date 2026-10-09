"""Step 5: ask the AI model to read one news item and return facts with word-for-word evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google import genai
from pydantic import ValidationError

from app.config import PROJECT_ROOT
from app.llm import LLMError, Usage, extract_json
from app.parse import ParsedHeadline
from app.schemas import Extraction, tool_schema

log = logging.getLogger("app.extract")

PROMPT_PATH = PROJECT_ROOT / "prompts" / "extract.md"
MAX_LEAD_WORDS = 600
MAX_INPUT_CHARS = 4800  # about 1,200 tokens: the hard cap per item
MAX_OUTPUT_TOKENS = 600


@dataclass
class Outcome:
    source_text: str  # exactly what the model saw (headline, summary, article): evidence is checked against it
    extraction: Extraction | None = None
    usage: Usage = field(default_factory=Usage)
    error: str | None = None
    raw: dict[str, Any] | None = None  # the model's answer as received (stored in the cache)
    from_cache: bool = False


def cache_key(model: str, system_prompt: str, message: str) -> str:
    """Same model + same instructions + same input = same answer, so it is never paid for twice."""
    return hashlib.sha256("\x00".join((model, system_prompt, message)).encode("utf-8")).hexdigest()


def open_cache(path: Path) -> sqlite3.Connection:
    """The answer cache is its own small file, so a dry run (which undoes database changes) still keeps it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS extraction_cache "
        "(cache_key TEXT PRIMARY KEY, url TEXT NOT NULL, raw TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    return conn


def _cache_get(cache: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = cache.execute("SELECT raw FROM extraction_cache WHERE cache_key = ?", (key,)).fetchone()
    return json.loads(row["raw"]) if row else None


def _cache_put(cache: sqlite3.Connection, key: str, url: str, raw: dict[str, Any]) -> None:
    cache.execute(
        "INSERT OR REPLACE INTO extraction_cache (cache_key, url, raw, created_at) VALUES (?, ?, ?, ?)",
        (key, url, json.dumps(raw), datetime.now(timezone.utc).isoformat()),
    )
    cache.commit()


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_input(title: str, summary: str, lead: str, hints: ParsedHeadline | None) -> tuple[str, str]:
    """Returns (message for the model, plain source text used for evidence checks)."""
    lead = " ".join(lead.split()[:MAX_LEAD_WORDS])
    room = MAX_INPUT_CHARS - len(title) - len(summary)
    lead = lead[: max(room, 0)]
    source_text = "\n".join(part for part in (title, summary, lead) if part)
    message = f"HEADLINE: {title}\nSUMMARY: {summary or '(none)'}\n"
    if lead:
        message += f"ARTICLE: {lead}\n"
    if hints:
        parts = []
        if hints.company:
            parts.append(f"company={hints.company}")
        if hints.money:
            parts.append(f"amount={hints.money.amount:g} {hints.money.currency}")
        if hints.round_type:
            parts.append(f"round={hints.round_type}")
        if hints.lead_investors:
            parts.append(f"lead_investors={'; '.join(hints.lead_investors)}")
        if parts:
            message += f"HINTS (from code, may be wrong): {', '.join(parts)}\n"
    return message, source_text


def extract_item(
    client: genai.Client,
    model: str,
    system_prompt: str,
    title: str,
    summary: str,
    lead: str = "",
    hints: ParsedHeadline | None = None,
    retry_wait_seconds: float = 3.0,
    cache: sqlite3.Connection | None = None,
    url: str = "",
    bare_dollar: str = "USD",
) -> Outcome:
    """One call (plus one retry if it fails). Failures are returned as `error`, never raised.

    With a `cache` connection, an identical earlier question is answered for free."""
    message, source_text = build_input(title, summary, lead, hints)
    if bare_dollar != "USD":
        message += f"NOTE: in this publication a bare '$' means {bare_dollar}.\n"
    outcome = Outcome(source_text=source_text)
    key = cache_key(model, system_prompt, message)
    raw = _cache_get(cache, key) if cache else None
    if raw is not None:
        outcome.from_cache = True
    else:
        try:
            raw, outcome.usage = extract_json(
                client, model, system_prompt, message, tool_schema(), MAX_OUTPUT_TOKENS, retry_wait_seconds
            )
        except LLMError as exc:
            outcome.usage, outcome.error = exc.usage, f"ai_call_failed: {exc}"
            return outcome
    try:
        outcome.extraction = Extraction.model_validate(raw)
    except ValidationError as exc:
        outcome.error = f"invalid_answer: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}"
        return outcome
    outcome.raw = raw
    if cache and not outcome.from_cache:
        _cache_put(cache, key, url, raw)
    return outcome
