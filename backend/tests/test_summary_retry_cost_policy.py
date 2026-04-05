import pytest

from app.api import filings as filings_api
from app.services.gemini_exceptions import GeminiAPIError


def test_retry_constants_are_single_shot() -> None:
    assert filings_api.MAX_SUMMARY_ATTEMPTS == 1
    assert filings_api.MAX_REWRITE_ATTEMPTS == 3


def test_generate_summary_tracks_single_generation_call(monkeypatch) -> None:
    def _fake_call(*_args, **_kwargs) -> str:
        return (
            "## Executive Summary\n"
            "Hold stance with mixed evidence.\n\n"
            "## Closing Takeaway\n"
            "HOLD for now."
        )

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)
    class _Client:
        def generate_content(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("should not be called directly in this test")

    stats = filings_api._init_summary_generation_telemetry(None)
    out = filings_api._generate_summary_with_quality_control(
        gemini_client=_Client(),
        base_prompt="Prompt",
        target_length=None,
        quality_validators=None,
        generation_stats=stats,
    )
    assert "Executive Summary" in out
    assert stats["generation_call_count"] == 1
    assert stats["rewrite_call_count"] == 0


def test_rewrite_gate_caps_total_rewrite_calls(monkeypatch) -> None:
    calls = {"count": 0}

    def _fake_call(*_args, **_kwargs) -> str:
        calls["count"] += 1
        body = (
            "## Executive Summary\n"
            "HOLD stance remains intact with stable execution.\n\n"
            "## Closing Takeaway\n"
            "HOLD. The primary risk is margin compression. "
            "I would upgrade to BUY if operating margin is above 20% for the next two quarters. "
            "I would downgrade to SELL if free cash flow falls below $500M in the next 12 months."
        )
        return f"{body}\nWORD COUNT: {filings_api._count_words(body)}"

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)

    stats = filings_api._init_summary_generation_telemetry(None)
    seed = "## Executive Summary\nShort.\n\n## Closing Takeaway\nShort."

    rewritten, _ = filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=seed,
        target_length=80,
        quality_validators=None,
        generation_stats=stats,
    )
    assert calls["count"] == 1
    assert stats["rewrite_call_count"] == 1
    assert stats["rewrite_used"] is True
    assert "WORD COUNT" not in rewritten

    latest = rewritten
    for _ in range(filings_api.MAX_REWRITE_ATTEMPTS - 1):
        latest, _ = filings_api._rewrite_summary_to_length(
            gemini_client=object(),
            summary_text=latest,
            target_length=80,
            quality_validators=None,
            generation_stats=stats,
        )

    assert calls["count"] == filings_api.MAX_REWRITE_ATTEMPTS
    assert stats["rewrite_call_count"] == filings_api.MAX_REWRITE_ATTEMPTS

    # One more call should be blocked by the rewrite gate.
    blocked, _ = filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=latest,
        target_length=80,
        quality_validators=None,
        generation_stats=stats,
    )
    assert calls["count"] == filings_api.MAX_REWRITE_ATTEMPTS
    assert blocked == latest


def test_rewrite_skips_gracefully_when_token_budget_guard_fails(monkeypatch) -> None:
    def _fail_if_called(*_args, **_kwargs) -> str:
        raise AssertionError("rewrite call should be skipped by token budget guard")

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fail_if_called)

    tiny_budget = filings_api.TokenBudget(total_tokens=1, remaining_tokens=1)
    stats = filings_api._init_summary_generation_telemetry(tiny_budget)
    seed = "## Executive Summary\nShort.\n\n## Closing Takeaway\nShort."

    # Rewrite should skip gracefully (return draft) instead of raising.
    result, (word_count, _tolerance) = filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=seed,
        target_length=120,
        quality_validators=None,
        token_budget=tiny_budget,
        generation_stats=stats,
    )
    assert result == seed
    assert stats.get("rewrite_skipped_budget_guard") is True
    assert stats["rewrite_call_count"] == 0


