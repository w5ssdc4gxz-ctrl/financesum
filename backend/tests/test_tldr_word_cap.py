from types import SimpleNamespace

import pytest

from app.services import analysis_fallback
from app.services.gemini_client import GeminiClient
from app.services.openai_client import (
    TLDRContractError,
    count_tldr_contract_words,
    normalize_tldr_contract_text,
    validate_tldr_contract,
)


VALID_TLDR = "Constructive outlook: strong margins and cash conversion support upside durability."


def _make_client() -> GeminiClient:
    client = GeminiClient.__new__(GeminiClient)
    client.usage_context = None
    return client


def test_gemini_clamp_tldr_length_max_10_words() -> None:
    client = GeminiClient.__new__(GeminiClient)
    tldr = "one two three four five six seven eight nine ten eleven twelve"
    clamped = client._clamp_tldr_length(tldr, max_words=10)
    assert len(clamped.split()) <= 10


def test_tldr_validator_accepts_exact_ten_word_line() -> None:
    report = validate_tldr_contract(VALID_TLDR)
    assert report["reasons"] == []
    assert report["word_count"] == 10
    assert report["normalized"] == normalize_tldr_contract_text(VALID_TLDR)


def test_tldr_validator_rejects_repetition_slop() -> None:
    report = validate_tldr_contract("Risk risk risk remains as margins weaken and cash slips.")
    reasons = set(report["reasons"])
    assert "consecutive_duplicate_token" in reasons
    assert any(r.startswith("repeated_nonstopword_token:") for r in reasons)


def test_tldr_validator_rejects_low_unique_token_count() -> None:
    report = validate_tldr_contract(
        "Stable stable stable stable stable stable margins margins cash improves."
    )
    assert "low_unique_token_count" in report["reasons"]


def test_tldr_validator_rejects_missing_punctuation() -> None:
    report = validate_tldr_contract(
        "Constructive outlook strong margins and cash conversion support upside durability"
    )
    assert "missing_terminal_punctuation" in report["reasons"]


def test_tldr_validator_rejects_dangling_ending_token() -> None:
    report = validate_tldr_contract(
        "Margins improved, but cash conversion and leverage trends remain and."
    )
    assert "dangling_ending_token:and" in report["reasons"]


def test_exact_tldr_contract_passes_valid_line_without_rewrite() -> None:
    client = _make_client()
    result = client._enforce_exact_tldr_contract(
        tldr=VALID_TLDR,
        company_name="ExampleCo",
        thesis="Strong margins and improving cash conversion.",
        risks="Demand volatility.",
        catalysts="Pricing gains.",
    )
    assert result == VALID_TLDR
    assert count_tldr_contract_words(result) == 10


def test_exact_tldr_contract_rewrites_overlength_to_exact_ten_words(monkeypatch) -> None:
    client = _make_client()
    calls = {"count": 0}

    def fake_generate_with_retry(prompt: str, **kwargs) -> str:
        calls["count"] += 1
        assert "EXACTLY 10 words" in prompt
        return VALID_TLDR

    monkeypatch.setattr(client, "_generate_with_retry", fake_generate_with_retry)

    result = client._enforce_exact_tldr_contract(
        tldr="Constructive outlook: strong margins and cash conversion support upside durability this quarter.",
        company_name="ExampleCo",
        thesis="Margins and conversion are improving.",
        risks="Execution consistency remains the main risk.",
        catalysts="Improved retention and margin expansion.",
    )

    assert calls["count"] == 1
    assert count_tldr_contract_words(result) == 10
    assert result == VALID_TLDR


def test_exact_tldr_contract_rewrites_underlength_or_missing_candidate(monkeypatch) -> None:
    client = _make_client()
    calls = {"count": 0}

    def fake_generate_with_retry(prompt: str, **kwargs) -> str:
        calls["count"] += 1
        assert "If the candidate is missing or unusable, write a fresh verdict" in prompt
        return VALID_TLDR

    monkeypatch.setattr(client, "_generate_with_retry", fake_generate_with_retry)

    result = client._enforce_exact_tldr_contract(
        tldr="",
        company_name="ExampleCo",
        thesis="Profitability is stabilizing while leverage remains manageable.",
        risks="Execution slippage could delay conversion improvements.",
        catalysts="Margin expansion and cash generation upgrades.",
    )

    assert calls["count"] == 1
    assert count_tldr_contract_words(result) == 10


def test_exact_tldr_contract_raises_after_bounded_invalid_rewrites(monkeypatch) -> None:
    client = _make_client()
    calls = {"count": 0}

    def fake_generate_with_retry(prompt: str, **kwargs) -> str:
        calls["count"] += 1
        return "Risk risk risk remains as margins weaken and cash slips."

    monkeypatch.setattr(client, "_generate_with_retry", fake_generate_with_retry)

    with pytest.raises(TLDRContractError):
        client._enforce_exact_tldr_contract(
            tldr="Too short.",
            company_name="ExampleCo",
            thesis="Mixed setup.",
            risks="Execution risk.",
            catalysts="Demand recovery.",
        )

    assert calls["count"] == 5


