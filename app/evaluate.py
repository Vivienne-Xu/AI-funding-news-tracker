"""Scores AI extraction against hand-checked test cases. Used by scripts/eval_extraction.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.cluster import normalise_name
from app.verify import AMOUNT_TOLERANCE, Verified

NUMBER_FIELDS = {"amount", "valuation"}
LIST_FIELDS = {"lead_investors", "other_investors"}


@dataclass(frozen=True)
class FieldResult:
    field: str
    expected: Any
    got: Any
    ok: bool
    invented: bool  # expected nothing, but a value was returned


def _same(field: str, expected: Any, got: Any) -> bool:
    if expected is None or got is None:
        return expected is None and got is None
    if field == "company":
        options = expected if isinstance(expected, list) else [expected]
        return normalise_name(str(got)) in {normalise_name(o) for o in options}
    if field in NUMBER_FIELDS:
        return abs(float(got) - float(expected)) <= AMOUNT_TOLERANCE * float(expected)
    if field in LIST_FIELDS:
        return {n.casefold() for n in got} == {n.casefold() for n in expected}
    return str(got).casefold() == str(expected).casefold()


def _got_value(field: str, verified: Verified | None) -> Any:
    if verified is None:
        return None
    if field == "kind":
        return verified.kind
    value = verified.data.get(field)
    return None if value == [] and field not in LIST_FIELDS else value


def score_case(expected: dict[str, Any], verified: Verified | None) -> list[FieldResult]:
    """One result per expected field. Cases of kind 'neither' only score the kind."""
    results = []
    for field, want in expected.items():
        got = _got_value(field, verified)
        if verified is None and field == "kind":
            got = "(failed)"
        if field in LIST_FIELDS and want == [] and got is None:
            got = []
        ok = _same(field, want, got)
        empty_expected = want is None or want == []
        results.append(FieldResult(field, want, got, ok, invented=empty_expected and bool(got)))
    return results


def accuracy(all_results: list[list[FieldResult]]) -> tuple[int, int]:
    flat = [r for case in all_results for r in case]
    return sum(r.ok for r in flat), len(flat)