def test_rewrite_token_guard_uses_expected_output_tokens(monkeypatch) -> None:
    def _fake_call(*_args, **_kwargs) -> str:
        body = (
            "## Executive Summary\n"
            "Rewrite path stays inside expected token limits.\n\n"
            "## Closing Takeaway\n"
            "HOLD."
        )
        return f"{body}\nWORD COUNT: {filings_api._count_words(body)}"

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)

    class _ProbeTokenBudget:
        def __init__(self) -> None:
            self.remaining_tokens = 999_999
            self.calls: list[int] = []

        def can_afford(self, _prompt: str, expected_output_tokens: int) -> bool:
            self.calls.append(int(expected_output_tokens))
            return True

        def charge(self, _prompt: str, _output: str) -> int:
            return 0

    probe_budget = _ProbeTokenBudget()
    seed = "## Executive Summary\nShort.\n\n## Closing Takeaway\nShort."
    max_output_tokens = 8_000
    target_length = 120

    filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=seed,
        target_length=target_length,
        quality_validators=None,
        token_budget=probe_budget,  # type: ignore[arg-type]
        max_output_tokens=max_output_tokens,
    )

    expected = filings_api._estimate_summary_output_tokens(
        target_length=target_length,
        max_output_tokens=max_output_tokens,
    )
    assert probe_budget.calls, "Expected token budget guard to run at least once"
    assert probe_budget.calls[0] == expected
    assert expected < max_output_tokens


def test_rewrite_call_uses_expected_output_token_cap(monkeypatch) -> None:
    captured: dict[str, int] = {}

    def _fake_call(*_args, **kwargs) -> str:
        override = kwargs.get("generation_config_override") or {}
        captured["max_output_tokens"] = int(override.get("maxOutputTokens") or 0)
        body = (
            "## Executive Summary\n"
            "Rewrite output should be constrained to expected tokens.\n\n"
            "## Closing Takeaway\n"
            "HOLD."
        )
        return f"{body}\nWORD COUNT: {filings_api._count_words(body)}"

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)

    seed = "## Executive Summary\nShort.\n\n## Closing Takeaway\nShort."
    max_output_tokens = 7_000
    target_length = 220

    filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=seed,
        target_length=target_length,
        quality_validators=None,
        max_output_tokens=max_output_tokens,
    )

    expected = filings_api._estimate_summary_output_tokens(
        target_length=target_length,
        max_output_tokens=max_output_tokens,
    )
    assert captured.get("max_output_tokens") == expected


def test_rewrite_skips_gracefully_when_cost_cap_would_be_crossed(monkeypatch) -> None:
    def _fail_if_called(*_args, **_kwargs) -> str:
        raise AssertionError("rewrite call should not execute when cost budget is exceeded")

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fail_if_called)

    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.005,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
    )
    seed = "## Executive Summary\nShort.\n\n## Closing Takeaway\nShort."
    stats = filings_api._init_summary_generation_telemetry(None)

    # Rewrite should skip gracefully (return draft) instead of raising.
    result, (word_count, _tolerance) = filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=seed,
        target_length=3000,
        quality_validators=None,
        current_words=20,
        cost_budget=cost_budget,
        generation_stats=stats,
    )
    assert result == seed
    assert stats.get("rewrite_skipped_budget_guard") is True


