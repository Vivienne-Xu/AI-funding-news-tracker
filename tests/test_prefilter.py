import pytest

from app.prefilter import classify


@pytest.mark.parametrize(
    "title",
    [
        "Acme raises $20M to build agents",
        "Acme raised a seed round",
        "Beta announces Series B funding",
        "Gamma valued at $2B in round led by Fund",
        "Delta closes round, valuation doubles",
    ],
)
def test_funding_headlines_are_kept(title: str) -> None:
    assert classify(title, "", needs_ai_match=False) == "funding"


@pytest.mark.parametrize(
    "title",
    [
        "Acme shuts down after failing to raise",
        "Beta lays off 40% of staff",
        "Gamma winding down operations",
        "Delta announces layoffs",
        "Epsilon in down round at lower valuation",
        "Zeta founders join Google in acqui-hire",
        "Eta team joins Microsoft",
    ],
)
def test_headwind_headlines_are_kept(title: str) -> None:
    assert classify(title, "", needs_ai_match=False) == "headwind"


@pytest.mark.parametrize(
    "title",
    [
        "Acme launches new dashboard",
        "Beta hires a new chief marketing officer",
        "Around the world in ten gadgets",  # 'round' inside 'Around' must not match
        "Our favourite TV series returns",  # lowercase 'series' alone must not match
    ],
)
def test_other_headlines_are_dropped(title: str) -> None:
    assert classify(title, "", needs_ai_match=False) is None


def test_summary_text_counts_too() -> None:
    assert classify("Acme news", "The startup raised $5M.", needs_ai_match=False) == "funding"


def test_ai_match_required_for_general_feeds() -> None:
    assert classify("Bakery raises $2M seed", "", needs_ai_match=True) is None
    assert classify("Acme raises $2M seed", "An AI startup.", needs_ai_match=True) == "funding"
    assert classify("Acme raises $2M seed", "A robotics startup.", needs_ai_match=True) == "funding"


def test_ai_match_ignores_lowercase_ai_inside_words() -> None:
    assert classify("Fairness startup raises $2M", "Said the maid.", needs_ai_match=True) is None
