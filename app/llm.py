"""The only place that talks to the AI model (Google Gemini). Measures tokens for every call."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import errors, types

log = logging.getLogger("app.llm")

# US dollars per million tokens on the PAID plan: (input, output). On the free tier the real
# bill is $0; this table gives the "paid-equivalent" cost so you can see what a paid plan would cost.
# Check against Google's pricing page.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),  # from Google's pricing page, checked 2026-10-08
    "gemini-3.8-flash": (1.50, 7.50),  # optional larger writer (set LLM_MODEL_WRITER). Google's price from 2027-01-01; $0.75 / $3.75 until then (checked 2026-10-08)
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0  # paid-equivalent, see PRICES

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_tokens + other.cached_tokens,
            self.cost_usd + other.cost_usd,
        )


class LLMError(Exception):
    """The model could not give a usable answer, even after one retry. `usage` is what it still used."""

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage or Usage()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES:
        raise LLMError(f"No price known for model '{model}'. Add it to PRICES in app/llm.py.")
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def make_client(api_key: str) -> genai.Client:
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is empty. Put your key in the .env file (see README).")
    return genai.Client(api_key=api_key)


def _least_thinking(model: str) -> types.ThinkingConfig:
    """Gemini 2.x switches thinking off with a budget of 0; Gemini 3.x rejects that and wants a level."""
    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    # The small "flash-lite" models accept MINIMAL; the larger writer model rejects it (tested), so it gets the next level up.
    return types.ThinkingConfig(thinking_level="MINIMAL" if "lite" in model else "LOW")


def extract_json(
    client: genai.Client,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int,
    retry_wait_seconds: float = 3.0,
) -> tuple[dict[str, Any], Usage]:
    """One JSON-only call. One retry if the call fails or the answer is cut off or broken. Never loops."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=0,
        max_output_tokens=max_tokens,
        response_mime_type="application/json",
        response_json_schema=schema,
        thinking_config=_least_thinking(model),  # keep hidden reasoning (billed tokens) to a minimum
    )
    total = Usage()
    last_problem = ""
    for attempt in (1, 2):
        if attempt == 2:
            time.sleep(retry_wait_seconds)  # also gives a rate limit a moment to clear
        try:
            response = client.models.generate_content(model=model, contents=user, config=config)
        except errors.APIError as exc:
            last_problem = f"{type(exc).__name__}: {exc}"
            log.warning("AI call failed (attempt %d): %s", attempt, last_problem)
            continue
        meta = response.usage_metadata
        tokens_in = (meta.prompt_token_count or 0) if meta else 0
        tokens_out = ((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)) if meta else 0
        cached = (meta.cached_content_token_count or 0) if meta else 0
        total += Usage(tokens_in, tokens_out, cached, cost_usd(model, tokens_in, tokens_out))
        finish = response.candidates[0].finish_reason if response.candidates else None
        if getattr(finish, "name", str(finish)) == "MAX_TOKENS":
            last_problem = "answer cut off (reached the output limit)"
        else:
            try:
                answer = json.loads(response.text or "")
            except (json.JSONDecodeError, ValueError):
                last_problem = f"answer was not valid JSON (finish reason: {finish})"
            else:
                if isinstance(answer, dict):
                    return answer, total
                last_problem = "answer was not a JSON object"
        log.warning("AI answer unusable (attempt %d): %s", attempt, last_problem)
    raise LLMError(last_problem, total)