def test_rewrite_rejects_candidate_that_drops_required_sections(monkeypatch) -> None:
    draft = (
        "## Executive Summary\n"
        "Demand remained stable and the next proof point is whether the numbers confirm that setup.\n\n"
        "## Financial Performance\n"
        "Revenue and cash conversion held up, leaving management execution as the next test.\n\n"
        "## Management Discussion & Analysis\n"
        "Management kept investing behind the product roadmap, and the real downside is what happens if execution slips.\n\n"
        "## Risk Factors\n"
        "**Capacity Risk**: Deployment timing could delay monetization. The clearest indicators to watch are utilization and conversion.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $10.0B\n"
        "Operating Margin | 30.0%\n"
        "Free Cash Flow | $2.0B\n"
        "Current Ratio | 1.4x\n"
        "Net Debt | $5.0B\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD while utilization catches up with infrastructure spend."
    )
    bad_rewrite = (
        "## Executive Summary\n"
        "Rewritten opening.\n\n"
        "## Financial Performance\n"
        "Rewritten performance section.\n"
        f"\nWORD COUNT: {filings_api._count_words('Rewritten opening. Rewritten performance section.')}"
    )

    monkeypatch.setattr(
        filings_api,
        "_call_gemini_client",
        lambda *_args, **_kwargs: bad_rewrite,
    )

    result, _ = filings_api._rewrite_summary_to_length(
        gemini_client=object(),
        summary_text=draft,
        target_length=220,
        quality_validators=None,
    )

    assert result == draft


def test_underflow_regeneration_skips_when_optional_retry_would_exceed_budget(
    monkeypatch,
) -> None:
    generation_calls = {"count": 0}

    def _fake_call(*_args, **_kwargs) -> str:
        generation_calls["count"] += 1
        base = (
            "## Executive Summary\n"
            + ("Short evidence. " * 40)
            + "\n\n## Closing Takeaway\n"
            + ("HOLD stance. " * 10)
        )
        return f"{base}\nWORD COUNT: {filings_api._count_words(base)}"

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)

    def _rewrite_passthrough(*args, **kwargs):
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        summary_text = str(summary_text or "")
        return summary_text, (filings_api._count_words(summary_text), 15)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)

    class _Client:
        def generate_content(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("should not be called directly")

    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.10,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
        spent_usd=0.02,
    )

    def _can_afford(prompt: str, _expected_output_tokens: int) -> bool:
        return "*** CRITICAL LENGTH REQUIREMENT ***" not in str(prompt)

    monkeypatch.setattr(cost_budget, "can_afford", _can_afford)
    monkeypatch.setattr(cost_budget, "estimate_call", lambda _prompt, _expected: 0.2)
    monkeypatch.setattr(cost_budget, "charge", lambda _prompt, _output: 0.001)

    stats = filings_api._init_summary_generation_telemetry(None)
    out = filings_api._generate_summary_with_quality_control(
        gemini_client=_Client(),
        base_prompt="Prompt",
        target_length=1000,
        quality_validators=None,
        cost_budget=cost_budget,
        generation_stats=stats,
        max_output_tokens=9000,
    )

    assert isinstance(out, str) and out.strip()
    assert generation_calls["count"] == 1
    assert stats.get("underflow_regeneration_triggered") is True
    assert stats.get("underflow_regeneration_skipped_budget_guard") is True


def test_generation_raises_budget_exceeded_before_call(monkeypatch) -> None:
    def _fail_if_called(*_args, **_kwargs) -> str:
        raise AssertionError("generation call should not execute when cost budget is exceeded")

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fail_if_called)

    class _Client:
        def generate_content(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("should not be called directly")

    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.001,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
    )

    with pytest.raises(filings_api.SummaryBudgetExceededError) as exc_info:
        filings_api._generate_summary_with_quality_control(
            gemini_client=_Client(),
            base_prompt="x " * 10000,
            target_length=2000,
            quality_validators=None,
            cost_budget=cost_budget,
        )

    detail = exc_info.value.detail
    assert detail.get("failure_code") == "SUMMARY_BUDGET_EXCEEDED"
    assert detail.get("stage") == "agent_2_summary_generation"


