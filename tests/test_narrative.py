import copy
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.db import connect
from app.insights import compute_insights
from app.llm import Usage
from app.narrative import SECTIONS, generate_narrative, narrative_facts, template_narrative
from app.narrative_check import verify_narrative
from test_extract import FakeClient, reply
from test_insights import add, seed_october

MODEL = "gemini-3.8-flash"


@pytest.fixture
def october(tmp_path: Path) -> Any:
    conn: sqlite3.Connection = connect(tmp_path / "t.db")
    seed_october(conn)
    return compute_insights(conn, "2026-10")


def draft(**changes: Any) -> dict[str, Any]:
    """A draft that only uses facts from the October fixture."""
    base: dict[str, Any] = {
        "headline": "AI companies raised $160M across 3 deals in October 2026.",
        "takeaways": [
            "Physical AI led with 62.5% of capital.",
            "Sequoia Capital was the most active investor.",
            "Beta Works raised $50M in Europe.",
        ],
        "section_headlines": {s: f"A plain headline about {s}" for s in SECTIONS},
        "section_notes": {s: "" for s in SECTIONS},
    }
    base.update(changes)
    return base


def check(text: str, facts: dict[str, Any]) -> list[str]:
    candidate = draft(headline=text)
    return verify_narrative(candidate, facts)


def test_numbers_and_names_from_the_facts_pass(october: Any) -> None:
    assert verify_narrative(draft(), narrative_facts(october)) == []


def test_invented_number_is_caught(october: Any) -> None:
    assert check("AI companies raised $777M in October 2026.", narrative_facts(october))


def test_invented_company_name_is_caught(october: Any) -> None:
    assert check("Zeta Dynamics led October 2026.", narrative_facts(october))


def test_number_words_are_checked_too(october: Any) -> None:
    facts = narrative_facts(october)
    assert check("There were three deals in October 2026.", facts) == []  # 3 deals exist
    assert check("There were seven deals in October 2026.", facts)


def test_rounded_figures_within_display_precision_pass(october: Any) -> None:
    assert check("Physical AI took about 63% of capital in October 2026.", narrative_facts(october)) == []


def test_template_is_always_safe_and_complete(october: Any) -> None:
    text = template_narrative(october)
    assert text["takeaways"][1] == "The 3 largest rounds make up 100% of capital raised."  # never claims "five" with only 3 deals
    assert "$160M" in text["headline"] and "up 60%" in text["headline"] and len(text["takeaways"]) == 3


def test_template_for_an_empty_month(tmp_path: Path) -> None:
    i = compute_insights(connect(tmp_path / "e.db"), "2026-10")
    assert "No funding deals" in template_narrative(i)["headline"]


def test_good_first_draft_is_used(october: Any) -> None:
    client = FakeClient(reply(draft()))
    result = generate_narrative(client, MODEL, october, retry_wait_seconds=0)  # type: ignore[arg-type]
    assert result.source == "model" and result.text["headline"].startswith("AI companies") and client.calls == 1
    assert result.usage.input_tokens == 1000


def test_bad_draft_is_retried_once_with_the_problems_listed(october: Any) -> None:
    bad = draft(headline="AI companies raised $777M in October 2026.")
    client = FakeClient(reply(bad), reply(draft()))
    result = generate_narrative(client, MODEL, october, retry_wait_seconds=0)  # type: ignore[arg-type]
    assert result.source == "model_retry" and client.calls == 2 and result.problems == []


def test_two_bad_drafts_fall_back_to_the_template(october: Any) -> None:
    bad = draft(headline="AI companies raised $777M in October 2026.")
    client = FakeClient(reply(bad), reply(bad))
    result = generate_narrative(client, MODEL, october, retry_wait_seconds=0)  # type: ignore[arg-type]
    assert result.source == "template" and result.problems and "$160M" in result.text["headline"]
    assert result.usage.input_tokens == 2000  # both attempts are paid for and reported


def test_wrong_shape_counts_as_a_failed_draft(october: Any) -> None:
    broken = copy.deepcopy(draft())
    broken["takeaways"] = ["only one"]
    client = FakeClient(reply(broken), reply(draft()))
    assert generate_narrative(client, MODEL, october, retry_wait_seconds=0).source == "model_retry"  # type: ignore[arg-type]


def test_ai_failure_falls_back_without_a_second_try(october: Any) -> None:
    client = FakeClient(reply("this is not json"), reply("still not json"))
    result = generate_narrative(client, MODEL, october, retry_wait_seconds=0)  # type: ignore[arg-type]
    assert result.source == "template" and result.problems[0].startswith("ai_call_failed")


def test_facts_leave_out_empty_values_and_preformat_money(october: Any) -> None:
    facts = narrative_facts(october)
    assert facts["total_raised"] == "$160M" and "headwinds" not in facts and "deals_without_disclosed_amount" not in facts


def test_usage_type_is_exposed() -> None:
    assert Usage().input_tokens == 0