def test_generate_company_summary_rewrites_tldr_to_exact_ten_words(monkeypatch) -> None:
    client = _make_client()
    sample = (
        "## TL;DR\n"
        "Constructive outlook: strong margins and cash conversion support upside durability this quarter.\n\n"
        "## Investment Thesis\n"
        "Margins improved while cash conversion strengthened.\n\n"
        "## Top 5 Risks\n"
        "Execution slippage could pressure conversion.\n\n"
        "## Catalysts\n"
        "Pricing discipline and better utilization.\n\n"
        "## Key KPIs\n"
        "Operating margin, free cash flow margin, retention, leverage, liquidity.\n"
    )

    monkeypatch.setattr(client, "generate_content", lambda prompt: SimpleNamespace(text=sample))
    monkeypatch.setattr(client, "_generate_with_retry", lambda prompt, **kwargs: VALID_TLDR)

    summary = client.generate_company_summary(
        company_name="ExampleCo",
        financial_data={},
        ratios={},
        health_score=70.0,
        mda_text=None,
        risk_factors_text=None,
    )

    assert summary["tldr"] == VALID_TLDR
    assert count_tldr_contract_words(summary["tldr"]) == 10
    assert "thesis" in summary and "risks" in summary and "catalysts" in summary and "kpis" in summary


def test_generate_company_summary_synthesizes_missing_tldr(monkeypatch) -> None:
    client = _make_client()
    sample = (
        "## Investment Thesis\n"
        "Margins improved while cash conversion strengthened.\n\n"
        "## Top 5 Risks\n"
        "Execution slippage could pressure conversion.\n\n"
        "## Catalysts\n"
        "Pricing discipline and better utilization.\n\n"
        "## Key KPIs\n"
        "Operating margin, free cash flow margin, retention, leverage, liquidity.\n"
    )

    monkeypatch.setattr(client, "generate_content", lambda prompt: SimpleNamespace(text=sample))
    monkeypatch.setattr(client, "_generate_with_retry", lambda prompt, **kwargs: VALID_TLDR)

    summary = client.generate_company_summary(
        company_name="ExampleCo",
        financial_data={},
        ratios={},
        health_score=70.0,
        mda_text=None,
        risk_factors_text=None,
    )

    assert summary["tldr"] == VALID_TLDR
    assert count_tldr_contract_words(summary["tldr"]) == 10


def test_generate_company_summary_retries_when_full_output_under_target_band(monkeypatch) -> None:
    client = _make_client()
    prompts: list[str] = []
    outputs = [
        "under " * 2666,
        "target " * 3000,
    ]

    def fake_generate_content(prompt: str):
        prompts.append(prompt)
        return SimpleNamespace(text=outputs.pop(0))

    monkeypatch.setattr(client, "generate_content", fake_generate_content)
    monkeypatch.setattr(client, "_post_process_summary", lambda text, **kwargs: text)
    monkeypatch.setattr(
        client,
        "_parse_summary_response",
        lambda text: {
            "tldr": "placeholder",
            "thesis": "Thesis.",
            "risks": "Risks.",
            "catalysts": "Catalysts.",
            "kpis": "KPIs.",
        },
    )
    monkeypatch.setattr(client, "_enforce_exact_tldr_contract", lambda **kwargs: VALID_TLDR)

    summary = client.generate_company_summary(
        company_name="ExampleCo",
        financial_data={},
        ratios={},
        health_score=70.0,
        mda_text=None,
        risk_factors_text=None,
        target_length=3000,
    )

    assert summary["tldr"] == VALID_TLDR
    assert len(prompts) == 2
    assert "is BELOW the required band (2980-3020)" in prompts[1]
    assert "Use a code-style word-count check on the FULL output" in prompts[1]
    assert "adjust and recount until the FULL output is within ±20 words of 3000" in prompts[1]


def test_build_summary_prompt_includes_full_output_recount_instruction() -> None:
    client = _make_client()
    prompt = client._build_summary_prompt(
        company_name="ExampleCo",
        financial_data={},
        ratios={},
        health_score=70.0,
        mda_text=None,
        risk_factors_text=None,
        target_length=3000,
        complexity="intermediate",
        variation_token=None,
    )

    assert "use a code-style word-count check on the full structured output" in prompt.lower()
    assert "Adjust and recount the FULL output until it lands within ±20 words of 3000." in prompt


def test_generate_company_summary_tldr_contract_failure_propagates(monkeypatch) -> None:
    client = _make_client()
    sample = (
        "## TL;DR\n"
        "A valid-looking but overlong summary line that still needs rewrite.\n\n"
        "## Investment Thesis\n"
        "Thesis text.\n"
    )

    monkeypatch.setattr(client, "generate_content", lambda prompt: SimpleNamespace(text=sample))

    def fail_contract(**kwargs):
        raise TLDRContractError("contract failed")

    monkeypatch.setattr(client, "_enforce_exact_tldr_contract", fail_contract)

    with pytest.raises(TLDRContractError):
        client.generate_company_summary(
            company_name="ExampleCo",
            financial_data={},
            ratios={},
            health_score=70.0,
            mda_text=None,
            risk_factors_text=None,
        )


def test_fallback_summary_tldr_is_exactly_ten_words_and_punctuated() -> None:
    summary = analysis_fallback._generate_fallback_summary(
        company_name="Very Long Company Name With Many Words Incorporated",
        ratios={},
        health_score=72.3,
        narrative="Test narrative.",
    )
    assert count_tldr_contract_words(summary["tldr"]) == 10
    assert summary["tldr"].rstrip().endswith((".", "!", "?"))