def test_preflight_estimate_includes_research_prompt_reserve(monkeypatch) -> None:
    budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.10,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
    )
    monkeypatch.setenv("SUMMARY_AGENT1_MAX_OUTPUT_TOKENS", "700")
    monkeypatch.setenv("SUMMARY_RESEARCH_TOKEN_RESERVE", "0")
    estimate_without_reserve = filings_api._estimate_two_agent_summary_cost_preflight(
        company_name="Reserve Corp",
        ticker="RSRV",
        sector="Tech",
        industry="Software",
        filing_type="10-Q",
        base_prompt="x " * 1200,
        target_length=650,
        max_output_tokens=4500,
        rewrite_attempt_cap=2,
        budget=budget,
    )

    monkeypatch.setenv("SUMMARY_RESEARCH_TOKEN_RESERVE", "420")
    estimate_with_reserve = filings_api._estimate_two_agent_summary_cost_preflight(
        company_name="Reserve Corp",
        ticker="RSRV",
        sector="Tech",
        industry="Software",
        filing_type="10-Q",
        base_prompt="x " * 1200,
        target_length=650,
        max_output_tokens=4500,
        rewrite_attempt_cap=2,
        budget=budget,
    )

    assert estimate_with_reserve["research_prompt_reserve_tokens"] == 420.0
    assert (
        estimate_with_reserve["agent2_generation_cost_usd"]
        > estimate_without_reserve["agent2_generation_cost_usd"]
    )


def test_estimate_summary_output_tokens_uses_longform_multiplier_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SUMMARY_PRO_MIN_TARGET_WORDS", "1500")
    monkeypatch.setenv("SUMMARY_OUTPUT_TOKENS_PER_WORD_LONGFORM", "1.6")

    estimated = filings_api._estimate_summary_output_tokens(
        target_length=3000,
        max_output_tokens=9000,
    )

    assert estimated == 4800


def test_compute_generation_prompt_caps_uses_expected_output_tokens() -> None:
    token_budget = filings_api.TokenBudget(total_tokens=16000, remaining_tokens=16000)
    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.10,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
        spent_usd=0.001,
    )

    caps = filings_api._compute_generation_prompt_caps(
        token_budget=token_budget,
        cost_budget=cost_budget,
        expected_output_tokens=6000,
        reserve_tokens=700,
    )

    assert caps["max_prompt_tokens_token_budget"] == 9300
    assert isinstance(caps["max_prompt_tokens_effective"], int)
    assert caps["max_prompt_chars_effective"] == (
        caps["max_prompt_tokens_effective"] * filings_api.CHARS_PER_TOKEN_ESTIMATE
    )


def test_truncate_prompt_to_token_budget_trims_large_data_window() -> None:
    prompt = (
        "Intro header\n"
        "CONTEXT:\n"
        + ("Context line. " * 5000)
        + "\nFINANCIAL SNAPSHOT (Reference only):\n"
        + ("Snapshot line\n" * 2500)
        + "\nKEY METRICS (Use these for calculations and evidence):\n"
        + ("-> Metric line\n" * 1500)
        + "\n\nINSTRUCTIONS:\n"
        "Follow the required section order and strict output contract.\n"
    )

    trimmed = filings_api._truncate_prompt_to_token_budget(
        prompt,
        max_prompt_chars=12_000,
        budget_note="\n\nNote: prompt trimmed.",
    )

    assert len(trimmed) <= 12_000
    assert "INSTRUCTIONS:" in trimmed


