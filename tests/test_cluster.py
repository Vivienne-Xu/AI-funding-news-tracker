from datetime import datetime, timezone

from app.cluster import cluster, company_from_title, normalise_name
from app.collect import RawItem
from app.prefilter import Candidate, Kind

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)


def cand(title: str, source: str = "S", tier: int = 2, summary: str = "", kind: Kind = "funding") -> Candidate:
    item = RawItem(f"https://x.test/{source}/{abs(hash(title))}", source, title, summary, NOW, "US", tier)
    return Candidate(item, kind)


def test_company_from_title() -> None:
    assert company_from_title("Acme AI raises $20M Series A") == "Acme AI"
    assert company_from_title("Exclusive: Mistral AI secures new round") == "Mistral AI"
    assert company_from_title("Acme, backed by Fund X, lands $5M") == "Acme"
    assert company_from_title("Why funding is slowing down") is None


def test_company_from_title_removes_descriptions_and_handles_more_verbs() -> None:
    assert company_from_title("AI computing startup Lambda to raise $4B ahead of IPO") == "Lambda"
    assert company_from_title("Saudi AI startup Echelon secures backing") == "Echelon"
    assert company_from_title("France- and Italy-based Netsec raises over 8M") == "Netsec"
    assert company_from_title("Edtech firm byteXL raises $9M") == "byteXL"
    assert company_from_title("Vinci reels in $250M for its platform") == "Vinci"
    assert company_from_title("Mecka AI reveals $60M Series B") == "Mecka AI"
    assert company_from_title("France\u2019s Rivercell emerges from stealth with \u20ac22M") == "Rivercell"


def test_name_with_extra_description_still_matches_plain_name() -> None:
    clusters = cluster([cand("UK-based InsurTech Konsileo raises 5.9M", "A"), cand("Konsileo raises 5.9M", "B")])
    assert len(clusters) == 1


def test_short_generic_words_do_not_merge_companies() -> None:
    clusters = cluster([cand("Acme Labs raises $5M", "A"), cand("Labs raises $5M", "B")])
    assert len(clusters) == 2


def test_normalise_name() -> None:
    assert normalise_name("Mistral AI") == "mistral"
    assert normalise_name("The Acme Inc.") == "acme"
    assert normalise_name("AI") == "ai"  # never reduce a name to nothing


def test_same_deal_in_three_outlets_is_one_cluster() -> None:
    clusters = cluster(
        [
            cand("Acme AI raises $20M Series A", "TechCrunch", tier=1, summary="short"),
            cand("Acme raises $20 million in Series A funding", "Sifted", tier=1, summary="a much longer lead text"),
            cand("ACME AI raises $19.5M", "e27", tier=2),
        ]
    )
    assert len(clusters) == 1
    assert len(clusters[0].items) == 3
    assert clusters[0].best.source == "Sifted"  # same tier, longer lead text wins
    assert len(clusters[0].urls) == 3


def test_higher_tier_beats_longer_text() -> None:
    clusters = cluster(
        [
            cand("Acme raises $20M", "Blog", tier=3, summary="a very long summary " * 10),
            cand("Acme raises $20M", "Reuters", tier=1, summary="brief"),
        ]
    )
    assert clusters[0].best.source == "Reuters"


def test_different_companies_with_similar_names_stay_apart() -> None:
    clusters = cluster(
        [
            cand("Mistral AI raises $100M", "A"),
            cand("Mistral Labs raises $100M", "B"),
            cand("Cohere raises $100M", "C"),
            cand("Coherent raises $100M", "D"),
        ]
    )
    assert len(clusters) == 4


def test_funding_and_headwind_about_same_company_stay_apart() -> None:
    clusters = cluster([cand("Acme raises $20M", kind="funding"), cand("Acme lays off staff", kind="headwind")])
    assert len(clusters) == 2


def test_headlines_without_company_cluster_by_title_similarity() -> None:
    clusters = cluster(
        [
            cand("Startup funding hits record high in October", "A"),
            cand("Startup funding hits record high in October!", "B"),
            cand("Layoffs weigh on the sector", "C", kind="funding"),
        ]
    )
    assert sorted(len(c.items) for c in clusters) == [1, 2]


def test_largest_cluster_first_and_empty_input() -> None:
    clusters = cluster([cand("Solo raises $1M", "A"), cand("Duo raises $2M", "B"), cand("Duo raises $2M", "C")])
    assert clusters[0].company == "Duo"
    assert cluster([]) == []
