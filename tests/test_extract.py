import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.evaluate import accuracy, score_case
from app.extract import MAX_INPUT_CHARS, build_input, extract_item
from app.llm import LLMError, cost_usd, extract_json, make_client
from app.parse import parse_headline
from app.schemas import Extraction, tool_schema
from app.verify import Verified

MODEL = "gemini-3.5-flash-lite"
GOOD = {"kind": "funding", "company": "Acme", "confidence": 0.9, "evidence": {"company": "Acme"}}


class FakeClient:
    """Stands in for the Gemini client. Replays the given responses, one per call."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.models = self

    def generate_content(self, **_: Any) -> Any:
        self.calls += 1
        return self.responses.pop(0)


def reply(payload: Any, finish: str = "STOP") -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=0, cached_content_token_count=0
        ),
    )


def call(client: FakeClient) -> Any:
    return extract_json(client, MODEL, "sys", "user", {}, 500, retry_wait_seconds=0)  # type: ignore[arg-type]


def test_cost_for_known_model_and_unknown_model() -> None:
    assert cost_usd(MODEL, 1_000_000, 1_000_000) == pytest.approx(2.80)
    with pytest.raises(LLMError):
        cost_usd("mystery-model", 1, 1)


def test_missing_key_fails_with_a_clear_message() -> None:
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_client("")


def test_extract_json_returns_answer_and_cost() -> None:
    answer, usage = call(FakeClient(reply(GOOD)))
    assert answer["company"] == "Acme"
    assert usage.cost_usd == pytest.approx(0.0008) and usage.input_tokens == 1000 and usage.output_tokens == 200


def test_extract_json_retries_once_then_gives_up() -> None:
    client = FakeClient(reply("{cut", finish="MAX_TOKENS"), reply("not json"), reply(GOOD))
    with pytest.raises(LLMError) as err:
        call(client)
    assert client.calls == 2  # one retry, no loop
    assert err.value.usage.input_tokens == 2000  # failed attempts still counted


def test_extract_json_second_try_can_succeed() -> None:
    answer, usage = call(FakeClient(reply("garbage"), reply(GOOD)))
    assert answer["kind"] == "funding" and usage.input_tokens == 2000


def test_extract_json_rejects_non_object_answers() -> None:
    with pytest.raises(LLMError):
        call(FakeClient(reply("[1, 2]"), reply("[3]")))


def test_extract_item_success_and_source_text() -> None:
    outcome = extract_item(FakeClient(reply(GOOD)), MODEL, "sys", "Acme raises $5M", "A summary.", retry_wait_seconds=0)  # type: ignore[arg-type]
    assert outcome.error is None and outcome.extraction is not None
    assert outcome.source_text == "Acme raises $5M\nA summary."


def test_extract_item_rejects_values_outside_closed_lists() -> None:
    bad = GOOD | {"round_type": "mega_round"}
    outcome = extract_item(FakeClient(reply(bad)), MODEL, "sys", "t", "s", retry_wait_seconds=0)  # type: ignore[arg-type]
    assert outcome.extraction is None and outcome.error and outcome.error.startswith("invalid_answer")
    assert outcome.usage.input_tokens > 0  # the call was still used


def test_extract_item_reports_ai_failure_without_raising() -> None:
    client = FakeClient(reply("x", finish="MAX_TOKENS"), reply("y", finish="MAX_TOKENS"))
    outcome = extract_item(client, MODEL, "sys", "t", "s", retry_wait_seconds=0)  # type: ignore[arg-type]
    assert outcome.extraction is None and outcome.error and outcome.error.startswith("ai_call_failed")


def test_schema_enforces_closed_lists() -> None:
    for field, value in [("category", "magic"), ("event_type", "vanished"), ("kind", "maybe"), ("currency", "usd")]:
        with pytest.raises(ValidationError):
            Extraction.model_validate({"kind": "funding", "confidence": 0.5, field: value})
    with pytest.raises(ValidationError):
        Extraction.model_validate({"kind": "funding", "confidence": 1.5})


def test_tool_schema_lists_the_allowed_values() -> None:
    props = tool_schema()["properties"]
    round_options = props["round_type"]["anyOf"]
    assert "series_a" in round_options[0]["enum"] and {"type": "null"} in round_options
    assert "robotics" in props["category"]["anyOf"][0]["enum"]
    assert "shutdown" in props["event_type"]["anyOf"][0]["enum"]


def test_build_input_caps_length_and_includes_hints() -> None:
    hints = parse_headline("Acme raises $20M Series A led by Example Ventures")
    message, source = build_input("Acme raises $20M Series A led by Example Ventures", "Summary.", "word " * 5000, hints)
    assert len(source) <= MAX_INPUT_CHARS + 100
    assert "HINTS" in message and "amount=2e+07 USD" in message and "round=series_a" in message
    assert "HINTS" not in source  # evidence is checked against the article text only


def test_build_input_without_lead_or_hints() -> None:
    message, source = build_input("Title", "", "", None)
    assert "ARTICLE" not in message and "HINTS" not in message and source == "Title"


def test_scoring_compares_fields_with_tolerances() -> None:
    verified = Verified(
        kind="funding",
        data={"company": "Nous Research Inc.", "amount": 90_000_000.0, "round_type": "series_b",
              "lead_investors": ["robot ventures"], "other_investors": [], "valuation": None},
    )
    expected = {"kind": "funding", "company": "Nous Research", "amount": 90e6, "round_type": "series_b",
                "lead_investors": ["Robot Ventures"], "other_investors": [], "valuation": None}
    results = score_case(expected, verified)
    assert all(r.ok for r in results) and accuracy([results]) == (7, 7)


def test_scoring_flags_invented_values_and_failures() -> None:
    verified = Verified(kind="funding", data={"company": "Acme", "amount": 5e6, "round_type": None})
    results = score_case({"kind": "funding", "company": "Acme", "amount": None}, verified)
    assert [r.ok for r in results] == [True, True, False]
    assert results[2].invented is True
    failed = score_case({"kind": "funding", "company": "Acme"}, None)
    assert not any(r.ok for r in failed)