def test_prompt_budget_adaptation_applies_non_core_ladder_in_order() -> None:
    template = (
        "Intro section\n"
        "__COMPANY_RESEARCH_BLOCK__\n"
        "CONTEXT:\n"
        + ("Filing context detail. " * 3000)
        + "\nFINANCIAL SNAPSHOT\n"
        "Snapshot data\n"
        "__SPOTLIGHT_CONTEXT_BLOCK__\n"
        "__FILING_LANGUAGE_BLOCK__\n"
        "__RISK_FACTORS_BLOCK__\n"
        "Tail section"
    )
    research_block = "COMPANY BACKGROUND KNOWLEDGE:\n" + ("Research detail. " * 900)
    prompt_component_blocks = {
        "spotlight_context_block": "COMPANY SPOTLIGHT CONTEXT:\n" + ("Spotlight. " * 700),
        "filing_language_block": "FILING LANGUAGE SNIPPETS:\n" + ("Quote line. " * 900),
        "risk_factors_block": "RISK FACTORS EXCERPT:\n" + ("Risk line. " * 1200),
    }
    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.10,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
        spent_usd=0.099,
    )

    with pytest.raises(filings_api.SummaryBudgetExceededError) as exc_info:
        filings_api._build_budget_adapted_summary_prompt(
            base_prompt_template=template,
            company_research_block_placeholder="__COMPANY_RESEARCH_BLOCK__",
            company_research_block=research_block,
            target_length=3000,
            max_output_tokens=4500,
            token_budget=None,
            cost_budget=cost_budget,
            spotlight_context_block_placeholder="__SPOTLIGHT_CONTEXT_BLOCK__",
            filing_language_block_placeholder="__FILING_LANGUAGE_BLOCK__",
            risk_factors_block_placeholder="__RISK_FACTORS_BLOCK__",
            prompt_component_blocks=prompt_component_blocks,
        )

    detail = exc_info.value.detail
    attempts = detail.get("budget_adjustments_attempted") or []
    assert attempts.index("spotlight_context_dropped") < attempts.index("filing_snippets_reduced")
    assert attempts.index("filing_snippets_reduced") < attempts.index("filing_snippets_dropped")
    assert attempts.index("filing_snippets_dropped") < attempts.index("risk_factors_excerpt_reduced")
    assert attempts.index("risk_factors_excerpt_reduced") < attempts.index("risk_factors_excerpt_dropped")


def test_prompt_budget_adaptation_compresses_or_skips_research_when_needed() -> None:
    template = (
        "Intro section\n"
        "__COMPANY_RESEARCH_BLOCK__\n"
        "CONTEXT:\n"
        + ("Filing context detail. " * 5000)
        + "\nFINANCIAL SNAPSHOT\n"
        "Snapshot data"
    )
    research_block = "COMPANY BACKGROUND KNOWLEDGE:\n" + ("Research detail. " * 900)
    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.10,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
        spent_usd=0.0935,
    )

    prompt, research_mode, prompt_budget_adapted, adjustments = (
        filings_api._build_budget_adapted_summary_prompt(
            base_prompt_template=template,
            company_research_block_placeholder="__COMPANY_RESEARCH_BLOCK__",
            company_research_block=research_block,
            target_length=250,
            max_output_tokens=4500,
            token_budget=None,
            cost_budget=cost_budget,
        )
    )

    assert research_mode in {"compressed", "skipped"}
    assert prompt_budget_adapted is True
    assert "research_compressed" in adjustments
    if research_mode == "skipped":
        assert "research_skipped" in adjustments
        assert "Research detail." not in prompt


def test_prompt_budget_adaptation_failure_includes_guidance_fields() -> None:
    template = (
        "Intro section\n"
        "__COMPANY_RESEARCH_BLOCK__\n"
        "CONTEXT:\n"
        + ("Filing context detail. " * 1200)
        + "\nFINANCIAL SNAPSHOT\n"
        "Snapshot data"
    )
    research_block = "COMPANY BACKGROUND KNOWLEDGE:\n" + ("Research detail. " * 500)
    cost_budget = filings_api.SummaryCostBudget(
        budget_cap_usd=0.10,
        input_rate_per_1m=2.5,
        output_rate_per_1m=10.0,
        spent_usd=0.099,
    )

    with pytest.raises(filings_api.SummaryBudgetExceededError) as exc_info:
        filings_api._build_budget_adapted_summary_prompt(
            base_prompt_template=template,
            company_research_block_placeholder="__COMPANY_RESEARCH_BLOCK__",
            company_research_block=research_block,
            target_length=400,
            max_output_tokens=4500,
            token_budget=None,
            cost_budget=cost_budget,
        )

    detail = exc_info.value.detail
    assert detail.get("failure_code") == "SUMMARY_BUDGET_EXCEEDED"
    assert detail.get("stage") == "agent_2_summary_generation"
    assert isinstance(detail.get("guidance"), str) and detail.get("guidance")
    assert int(detail.get("suggested_target_length") or 0) > 0
    assert isinstance(detail.get("budget_adjustments_attempted"), list)
    assert int(detail.get("runtime_expected_output_tokens") or 0) > 0
    assert int(detail.get("runtime_prompt_tokens_estimated") or 0) > 0
    assert detail.get("effective_prompt_token_cap") is not None


