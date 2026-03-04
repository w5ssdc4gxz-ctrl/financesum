from app.services.summary_contracts import (
    count_summary_contract_words,
    repair_summary_contract_deterministically,
    validate_summary_contract,
)


def test_validate_summary_contract_accepts_exact_ten_word_summary() -> None:
    text = "Constructive stance: margins, cash conversion, and execution support upside durability."
    report = validate_summary_contract(
        text,
        target_words=10,
        require_single_line=True,
        forbid_markdown_headings=True,
    )
    assert report["reasons"] == []
    assert report["word_count"] == 10


def test_validate_summary_contract_rejects_repetition_slop() -> None:
    report = validate_summary_contract(
        "Risk risk risk remains as margins weaken and cash conversion slips.",
        target_words=10,
        require_single_line=True,
        forbid_markdown_headings=True,
    )
    reasons = set(report["reasons"])
    assert "consecutive_duplicate_token" in reasons
    assert any(r.startswith("repeated_nonstopword_token:") for r in reasons)


def test_validate_summary_contract_rejects_missing_punctuation_and_dangling_ending() -> None:
    report = validate_summary_contract(
        "Margins improved but cash conversion depends on pricing and",
        target_words=8,
        require_single_line=True,
    )
    assert "missing_terminal_punctuation" in report["reasons"]
    assert "dangling_ending_token:and" in report["reasons"]


def test_validate_summary_contract_scales_unique_token_requirement() -> None:
    low_unique = validate_summary_contract(
        "Stable stable stable stable stable margins margins margins improve improve.",
        target_words=10,
    )
    assert "low_unique_token_count" in low_unique["reasons"]

    higher_target = validate_summary_contract(
        " ".join(f"word{i}" for i in range(1, 21)) + ".",
        target_words=20,
    )
    assert "low_unique_token_count" not in higher_target["reasons"]


def test_repair_summary_contract_trims_to_exact_words_without_extra_model_call() -> None:
    text = "Constructive outlook as strong margins and cash conversion support upside durability."
    repaired = repair_summary_contract_deterministically(
        text,
        target_words=10,
        require_single_line=True,
        forbid_markdown_headings=True,
    )
    assert count_summary_contract_words(repaired) == 10
    report = validate_summary_contract(
        repaired,
        target_words=10,
        require_single_line=True,
        forbid_markdown_headings=True,
    )
    assert report["reasons"] == []


def test_repair_summary_contract_leaves_unrepairable_case_invalid() -> None:
    text = "## Executive Summary\nToo short."
    repaired = repair_summary_contract_deterministically(
        text,
        target_words=10,
        require_single_line=True,
        forbid_markdown_headings=True,
    )
    report = validate_summary_contract(
        repaired,
        target_words=10,
        require_single_line=True,
        forbid_markdown_headings=True,
    )
    assert report["reasons"]
