import pytest

from app.parse import Money, find_amounts, parse_headline, round_from_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("raises $20M", Money(20e6, "USD")),
        ("raises $1.5B valuation", Money(1.5e9, "USD")),
        ("$60-million USD Series B", Money(60e6, "USD")),
        ("raises US$20M Series A", Money(20e6, "USD")),
        ("raises over €8.93 million", Money(8.93e6, "EUR")),
        ("lands £4.5m", Money(4.5e6, "GBP")),
        ("Raises ₹32 Cr To Build", Money(32e7, "INR")),
        ("Bags $9 Mn To Take", Money(9e6, "USD")),
        ("a $250,000 grant", Money(250_000, "USD")),
        ("raised 10 million euros", Money(10e6, "EUR")),
        ("EUR 5 million", Money(5e6, "EUR")),
        ("raises $115-million USD Series D", Money(115e6, "USD")),
    ],
)
def test_find_amounts(text: str, expected: Money) -> None:
    assert find_amounts(text)[0] == expected


def test_find_amounts_returns_all_in_order() -> None:
    found = find_amounts("€22 million ($25 million) Seed round")
    assert found == [Money(22e6, "EUR"), Money(25e6, "USD")]


def test_find_amounts_ignores_text_without_money() -> None:
    assert find_amounts("The startup grew 30 percent in 2026") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$12M seed round", "seed"),
        ("a pre-seed round", "pre_seed"),
        ("Series A led by X", "series_a"),
        ("$115M Series D at a valuation", "series_d"),
        ("Series F funding", "series_e_plus"),
        ("a growth round", "growth"),
        ("bridge financing", "bridge"),
        ("$8M seed and $30M Series A", None),  # two rounds: do not guess
        ("raised money", None),
    ],
)
def test_round_from_text(text: str, expected: str | None) -> None:
    assert round_from_text(text) == expected


def test_parse_headline_full_pattern() -> None:
    parsed = parse_headline("Acme AI raises $20M Series A led by Example Ventures")
    assert parsed.company == "Acme AI"
    assert parsed.money == Money(20e6, "USD")
    assert parsed.round_type == "series_a"
    assert parsed.lead_investors == ["Example Ventures"]


def test_parse_headline_multiple_leads_and_verbs() -> None:
    parsed = parse_headline("Beta secures $5M seed round led by Fund One and Fund Two to expand")
    assert parsed.company == "Beta"
    assert parsed.lead_investors == ["Fund One", "Fund Two"]


def test_parse_headline_with_two_amounts_does_not_guess() -> None:
    assert parse_headline("Acme raises $20M after $5M bridge").money is None


def test_parse_headline_with_nothing_clear() -> None:
    parsed = parse_headline("Why startups struggle")
    assert (parsed.company, parsed.money, parsed.round_type, parsed.lead_investors) == (None, None, None, [])