def test_model_plan_prefers_primary_when_affordable(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_MODEL_NAME", "gemini-3-pro")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_MODEL_NAME", "gemini-3-flash-preview")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_INPUT_RATE_PER_1M_USD", "0.05")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_OUTPUT_RATE_PER_1M_USD", "0.10")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_INPUT_RATE_PER_1M_USD", "0.04")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_OUTPUT_RATE_PER_1M_USD", "0.08")

    plan = filings_api._plan_summary_model_selection(
        base_prompt="x " * 4000,
        target_length=700,
        max_output_tokens=4500,
    )
    assert plan.primary_model_affordable is True
    assert plan.selected_model == "gemini-3-pro"
    assert plan.fallback_model == "gemini-3-flash-preview"
    assert plan.used_fallback is False


def test_model_plan_uses_fallback_when_primary_unaffordable(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_MODEL_NAME", "gemini-3-pro")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_MODEL_NAME", "gemini-3-flash-preview")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_INPUT_RATE_PER_1M_USD", "100.0")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_OUTPUT_RATE_PER_1M_USD", "200.0")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_INPUT_RATE_PER_1M_USD", "0.02")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_OUTPUT_RATE_PER_1M_USD", "0.05")

    plan = filings_api._plan_summary_model_selection(
        base_prompt="x " * 4000,
        target_length=700,
        max_output_tokens=4500,
    )
    assert plan.primary_model_affordable is False
    assert plan.selected_model == "gemini-3-flash-preview"
    assert plan.used_fallback is True


def test_model_plan_forces_primary_when_pro_only_flag_enabled(monkeypatch) -> None:
    monkeypatch.setenv("SUMMARY_FORCE_PRO_ONLY", "1")
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_MODEL_NAME", "gemini-3-pro")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_MODEL_NAME", "gemini-3-flash-preview")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_INPUT_RATE_PER_1M_USD", "100.0")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_OUTPUT_RATE_PER_1M_USD", "200.0")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_INPUT_RATE_PER_1M_USD", "0.02")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_OUTPUT_RATE_PER_1M_USD", "0.05")

    plan = filings_api._plan_summary_model_selection(
        base_prompt="x " * 4000,
        target_length=700,
        max_output_tokens=4500,
    )
    assert plan.selected_model == "gemini-3-pro"
    assert plan.fallback_model == "gemini-3-pro"
    assert plan.primary_model_affordable is True


def test_model_plan_forces_primary_for_long_form_targets(monkeypatch) -> None:
    monkeypatch.setenv("SUMMARY_PRO_MIN_TARGET_WORDS", "1500")
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_MODEL_NAME", "gemini-3-pro")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_MODEL_NAME", "gemini-3-flash-preview")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_INPUT_RATE_PER_1M_USD", "100.0")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_OUTPUT_RATE_PER_1M_USD", "200.0")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_INPUT_RATE_PER_1M_USD", "0.02")
    monkeypatch.setenv("OPENAI_SUMMARY_FALLBACK_OUTPUT_RATE_PER_1M_USD", "0.05")

    short_plan = filings_api._plan_summary_model_selection(
        base_prompt="x " * 4000,
        target_length=1499,
        max_output_tokens=4500,
    )
    assert short_plan.selected_model == "gemini-3-flash-preview"
    assert short_plan.used_fallback is True

    # Long-form targets now stay on the primary model policy.
    long_plan = filings_api._plan_summary_model_selection(
        base_prompt="x " * 4000,
        target_length=1500,
        max_output_tokens=4500,
    )
    assert long_plan.selected_model == "gemini-3-pro"
    assert long_plan.fallback_model == "gemini-3-pro"
    assert long_plan.used_fallback is False


