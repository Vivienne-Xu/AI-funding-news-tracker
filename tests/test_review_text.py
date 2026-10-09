from app.review_text import plain_review


def test_known_codes_become_sentences() -> None:
    assert plain_review("Manus (TechCrunch AI): round_not_in_quote") == (
        "Manus (TechCrunch AI): the funding round could not be confirmed from the article."
    )


def test_codes_with_a_field_name_and_several_codes() -> None:
    text = plain_review("Echelon (Wamda): round_not_in_quote, no_evidence:hq_country")
    assert text == (
        "Echelon (Wamda): the funding round could not be confirmed from the article; "
        "no supporting text was found in the article for the headquarters country."
    )
    assert "supporting quote for the funding round was not found" in plain_review("Neela (UKTN): evidence_not_in_text:round_type")


def test_not_stored_prefix_and_repeated_reasons() -> None:
    text = plain_review("Acme (TC): not stored, description_too_long, summary_has_url, no_company")
    assert text == "Acme (TC): not saved: the short description was left out because it failed a check; no company name could be confirmed."


def test_error_messages_are_not_shown_raw() -> None:
    assert plain_review("Acme (TC): ClientError: 429 RESOURCE_EXHAUSTED") == "Acme (TC): the AI could not read this article reliably."


def test_unknown_code_is_still_readable_and_odd_text_is_left_alone() -> None:
    assert plain_review("Acme (TC): brand_new_check") == "Acme (TC): a check failed (brand new check)."
    assert plain_review("no separator here") == "no separator here"
