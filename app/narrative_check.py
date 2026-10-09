"""Checks AI-written prose against the facts it was given (brief Section 3.5). Pure code, no network.

Every number, percentage and capitalised name in the prose must appear in the input facts.
Limits, stated plainly: it does not check the direction of a change ("up" vs "down") or the meaning of a sentence."""

from __future__ import annotations

import json
import re
from typing import Any

NUMBER_RE = re.compile(
    r"(?<![\w.])(?:US\$|C\$|[$€£])?\s?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?"
    r"(?:\s?(?P<unit>%|percent|per cent|billion|bn|B|million|mn|M|thousand|K|k|x|×)(?![A-Za-z]))?"
)
UNIT_FACTOR = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}
NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9&'’]*")
COMMON_CAPITALISED = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "ai", "us", "uk", "eu", "usd", "i", "q1", "q2", "q3", "q4", "ipo",
}  # fmt: skip


def numbers_in(text: str) -> list[tuple[float, float]]:
    """(value, rounding tolerance) for each number in the text. $1.2B -> (1.2e9, 5e7); 35% -> (35, 0.5)."""
    found = []
    for m in NUMBER_RE.finditer(text):
        decimals = len(m.group("dec") or "")
        value = float(m.group("int").replace(",", "") + ("." + m.group("dec") if m.group("dec") else ""))
        factor = UNIT_FACTOR.get((m.group("unit") or "").lower(), 1.0)
        found.append((value * factor, 0.5 * 10 ** (-decimals) * factor + 1e-9))
    return found


def _leaves(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return [x for v in node.values() for x in _leaves(v)]
    if isinstance(node, list):
        return [x for v in node for x in _leaves(v)]
    return [node]


def allowed_numbers(facts: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for leaf in _leaves(facts):
        if isinstance(leaf, bool) or leaf is None:
            continue
        if isinstance(leaf, (int, float)):
            values.append(abs(float(leaf)))
        else:
            values += [v for v, _ in numbers_in(str(leaf))]
    return values


def _norm(token: str) -> str:
    """Lower case, without a possessive: "Mistral's" -> "mistral"."""
    return re.sub(r"['’]s$", "", token.casefold())


def allowed_words(facts: dict[str, Any]) -> set[str]:
    words = {_norm(w) for leaf in _leaves(facts) if isinstance(leaf, str) for w in WORD_RE.findall(leaf)}
    return words | COMMON_CAPITALISED


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check_text(text: str, numbers: list[float], words: set[str]) -> list[str]:
    """Problems found in one piece of prose: numbers or names that are not in the facts."""
    problems = []
    for value, tolerance in numbers_in(text):
        if not any(abs(value - a) <= tolerance for a in numbers):
            problems.append(f"number not in the facts: {value:g}")
    for word in re.findall(r"[A-Za-z]+", text.casefold()):
        if word in NUMBER_WORDS and not any(abs(NUMBER_WORDS[word] - a) < 0.5 for a in numbers):
            problems.append(f"number word not in the facts: {word}")
    for sentence in _sentences(text):
        tokens = WORD_RE.findall(sentence)
        for index, token in enumerate(tokens):
            if not token[0].isupper() or len(token) == 1 or _norm(token) in words:
                continue  # lower case, a single letter ("B" in "Series B"), or a known word
            at_start = index == 0
            starts_a_name = at_start and len(tokens) > 1 and tokens[1][0].isupper()
            if not at_start or starts_a_name:
                problems.append(f"name not in the facts: {token}")
    return problems


def flatten_prose(narrative: dict[str, Any]) -> list[str]:
    return [t for t in _leaves(narrative) if isinstance(t, str)]


def verify_narrative(narrative: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """Empty list = every number and name in the prose is backed by the facts."""
    numbers, words = allowed_numbers(facts), allowed_words(facts)
    problems: list[str] = []
    for text in flatten_prose(narrative):
        problems += check_text(text, numbers, words)
    return sorted(set(problems))


def facts_to_prompt(facts: dict[str, Any]) -> str:
    return json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