def test_persona_intensity_downgrades_for_repetition_class_issue(monkeypatch) -> None:
    def _fake_call(*_args, **_kwargs) -> str:
        return (
            "## Executive Summary\n"
            "HOLD stance with balanced risk and reward.\n\n"
            "## Closing Takeaway\n"
            "HOLD for now while monitoring margin durability."
        )

    captured: dict[str, str] = {}

    def _fake_rewrite(*args, persona_intensity: str = "strong", generation_stats=None, **kwargs):
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        if summary_text is None:
            summary_text = ""
        captured["persona_intensity"] = persona_intensity
        if generation_stats is not None:
            generation_stats["rewrite_used"] = True
            generation_stats["rewrite_call_count"] = int(
                generation_stats.get("rewrite_call_count", 0)
            ) + 1
        return summary_text, (
            filings_api._count_words(summary_text),
            filings_api.FINAL_STRICT_WORD_BAND_TOLERANCE,
        )

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _fake_rewrite)

    class _Client:
        def generate_content(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("should not be called directly in this test")

    stats = filings_api._init_summary_generation_telemetry(None)

    out = filings_api._generate_summary_with_quality_control(
        gemini_client=_Client(),
        base_prompt="Prompt",
        target_length=140,
        quality_validators=[lambda _text: "instruction leak detected"],
        generation_stats=stats,
        persona_requested=True,
    )
    assert "Executive Summary" in out
    assert captured["persona_intensity"] == "subtle"
    assert stats["persona_intensity_downgraded"] is True


def test_primary_model_404_is_treated_as_unavailable() -> None:
    exc = GeminiAPIError("Gemini API error: 404", status_code=404)
    assert filings_api._is_primary_model_unavailable_error(exc) is True


def test_fallback_replacement_retry_allowed_for_model_unavailable_when_budget_fits() -> None:
    plan = filings_api.SummaryModelPlan(
        selected_model="gemini-3-pro",
        fallback_model="gemini-3-flash-preview",
        estimated_generation_cost_usd=0.03,
        estimated_rewrite_cost_usd=0.03,
        estimated_total_cost_usd=0.06,
        estimated_fallback_retry_cost_usd=0.02,
        estimated_fallback_total_cost_usd=0.05,
        budget_cap_usd=0.10,
        primary_model_affordable=True,
    )
    exc = GeminiAPIError("Gemini API error: 404", status_code=404)

    assert (
        filings_api._allow_fallback_replacement_retry(
            selected_model_name="gemini-3-pro",
            fallback_model_name="gemini-3-flash-preview",
            model_plan=plan,
            exc=exc,
        )
        is True
    )


def test_fallback_replacement_retry_blocked_when_fallback_path_exceeds_budget() -> None:
    plan = filings_api.SummaryModelPlan(
        selected_model="gemini-3-pro",
        fallback_model="gemini-3-flash-preview",
        estimated_generation_cost_usd=0.03,
        estimated_rewrite_cost_usd=0.03,
        estimated_total_cost_usd=0.06,
        estimated_fallback_retry_cost_usd=0.02,
        estimated_fallback_total_cost_usd=0.12,
        budget_cap_usd=0.10,
        primary_model_affordable=True,
    )
    exc = GeminiAPIError("Gemini API error: 404", status_code=404)

    assert (
        filings_api._allow_fallback_replacement_retry(
            selected_model_name="gemini-3-pro",
            fallback_model_name="gemini-3-flash-preview",
            model_plan=plan,
            exc=exc,
        )
        is False
    )
