from app.api.filings import (
    _select_one_shot_contract_failure_requirements,
    _summary_contract_scope_for_target,
    _summary_target_band_payload,
)


def test_one_shot_long_form_only_keeps_fatal_requirements() -> None:
    missing = [
        "Final word-count band violation: expected 630-670, got split=608, stripped=611.",
        "Section balance issue: 'Risk Factors' is underweight (12 words; target ~85±10). Expand it.",
        "Executive Summary must include at least one verified direct quote.",
        "Missing the heading '## Key Metrics'. Use that exact markdown heading (no prefixes) and include substantive content beneath it.",
        "Duplicate section heading detected: '## Risk Factors'. Each required section may appear only once.",
        "Leading-word repetition in Executive Summary: too many sentences start with 'The'.",
    ]

    fatal = _select_one_shot_contract_failure_requirements(
        missing_requirements=missing,
        target_length=650,
    )

    assert fatal == [
        missing[0],
        missing[3],
        missing[4],
    ]


def test_one_shot_micro_keeps_full_contract_requirements() -> None:
    missing = [
        "micro_summary_contract_failed: word_count_mismatch",
        "micro_summary_contract_failed: repeated_nonstopword_token",
    ]

    fatal = _select_one_shot_contract_failure_requirements(
        missing_requirements=missing,
        target_length=10,
    )

    assert fatal == missing
    assert _summary_contract_scope_for_target(10) == "micro_exact"


def test_one_shot_long_form_target_band_payload_is_present() -> None:
    payload = _summary_target_band_payload(650)

    assert payload is not None
    assert payload["lower"] < 650 < payload["upper"]
    assert _summary_contract_scope_for_target(650) == "long_form_one_shot"
    assert _summary_target_band_payload(10) is None

