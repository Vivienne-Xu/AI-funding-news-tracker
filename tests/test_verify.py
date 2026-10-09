from datetime import date
from typing import Any

from app.schemas import Extraction
from app.verify import verify

TEXT = (
    "Nimbus Labs raises $12M seed round led by Orchard Ventures\n"
    "The Berlin startup builds AI agents that file expense reports. It is valued at $60M."
)


def make(**overrides: Any) -> Extraction:
    base: dict[str, Any] = {
        "kind": "funding",
        "company": "Nimbus Labs",
        "round_type": "seed",
        "amount": 12_000_000,
        "currency": "USD",
        "lead_investors": ["Orchard Ventures"],
        "hq_city": "Berlin",
        "category": "agent_infra",
        "description": "Builds agents that file expense reports.",
        "confidence": 0.9,
        "evidence": {
            "company": "Nimbus Labs",
            "round_type": "seed round",
            "amount": "raises $12M",
            "lead_investors": "led by Orchard Ventures",
            "hq_city": "The Berlin startup",
        },
    }
    base.update(overrides)
    return Extraction.model_validate(base)


def test_fully_supported_extraction_passes() -> None:
    result = verify(make(), TEXT)
    assert result.reasons == [] and not result.needs_review and result.removed == []
    assert result.data["amount"] == 12_000_000 and result.data["currency"] == "USD"


def test_quote_not_in_text_nulls_the_field_and_flags_review() -> None:
    ev = make().evidence | {"company": "Nimbus Laboratories"}
    result = verify(make(evidence=ev), TEXT)
    assert result.data["company"] is None
    assert "evidence_not_in_text:company" in result.reasons and result.needs_review


def test_missing_quote_nulls_the_field() -> None:
    ev = {k: v for k, v in make().evidence.items() if k != "hq_city"}
    result = verify(make(evidence=ev), TEXT)
    assert result.data["hq_city"] is None and "no_evidence:hq_city" in result.reasons


def test_quote_matching_ignores_line_breaks_and_extra_spaces() -> None:
    ev = make().evidence | {"hq_city": "The   Berlin\nstartup"}
    assert verify(make(evidence=ev), TEXT).data["hq_city"] == "Berlin"


def test_amount_that_disagrees_with_quote_is_removed() -> None:
    result = verify(make(amount=21_000_000), TEXT)
    assert result.data["amount"] is None and result.data["currency"] is None
    assert "amount_mismatch" in result.reasons


def test_wrong_currency_is_a_mismatch() -> None:
    assert verify(make(currency="EUR"), TEXT).data["amount"] is None


def test_amount_without_model_currency_takes_currency_from_quote() -> None:
    assert verify(make(currency=None), TEXT).data["currency"] == "USD"


def test_round_must_be_in_quote() -> None:
    result = verify(make(round_type="series_a"), TEXT)
    assert result.data["round_type"] is None and "round_not_in_quote" in result.reasons


def test_investor_not_in_quote_is_dropped_but_others_stay() -> None:
    result = verify(make(lead_investors=["Orchard Ventures", "Ghost Capital"]), TEXT)
    assert result.data["lead_investors"] == ["Orchard Ventures"]
    assert "investor_not_in_quote:lead_investors" in result.reasons


def test_description_with_unsupported_number_is_removed() -> None:
    result = verify(make(description="Builds agents used by 500 firms."), TEXT)
    assert result.data["description"] is None and "description_has_unsupported_number" in result.reasons


def test_description_too_long_or_with_url_is_removed() -> None:
    assert verify(make(description=" ".join(["word"] * 26)), TEXT).data["description"] is None
    assert verify(make(description="See www.nimbus.test"), TEXT).data["description"] is None


def test_valuation_checked_against_its_quote() -> None:
    good = verify(make(valuation=60e6, evidence=make().evidence | {"valuation": "valued at $60M"}), TEXT)
    assert good.data["valuation"] == 60e6
    bad = verify(make(valuation=600e6, evidence=make().evidence | {"valuation": "valued at $60M"}), TEXT)
    assert bad.data["valuation"] is None


def test_valuation_below_amount_is_flagged() -> None:
    text = TEXT + " Another note: valued at $5M."
    ev = make().evidence | {"valuation": "valued at $5M"}
    assert "valuation_below_amount" in verify(make(valuation=5e6, evidence=ev), text).reasons


def test_implausible_seed_size_is_flagged_not_removed() -> None:
    text = "Nimbus Labs raises $900M seed round"
    ev = {"company": "Nimbus Labs", "round_type": "seed round", "amount": "raises $900M"}
    result = verify(make(amount=900e6, evidence=ev, lead_investors=[], hq_city=None), text)
    assert result.data["amount"] == 900e6 and "amount_unusually_large_for_round" in result.reasons


def test_low_confidence_and_future_date_are_flagged() -> None:
    result = verify(make(confidence=0.4), TEXT, announced=date(2030, 1, 1), today=date(2026, 10, 8))
    assert {"low_confidence", "date_in_future"} <= set(result.reasons)


def test_hedged_text_forces_reported_status() -> None:
    text = "Nimbus Labs reportedly raises $12M seed round"
    ev = {"company": "Nimbus Labs", "amount": "raises $12M"}
    result = verify(make(status="confirmed", evidence=ev, round_type=None, lead_investors=[], hq_city=None), text)
    assert result.data["status"] == "reported"


def test_headwind_needs_event_cue_in_quote() -> None:
    text = "Pixelwise is shutting down after failing to find a buyer"
    base = {"kind": "headwind", "company": "Pixelwise", "event_type": "shutdown", "confidence": 0.9}
    ok = Extraction.model_validate(base | {"evidence": {"company": "Pixelwise", "event_type": "is shutting down"}})
    assert verify(ok, text).data["event_type"] == "shutdown" and not verify(ok, text).needs_review
    # silence or distress is not enough: the quote must contain the event
    bad = Extraction.model_validate(base | {"evidence": {"company": "Pixelwise", "event_type": "failing to find a buyer"}})
    result = verify(bad, text)
    assert result.data["event_type"] is None and "no_confirmed_event" in result.reasons


def test_neither_passes_through_untouched() -> None:
    result = verify(Extraction(kind="neither", confidence=0.9), "anything")
    assert result.kind == "neither" and result.data == {} and not result.needs_review
