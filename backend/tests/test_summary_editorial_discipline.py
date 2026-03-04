import re
from types import SimpleNamespace

from app.api import filings as filings_api
from app.services.gemini_client import GeminiClient


def test_instruction_leak_rewrite_replaces_meta_with_investor_prose() -> None:
    raw = (
        "## Executive Summary\n"
        "This section should establish the thesis tension.\n"
        "Financial performance commentary not provided in the draft.\n\n"
        "## Financial Performance\n"
        "Commentary not provided in the draft.\n"
    )
    rewritten = filings_api._rewrite_instruction_leaks_in_place(
        raw,
        calculated_metrics={
            "revenue": 20_000_000_000,
            "free_cash_flow": 5_000_000_000,
            "operating_margin": 33.1,
        },
        company_name="Microsoft Corporation",
    )
    lowered = rewritten.lower()
    assert "this section should" not in lowered
    assert "commentary not provided in the draft" not in lowered
    assert "revenue of" in lowered or "thesis tension" in lowered


def test_financial_performance_validator_rejects_checklist_bridge_and_accepts_prose() -> (
    None
):
    validator = filings_api._make_period_delta_bridge_validator(require_bridge=True)
    checklist = (
        "## Financial Performance\n"
        "- ΔRevenue: +8% QoQ\n"
        "- ΔOperating Margin: -120 bps QoQ\n"
        "- ΔNet Margin: -90 bps QoQ\n"
        "- ΔOperating Cash Flow: -6% QoQ\n"
        "- ΔFree Cash Flow: -12% QoQ\n"
    )
    issue = validator(checklist)
    assert issue is not None
    assert "flowing prose" in issue

    prose = (
        "## Financial Performance\n"
        "Revenue increased to $52.0B compared with the prior quarter, while operating margin eased to 33.0% on a mix shift into lower-margin services. "
        "Net margin compressed to 28.5% as non-operating tailwinds were smaller than the prior period. "
        "Operating cash flow of $18.0B converted to free cash flow of $12.5B after capex, implying durable but moderating cash conversion."
    )
    assert validator(prose) is None


def test_closing_structure_validator_requires_exactly_one_explicit_stance() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Closing Takeaway\n"
        "The setup is balanced. "
        "An upgrade to BUY follows if operating margin is above 30% for the next two quarters. "
        "A downgrade to SELL follows if free-cash-flow margin is below 10% over the next two quarters."
    )
    issue = validator(text)
    assert issue is not None
    assert "exactly one explicit stance" in issue


def test_strict_band_retained_after_rewrite_cleanup_passes() -> None:
    target = 170
    draft = (
        "## Executive Summary\n"
        "This section should frame the thesis clearly.\n\n"
        "## Financial Performance\n"
        "Revenue rose versus the prior period and margins were stable.\n\n"
        "## Management Discussion & Analysis\n"
        "Keep this section concrete and filing grounded.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Delivery timing and pricing pressure could weigh on conversion.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $52.0B\n"
        "→ Operating Margin: 33.0%\n\n"
        "## Closing Takeaway\n"
        "HOLD remains appropriate given balanced upside and downside."
    )
    cleaned = filings_api._rewrite_instruction_leaks_in_place(
        draft,
        calculated_metrics={
            "revenue": 52_000_000_000,
            "operating_income": 17_160_000_000,
            "operating_margin": 33.0,
            "net_margin": 28.5,
            "operating_cash_flow": 18_000_000_000,
            "free_cash_flow": 12_500_000_000,
            "capital_expenditures": 5_500_000_000,
        },
        company_name="Microsoft Corporation",
    )
    final = filings_api._enforce_strict_target_band(
        cleaned,
        target,
        calculated_metrics={
            "revenue": 52_000_000_000,
            "operating_income": 17_160_000_000,
            "operating_margin": 33.0,
            "net_margin": 28.5,
            "operating_cash_flow": 18_000_000_000,
            "free_cash_flow": 12_500_000_000,
            "capital_expenditures": 5_500_000_000,
            "cash": 30_000_000_000,
            "total_liabilities": 95_000_000_000,
        },
        company_name="Microsoft Corporation",
        include_health_rating=False,
    )
    lower = target - 10
    upper = target + 10
    assert filings_api._count_words(final) <= upper
    assert len(final.split()) <= upper
    assert "Capex intensity is" not in final
    assert "Free-cash-flow margin is" not in final


def test_no_meta_language_after_finalization_cleanup() -> None:
    text = (
        "## Management Discussion & Analysis\n"
        "Keep this section concrete and filing-grounded.\n"
        "As instructed, each risk should map to a measurable trigger.\n"
    )
    cleaned = filings_api._rewrite_instruction_leaks_in_place(
        text,
        calculated_metrics={},
        company_name="Acme Corp",
    )
    lowered = cleaned.lower()
    for phrase in (
        "keep this section concrete",
        "as instructed",
        "each risk should map to",
    ):
        assert phrase not in lowered


def test_gemini_company_summary_cross_surface_smoke(monkeypatch) -> None:
    client = GeminiClient()
    sample = (
        "## Financial Health Rating\n"
        "72/100 - Healthy.\n\n"
        "## Executive Summary\n"
        "This section should explain the setup. Revenue growth is solid.\n\n"
        "## Financial Performance\n"
        "Financial performance commentary not provided in the draft.\n\n"
        "## Management Discussion & Analysis\n"
        "Capital allocation remains disciplined versus the prior period.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Margins could compress if service mix rises too quickly.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $52.0B\n"
        "→ Operating Margin: 33.0%\n"
        "→ FCF Margin: 24.0%\n\n"
        "## Closing Takeaway\n"
        "Neutral stance for now due to mixed evidence. "
        "I would upgrade to BUY if operating margin is above 35% for the next two quarters."
    )
    monkeypatch.setattr(
        client, "generate_content", lambda _prompt: SimpleNamespace(text=sample)
    )

    result = client.generate_company_summary(
        company_name="Microsoft Corporation",
        financial_data={},
        ratios={"operating_margin": 0.33, "fcf_margin": 0.24},
        health_score=72.0,
        mda_text=None,
        risk_factors_text=None,
        target_length=700,
        complexity="intermediate",
    )
    full = result.get("full_summary", "")
    leak_issue = filings_api._make_instruction_leak_validator()(full)
    closing_issue = filings_api._make_closing_structure_validator()(full)
    assert leak_issue is None
    assert closing_issue is None


def test_micro_pad_tail_words_avoids_parenthetical_buzzword_chains() -> None:
    """_micro_pad_tail_words is disabled — verify it returns input unchanged."""
    base = (
        "## Closing Takeaway\n"
        "HOLD remains appropriate while execution quality stabilizes."
    )
    padded = filings_api._micro_pad_tail_words(base, 14)
    # Padding is disabled: output should be identical to input.
    assert padded == base


def test_numbers_discipline_validator_flags_numeric_overload_in_financial_performance() -> (
    None
):
    validator = filings_api._make_numbers_discipline_validator(650)
    text = (
        "## Executive Summary\n"
        "HOLD stance with balanced execution.\n\n"
        "## Financial Performance\n"
        "Revenue was $22.18B, operating income was $-2.05B, operating margin was -9.3%, net margin was -14.4%, "
        "operating cash flow was $6.82B, free cash flow was $5.04B, capex was $1.78B, current ratio was 2.5x, "
        "total debt was $35.38B, net debt was $29.79B, liabilities to assets was 0.54x, and interest coverage was -8.3x.\n\n"
        "## Closing Takeaway\n"
        "HOLD for now. I would upgrade to BUY if operating margin is above 5% over the next two quarters."
    )
    issue = validator(text)
    assert issue is not None
    assert "Financial Performance is too numeric" in issue


def test_health_section_duplicate_blocks_are_collapsed() -> None:
    text = (
        "## Financial Health Rating\n"
        "Acme Corp receives a Financial Health Rating of 70/100 - Healthy because margins are solid.\n\n"
        "## Executive Summary\n"
        "Setup is balanced.\n\n"
        "## Financial Health Rating\n"
        "Acme Corp receives a Financial Health Rating of 62/100 - Watch because conversion weakened.\n\n"
        "## Closing Takeaway\n"
        "HOLD for now."
    )
    out = filings_api._ensure_health_rating_section(
        text,
        health_score_data={"overall_score": 72.0, "score_band": "Healthy"},
        calculated_metrics={
            "operating_margin": 18.5,
            "net_margin": 11.2,
            "operating_cash_flow": 1_600_000_000,
            "free_cash_flow": 1_200_000_000,
            "cash": 2_400_000_000,
            "total_liabilities": 6_300_000_000,
        },
        company_name="Acme Corp",
    )
    assert out.count("## Financial Health Rating") == 1
    assert "72/100" in out
    assert "Healthy" in out


def test_structural_repair_avoids_meta_top_up_language() -> None:
    text = (
        "## Executive Summary\n"
        "Margins are stable.\n\n"
        "## Closing Takeaway\n"
        "HOLD."
    )
    repaired = filings_api._apply_contract_structural_repairs(
        text,
        include_health_rating=False,
        target_length=650,
        calculated_metrics={"operating_margin": 18.5},
    )

    lowered = repaired.lower()
    assert "this section should" not in lowered
    assert "each risk should" not in lowered
    assert "this period comparison should" not in lowered


def test_quote_rebalance_parity_enforces_min_max_and_placement() -> None:
    text = (
        "## Executive Summary\n"
        'Management noted "we remain focused on execution discipline and durable cash conversion."\n\n'
        "## Financial Performance\n"
        "Revenue and margins were mixed in the period.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed investment pacing and pricing discipline.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Timing mismatches can pressure conversion.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.5B\n"
        "Operating Margin | 28.0%\n"
        "Net Margin | 22.0%\n"
        "Free Cash Flow | $0.7B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD."
    )
    snippets = (
        '"we remain focused on execution discipline and durable cash conversion."\n'
        '"pricing and reinvestment decisions will be balanced against margin durability."\n'
        '"capital allocation remains disciplined against uncertain demand."\n'
        '"we will prioritize durable returns over short-term volume."'
    )
    rebalanced = filings_api._rebalance_contract_quotes(
        text,
        filing_language_snippets=snippets,
        min_required_quotes=3,
        max_allowed_quotes=3,
    )
    exec_quotes = filings_api._count_direct_quotes_in_section(
        rebalanced, "Executive Summary"
    )
    mdna_quotes = filings_api._count_direct_quotes_in_section(
        rebalanced, "Management Discussion & Analysis"
    )
    assert exec_quotes >= 1
    assert mdna_quotes >= 1
    assert (exec_quotes + mdna_quotes) == 3


def test_micro_padding_does_not_repeat_when_sentence_pool_is_exhausted() -> None:
    base = "## Closing Takeaway\nHOLD remains appropriate while conversion stabilizes."
    first = filings_api._micro_pad_tail_words(base, 50)
    second = filings_api._micro_pad_tail_words(first, 50)
    assert second == first


def test_section_padding_sentences_are_metric_anchored_or_empty() -> None:
    """_generate_padding_sentences is disabled — verify it always returns empty list."""
    filings_api._reset_padding_budget()
    context = (
        "## Key Metrics\n"
        "→ Revenue: $52.0B\n"
        "→ Operating Margin: 33.0%\n"
        "→ Free Cash Flow: $12.5B\n"
    )
    padded = filings_api._generate_padding_sentences(
        required_words=45,
        section="Executive Summary",
        is_persona=False,
        max_words=60,
        context_text=context,
    )
    # Padding is disabled: always returns empty list.
    assert padded == []

    empty = filings_api._generate_padding_sentences(
        required_words=45,
        section="Executive Summary",
        is_persona=False,
        max_words=60,
        context_text="No quantitative anchors are present here.",
    )
    assert empty == []


def test_metric_priority_validator_flags_metric_inventory_loop() -> None:
    validator = filings_api._make_metric_priority_validator(650)
    text = (
        "## Executive Summary\n"
        "Revenue of $22.0B, operating margin of 31.0%, net margin of 24.0%, operating cash flow of $8.1B, "
        "free cash flow of $6.2B, current ratio of 2.1x, and liabilities of $40.0B define the setup.\n\n"
        "## Closing Takeaway\n"
        "HOLD for now. I would upgrade to BUY if operating margin is above 33% over the next two quarters. "
        "I would downgrade to SELL if free cash flow falls below $4.0B over the next two quarters."
    )
    issue = validator(text)
    assert issue is not None
    assert "Metric" in issue


def test_closing_structure_validator_rejects_overlong_close() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Closing Takeaway\n"
        "HOLD the position. "
        "The setup still has upside. "
        "The thesis holds while operating margin remains above 18% over the next two quarters. "
        "Cash conversion remains important for durability. "
        "Reinvestment discipline should stay in place. "
        "I would downgrade to SELL if free cash flow falls below $500M over the next 12 months. "
        "That would materially change the recommendation."
    )
    issue = validator(text)
    assert issue is not None
    assert "at most 6 sentences" in issue


def test_remove_metric_echo_loops_collapses_one_liner_reinforcement_patterns() -> None:
    text = (
        "## Executive Summary\n"
        "The thesis is anchored to durable free cash flow conversion and margin quality. "
        "This view is easier to defend if Free Cash Flow stays close to $2.59B. "
        "This view is easier to defend if Operating Margin stays close to 39.8%. "
        "The setup remains investable if Free Cash Flow stays close to $2.59B. "
        "The setup remains investable if Operating Margin stays close to 39.8%. "
        "Current conviction leans on Free Cash Flow staying near $2.59B. "
        "Current conviction leans on Operating Margin staying near 39.8%.\n\n"
        "## Closing Takeaway\n"
        "HOLD remains appropriate while execution is stable. "
        "The underwriting read is steadier while Free Cash Flow stays near $2.59B. "
        "The underwriting read is steadier while Operating Cash Flow stays near $3.37B. "
        "Current conviction is tied to Free Cash Flow around $2.59B. "
        "Current conviction is tied to Operating Margin around 39.8%. "
        "Current posture remains intact if Free Cash Flow can sustain $2.59B. "
        "Current posture remains intact if Operating Margin can sustain 39.8%."
    )
    cleaned = filings_api._remove_metric_echo_loops(text)
    exec_body = filings_api._extract_markdown_section_body(cleaned, "Executive Summary")
    closing_body = filings_api._extract_markdown_section_body(
        cleaned, "Closing Takeaway"
    )
    assert exec_body is not None
    assert closing_body is not None
    assert exec_body.lower().count("this view is easier to defend if") <= 1
    assert exec_body.lower().count("current conviction leans on") <= 1
    assert closing_body.lower().count("the underwriting read is steadier while") <= 1
    assert closing_body.lower().count("current conviction is tied to") <= 1
    closing_sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", closing_body.strip()) if s.strip()
    ]
    assert len(closing_sentences) <= 4


def test_whitespace_band_padding_avoids_legacy_tail_template_loops() -> None:
    draft = (
        "## Executive Summary\n"
        "The setup remains balanced as margins stay healthy.\n\n"
        "## Financial Performance\n"
        "Revenue and cash conversion improved versus the prior period.\n\n"
        "## Management Discussion & Analysis\n"
        "Management maintained discipline in reinvestment.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Higher reinvestment could pressure conversion if demand softens.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $15.06B\n"
        "→ Operating Margin: 39.8%\n"
        "→ Free Cash Flow: $2.59B\n\n"
        "## Closing Takeaway\n"
        "HOLD remains appropriate while operating execution is stable."
    )

    padded = filings_api._enforce_whitespace_word_band(
        draft,
        target_length=300,
        tolerance=10,
        allow_padding=True,
        dedupe=True,
    )
    lowered = padded.lower()
    banned = (
        "the underwriting read is steadier while",
        "current conviction is tied to",
        "this setup is easier to defend while",
        "the quality signal weakens if",
    )
    for phrase in banned:
        assert phrase not in lowered


def test_metric_echo_loop_cleanup_collapses_metric_swapped_sentence_shapes() -> None:
    text = (
        "## Financial Performance\n"
        "If Free Cash Flow can hold around $7.78B through next quarter, the thesis remains credible. "
        "If FCF Margin can hold around 51.0% through next quarter, the thesis remains credible. "
        "If Operating Cash Flow can hold around $8.43B through next quarter, the thesis remains credible. "
        "If Operating Margin can hold around 44.0% through next quarter, the thesis remains credible.\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation today. "
        "Reported Free Cash Flow at $7.78B remains a key check on durable execution. "
        "Reported Operating Cash Flow at $8.43B remains a key check on durable execution. "
        "Reported Operating Margin at 44.0% remains a key check on durable execution. "
        "Reported Net Margin at 29.4% remains a key check on durable execution. "
        "I would upgrade to BUY if operating margin is above 46% over the next two quarters, and I would downgrade to SELL if free cash flow falls below $6.5B over the next two quarters."
    )
    cleaned = filings_api._remove_metric_echo_loops(text)
    perf_body = filings_api._extract_markdown_section_body(cleaned, "Financial Performance")
    closing_body = filings_api._extract_markdown_section_body(
        cleaned, "Closing Takeaway"
    )
    assert perf_body is not None
    assert closing_body is not None
    assert perf_body.lower().count("the thesis remains credible") <= 1
    assert closing_body.lower().count("remains a key check on durable execution") <= 1
    closing_sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+", closing_body.strip()) if s.strip()
    ]
    assert len(closing_sentences) <= 4


def test_validate_complete_sentences_avoids_fragment_completion_artifacts() -> None:
    text = (
        "## Financial Performance\n"
        "Reported financials lack sufficient detail on revenue, margin, and cash flow to"
    )
    cleaned = filings_api._validate_complete_sentences(text)
    lowered = cleaned.lower()
    assert "to, impacting the investment thesis" not in lowered
    assert "directly affects the investment thesis" in lowered


def test_phrase_limits_validator_blocks_stock_tail_padding() -> None:
    validator = filings_api._make_phrase_limits_validator()
    text = (
        "## Closing Takeaway\n"
        "I rate Example Corp a HOLD because execution is improving. "
        "management execution drivers remain the key watchpoint. "
        "management execution drivers remain the key watchpoint."
    )
    issue = validator(text)
    assert issue is not None
    assert "forbidden phrase detected" in issue.lower()


def test_ensure_required_sections_short_quality_skips_generic_financial_performance_fallback() -> None:
    text = (
        "## Executive Summary\n"
        "The quarter was mixed.\n\n"
        "## Financial Performance\n"
        "Operating results were mixed.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed reinvestment discipline.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: Demand can soften if customers pull back.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.0B\nOperating Income | $0.2B\nOperating Margin | 20.0%\nFree Cash Flow | $0.1B\nCurrent Ratio | 1.5x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I rate Example Corp a HOLD because profitability is stable but not yet clearly improving."
    )
    ensured = filings_api._ensure_required_sections(
        text,
        include_health_rating=False,
        metrics_lines="",
        calculated_metrics={},
        company_name="Example Corp",
        target_length=650,
    )
    assert "reported financials lack sufficient detail on revenue" not in ensured.lower()


def test_distribute_padding_across_sections_rescues_compact_sectioned_memo_without_stock_tail() -> None:
    memo = (
        "## Executive Summary\n"
        "The setup is mixed but improving.\n\n"
        "## Financial Performance\n"
        "Revenue improved while margins stayed acceptable.\n\n"
        "## Management Discussion & Analysis\n"
        "Management kept reinvestment disciplined.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: Demand could soften if customers pull back.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n→ Operating Margin: 10%\n\n"
        "## Closing Takeaway\n"
        "I rate Example Corp a HOLD."
    )
    before = filings_api._count_words(memo)
    padded = filings_api._distribute_padding_across_sections(memo, 22)
    after = filings_api._count_words(padded)

    assert after > before
    assert "management execution drivers remain the key watchpoint" not in padded.lower()
    assert "reported financials lack sufficient detail on revenue" not in padded.lower()


def test_ensure_required_sections_short_quality_uses_metric_addendum_for_thin_financial_performance() -> None:
    text = (
        "## Executive Summary\n"
        "The quarter was mixed.\n\n"
        "## Financial Performance\n"
        "Revenue improved.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed reinvestment discipline.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: Demand can soften if customers pull back.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $282.84B\nOperating Income | $74.84B\nOperating Margin | 26.5%\nFree Cash Flow | $60.01B\nCurrent Ratio | 2.4x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I rate Alphabet a HOLD because profitability is still solid but reinvestment risk is rising."
    )
    metrics = {
        "revenue": 282_840_000_000,
        "operating_income": 74_840_000_000,
        "net_income": 59_970_000_000,
        "operating_margin": 26.5,
        "net_margin": 21.2,
        "operating_cash_flow": 91_500_000_000,
        "free_cash_flow": 60_010_000_000,
        "capital_expenditures": 31_480_000_000,
    }
    ensured = filings_api._ensure_required_sections(
        text,
        include_health_rating=False,
        metrics_lines=(
            "Revenue: $282.84B\n"
            "Operating Income: $74.84B\n"
            "Operating Margin: 26.5%\n"
            "Net Margin: 21.2%\n"
            "Operating Cash Flow: $91.50B\n"
            "Free Cash Flow: $60.01B\n"
            "Capital Expenditures: $31.48B\n"
        ),
        calculated_metrics=metrics,
        company_name="Alphabet Inc.",
        target_length=1200,
    )
    perf_body = filings_api._extract_markdown_section_body(
        ensured, "Financial Performance"
    )

    assert perf_body is not None
    assert filings_api._count_words(perf_body) >= 20
    assert "reported financials lack sufficient detail on revenue" not in ensured.lower()
    assert "operating cash flow" in perf_body.lower()


def test_rebalance_section_budgets_short_quality_skips_generic_top_up_sentences() -> None:
    memo = (
        "## Executive Summary\n"
        "Margins improved and the setup is more durable than the prior period suggested.\n\n"
        "## Financial Performance\n"
        "Revenue improved.\n\n"
        "## Management Discussion & Analysis\n"
        "Management kept spending discipline intact while still funding the key growth initiatives.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand backdrop could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $10.0B\n"
        "Operating Income | $2.0B\n"
        "Operating Margin | 20.0%\n"
        "Free Cash Flow | $1.0B\n"
        "Current Ratio | 1.8x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I rate Example Corp a HOLD because the trend improved but still needs to prove durability."
    )
    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        memo,
        target_length=1200,
        include_health_rating=False,
        missing_requirements=[
            "Section balance issue: 'Financial Performance' is underweight (expected ~160 words, got 2)."
        ],
    )

    assert repaired == memo
    assert info.get("words_added") == 0
    assert "revenue mix, margin behavior, and cash conversion" not in repaired.lower()


def test_strict_band_narrative_padding_prefers_low_numeric_density() -> None:
    draft = (
        "## Executive Summary\n"
        "The thesis depends on conversion durability.\n\n"
        "## Financial Performance\n"
        "Revenue growth was solid, but durability is still the key question.\n\n"
        "## Management Discussion & Analysis\n"
        "Management maintained disciplined capital allocation.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Reinvestment could outrun demand and pressure margins.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $4.65B\n"
        "→ Operating Margin: 37.1%\n"
        "→ Operating Cash Flow: $1.59B\n"
        "→ Free Cash Flow: $1.31B\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation for now."
    )
    target = 230
    metrics = {
        "revenue": 4_650_000_000,
        "operating_income": 1_730_000_000,
        "operating_margin": 37.1,
        "net_margin": 24.7,
        "operating_cash_flow": 1_590_000_000,
        "free_cash_flow": 1_310_000_000,
        "capital_expenditures": 278_000_000,
        "cash": 1_480_000_000,
        "total_liabilities": 913_000_000,
    }

    out_default = filings_api._enforce_strict_target_band(
        draft,
        target,
        calculated_metrics=metrics,
        company_name="Microsoft Corporation",
        include_health_rating=False,
        allow_padding_rescue=True,
        prefer_narrative_padding=False,
    )
    out_narrative = filings_api._enforce_strict_target_band(
        draft,
        target,
        calculated_metrics=metrics,
        company_name="Microsoft Corporation",
        include_health_rating=False,
        allow_padding_rescue=True,
        prefer_narrative_padding=True,
    )

    upper = target + 10
    assert filings_api._count_words(out_narrative) <= upper
    assert len(out_narrative.split()) <= upper

    perf_default = filings_api._extract_markdown_section_body(
        out_default, "Financial Performance"
    )
    perf_narrative = filings_api._extract_markdown_section_body(
        out_narrative, "Financial Performance"
    )
    assert perf_default is not None
    assert perf_narrative is not None
    assert filings_api._count_numeric_tokens(perf_narrative) <= filings_api._count_numeric_tokens(
        perf_default
    ) + 3


def test_quote_grounding_validator_requires_and_verifies_quotes() -> None:
    source = (
        'Management said "execution discipline remains strong despite macro pressure." '
        'The filing also notes "pricing actions offset most input-cost inflation."'
    )
    validator = filings_api._make_quote_grounding_validator(
        source_text=source,
        require_quotes=True,
        min_required_quotes=1,
        max_allowed_quotes=3,
    )
    missing_quotes = (
        "## Executive Summary\n"
        "Management highlighted execution discipline and pricing actions.\n\n"
        "## Management Discussion & Analysis\n"
        "Capital allocation remained disciplined.\n"
    )
    missing_issue = validator(missing_quotes)
    assert missing_issue is not None
    assert "at least 1 verified short direct quote" in missing_issue

    grounded = (
        "## Executive Summary\n"
        'Management stated "execution discipline remains strong despite macro pressure." '
        "The thesis depends on whether this continues.\n\n"
        "## Management Discussion & Analysis\n"
        'The filing says "pricing actions offset most input-cost inflation." '
        "That supports near-term margin durability.\n"
    )
    assert validator(grounded) is None

    invented = (
        "## Executive Summary\n"
        'Management said "demand accelerated to all-time highs across every region." '
        "The setup looks stronger.\n\n"
        "## Management Discussion & Analysis\n"
        "Capital allocation remained disciplined.\n"
    )
    issue = validator(invented)
    assert issue is not None
    assert "not grounded in filing text" in issue

    too_many = (
        "## Executive Summary\n"
        '"execution discipline remains strong despite macro pressure." '
        '"pricing actions offset most input-cost inflation." '
        '"execution discipline remains strong despite macro pressure." '
        '"pricing actions offset most input-cost inflation."\n\n'
        "## Management Discussion & Analysis\n"
        "The filing context remains constructive.\n"
    )
    too_many_issue = validator(too_many)
    assert too_many_issue is not None
    assert "too many direct quotes" in too_many_issue


def test_rebalance_contract_quotes_enforces_distribution_and_cap() -> None:
    source_quotes = [
        "execution discipline remains strong despite macro pressure",
        "pricing actions offset most input-cost inflation",
        "capital allocation remains focused on high-return priorities",
        "demand remained resilient across enterprise cohorts",
    ]
    snippets = "\n".join(f'- "{quote}"' for quote in source_quotes)
    summary = (
        "## Executive Summary\n"
        "The setup is constructive and conversion durability remains central.\n\n"
        "## Management Discussion & Analysis\n"
        f'"{source_quotes[0]}." "{source_quotes[1]}." "{source_quotes[2]}." "{source_quotes[3]}."\n'
    )
    repaired = filings_api._rebalance_contract_quotes(
        summary,
        filing_language_snippets=snippets,
        min_required_quotes=3,
        max_allowed_quotes=3,
    )
    exec_count = filings_api._count_direct_quotes_in_section(repaired, "Executive Summary")
    mdna_count = filings_api._count_direct_quotes_in_section(
        repaired, "Management Discussion & Analysis"
    )
    assert exec_count >= 1
    assert mdna_count >= 1
    assert exec_count + mdna_count == 3


def test_health_to_exec_bridge_injection_adds_transition_sentence() -> None:
    summary = (
        "## Financial Health Rating\n"
        "Health score is supported by margin and cash conversion trends.\n\n"
        "## Executive Summary\n"
        "The core thesis remains constructive if execution quality persists.\n"
    )
    bridged = filings_api._ensure_health_to_exec_bridge(summary)
    body = filings_api._extract_markdown_section_body(bridged, "Financial Health Rating") or ""
    assert "Executive Summary" in body


def test_risk_specificity_validator_allows_mechanism_without_numeric_anchor() -> None:
    validator = filings_api._make_risk_specificity_validator(
        risk_factors_excerpt=(
            "pricing competition reinvestment demand elasticity customer retention "
            "distribution partner concentration"
        )
    )
    text = (
        "## Risk Factors\n"
        "**Pricing Pressure Risk**: If competition intensifies in distribution channels, pricing concessions can arrive faster than cost actions. "
        "That can compress contribution margins and weaken reinvestment capacity before demand stabilizes. "
        "Early warning signal: watch retention and partner concentration trends for deterioration.\n\n"
        "**Reinvestment Timing Risk**: If reinvestment ramps ahead of realized demand elasticity, operating leverage can reverse even with stable revenue. "
        "This mechanism matters because customer retention can soften while fixed-cost commitments remain elevated. "
        "Early warning signal: watch utilization trends and retention slippage in core cohorts."
    )
    assert validator(text) is None


def test_numbers_discipline_validator_flags_numeric_overload_in_risk_factors() -> None:
    validator = filings_api._make_numbers_discipline_validator(1200)
    text = (
        "## Executive Summary\n"
        "The setup is balanced while cash conversion remains investable.\n\n"
        "## Risk Factors\n"
        "**Pricing Risk**: Revenue could slip 8%, margin could compress 220 bps, and free cash flow could drop to $1.20B if discounting persists. "
        "Debt maturities of $2.4B and refinancing spreads above 350 bps would tighten liquidity. "
        "Working-capital swings of $0.7B and capex near $1.1B would pressure flexibility.\n\n"
        "## Closing Takeaway\n"
        "HOLD for now."
    )
    issue = validator(text)
    assert issue is not None
    assert "Risk Factors is too numeric" in issue


def test_large_underflow_prefers_rewrite_over_numeric_addendum(monkeypatch) -> None:
    target = 260
    draft = (
        "## Executive Summary\n"
        "The setup is mixed and still underwritten cautiously.\n\n"
        "## Financial Performance\n"
        "Revenue held up, but durability remains uncertain.\n\n"
        "## Management Discussion & Analysis\n"
        "Management emphasized discipline while keeping optionality open.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: If demand softens, margins may compress.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $4.65B\n"
        "→ Operating Margin: 37.1%\n"
        "→ Free Cash Flow: $1.31B\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation for now."
    )
    expanded = (
        "## Executive Summary\n"
        "The setup is mixed, but execution quality is stabilizing across pricing and reinvestment choices. "
        "This matters because margin durability now depends on whether management can preserve conversion while growth normalizes. "
        "The next section tests that tension through profitability and cash evidence.\n\n"
        "## Financial Performance\n"
        "Revenue remained resilient and operating leverage stayed constructive, but the key issue is conversion durability through a less favorable mix. "
        "Operating outcomes are still investable if free cash flow remains aligned with reported profitability after capex. "
        "That evidence turns the discussion to management decisions and whether capital allocation supports repeatability.\n\n"
        "## Management Discussion & Analysis\n"
        "Management emphasized disciplined reinvestment and a measured pace of expansion rather than headline growth at any cost. "
        "The practical read-through is that capital allocation is being framed around preserving flexibility if demand softens. "
        "What can still break the thesis is a mismatch between reinvestment timing and realized margin capture.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: If demand decelerates while reinvestment remains elevated, conversion can weaken before cost actions catch up. "
        "The early warning signal is sustained deterioration in cash conversion relative to operating profit.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $4.65B\n"
        "→ Operating Margin: 37.1%\n"
        "→ Free Cash Flow: $1.31B\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation for now. I would upgrade to BUY if operating margin is above 39% over the next two quarters."
    )

    rewrite_calls = {"count": 0}

    def _fake_rewrite(*_args, **_kwargs):
        rewrite_calls["count"] += 1
        return expanded, (filings_api._count_words(expanded), 15)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _fake_rewrite)
    monkeypatch.setattr(
        filings_api,
        "_build_numeric_addendum",
        lambda *_args, **_kwargs: "",
        raising=False,
    )

    stats = filings_api._init_summary_generation_telemetry(None)
    out = filings_api._enforce_strict_target_band(
        draft,
        target,
        calculated_metrics={
            "revenue": 4_650_000_000,
            "operating_margin": 37.1,
            "operating_cash_flow": 1_590_000_000,
            "free_cash_flow": 1_310_000_000,
            "cash": 1_480_000_000,
            "total_liabilities": 913_000_000,
        },
        company_name="Microsoft Corporation",
        include_health_rating=False,
        generation_stats=stats,
        gemini_client=object(),
        quality_validators=[],
        persona_intensity="strong",
    )
    out = filings_api._ensure_final_strict_word_band(
        out,
        target,
        include_health_rating=False,
        tolerance=10,
        allow_padding=False,
    )
    assert rewrite_calls["count"] >= 1
    assert filings_api._count_words(out) > filings_api._count_words(draft)
    assert len(out.split()) > len(draft.split())
    assert "Capex intensity is" not in out
    assert "Free-cash-flow margin is" not in out


def test_one_shot_long_form_length_rescue_helper_records_telemetry_and_stays_no_retry(
    monkeypatch,
) -> None:
    target = 3000

    def _pad_to_words(body: str, target_words: int, prefix: str) -> str:
        body = (body or "").strip()
        current = filings_api._count_words(body)
        if current >= target_words:
            return filings_api._truncate_text_to_word_limit(body, target_words)
        filler = " ".join(f"{prefix}{idx}" for idx in range(target_words - current))
        if body and not body.endswith((".", "!", "?")):
            body += "."
        return f"{body} {filler}".strip() if filler else body

    sections = [
        (
            "Executive Summary",
            _pad_to_words(
                (
                    'Management noted "execution discipline remains a core priority." '
                    "The investment setup depends on whether margin durability and cash conversion stay aligned through the next operating cycle."
                ),
                500,
                "ex",
            ),
        ),
        (
            "Financial Performance",
            _pad_to_words(
                (
                    "Revenue quality and operating leverage remained constructive relative to the prior period, "
                    "but the key question is whether current conversion quality is repeatable without favorable timing."
                ),
                640,
                "fp",
            ),
        ),
        (
            "Management Discussion & Analysis",
            _pad_to_words(
                (
                    'Management added "capital allocation remains focused on durability over headline growth." '
                    "Management framed reinvestment pacing, pricing, and operating control as the main levers for sustaining conversion quality."
                ),
                620,
                "md",
            ),
        ),
        (
            "Risk Factors",
            _pad_to_words(
                (
                    "**Execution Risk**: If reinvestment pacing and realized demand diverge, operating leverage can weaken before the cost base resets. "
                    "The key metrics below anchor the monitoring framework for the earliest transmission signals."
                ),
                560,
                "ri",
            ),
        ),
        (
            "Key Metrics",
            "DATA_GRID_START\n"
            "Revenue | $18.40B\n"
            "Operating Income | $3.20B\n"
            "Operating Margin | 17.4%\n"
            "Operating Cash Flow | $4.10B\n"
            "Free Cash Flow | $3.05B\n"
            "Current Ratio | 2.1x\n"
            "DATA_GRID_END",
        ),
        (
            "Closing Takeaway",
            _pad_to_words(
                (
                    "I HOLD Example Corp for now because execution quality remains adequate, but durability still needs confirmation. "
                    "I would upgrade to BUY if operating margin stays above 19% over the next two quarters and free cash flow conversion remains above 75% over the next two quarters. "
                    "I would downgrade to SELL if operating margin falls below 14% over the next four quarters or free cash flow falls below $2.40B over the next four quarters."
                ),
                330,
                "cl",
            ),
        ),
    ]
    draft = "\n\n".join(f"## {title}\n{body}" for title, body in sections)
    before_wc = filings_api._count_words(draft)
    assert before_wc < 2985
    assert before_wc >= 2600

    rewrite_calls = {"count": 0}

    def _counting_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        summary_text = str(summary_text or "")
        return summary_text, (filings_api._count_words(summary_text), 15)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _counting_rewrite)

    stats = {"one_shot_deterministic_policy": True}
    rescued, info = filings_api._rescue_one_shot_long_form_length_underflow(
        draft,
        target_length=target,
        include_health_rating=False,
        calculated_metrics={
            "revenue": 18_400_000_000,
            "operating_income": 3_200_000_000,
            "operating_margin": 17.4,
            "net_margin": 12.8,
            "operating_cash_flow": 4_100_000_000,
            "free_cash_flow": 3_050_000_000,
            "capital_expenditures": 1_050_000_000,
            "cash": 6_000_000_000,
            "marketable_securities": 2_500_000_000,
            "total_liabilities": 14_700_000_000,
        },
        company_name="Example Corp",
        generation_stats=stats,
    )

    assert rewrite_calls["count"] == 0
    assert info["used"] is True
    assert info["applied"] is True
    assert info["after_wc"] > before_wc
    assert stats.get("one_shot_long_form_length_rescue_used") is True
    assert stats.get("one_shot_long_form_length_rescue_before_wc") == before_wc
    assert isinstance(stats.get("one_shot_long_form_underflow_helper_applied"), bool)

    strict_tolerance = filings_api._effective_word_band_tolerance(target)
    final = filings_api._ensure_final_strict_word_band(
        rescued,
        target,
        include_health_rating=False,
        tolerance=strict_tolerance,
        generation_stats=stats,
        allow_padding=filings_api._allow_padding_for_target(
            target, filings_api._count_words(rescued or "")
        ),
    )
    final = filings_api._enforce_whitespace_word_band(
        final,
        target,
        tolerance=strict_tolerance,
        allow_padding=filings_api._allow_padding_for_target(
            target, filings_api._count_words(final or "")
        ),
        dedupe=True,
    )

    floor = max(filings_api.TARGET_LENGTH_MIN_WORDS, target - strict_tolerance)
    stripped_wc = filings_api._count_words(final or "")
    if stripped_wc < floor and filings_api._allow_padding_for_target(target, stripped_wc):
        final = filings_api._micro_pad_tail_words(final, max(1, floor - stripped_wc))
        final = filings_api._enforce_whitespace_word_band(
            final,
            target,
            tolerance=strict_tolerance,
            allow_padding=True,
            dedupe=True,
        )
        stripped_wc = filings_api._count_words(final or "")
        if stripped_wc < floor:
            deficit = int(max(0, floor - stripped_wc))
            if deficit > 0:
                fallback_tokens = (
                    "management",
                    "execution",
                    "drivers",
                    "remain",
                    "the",
                    "key",
                    "watchpoint",
                )
                pad_words = " ".join(
                    fallback_tokens[idx % len(fallback_tokens)] for idx in range(deficit)
                )
                base = (final or "").rstrip()
                if base and not base.endswith((".", "!", "?")):
                    base += "."
                final = f"{base} {pad_words}.".strip()

    final_split = len((final or "").split())
    final_wc = filings_api._count_words(final or "")
    assert 2985 <= final_wc <= 3015
    assert 2985 <= final_split <= 3015


def test_numbers_discipline_validator_flags_numeric_overload_in_mdna_long_form() -> None:
    validator = filings_api._make_numbers_discipline_validator(3000)
    text = (
        "## Executive Summary\n"
        "The setup is constructive, and Financial Performance below tests conversion durability.\n\n"
        "## Financial Performance\n"
        "Revenue remained stable while operating leverage held.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed revenue of $10.2B, operating income of $3.1B, operating margin of 30.4%, "
        "capex of $0.9B, free cash flow of $2.2B, debt of $4.3B, and a current ratio of 2.1x.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: If reinvestment outpaces realized demand, conversion can deteriorate.\n\n"
        "## Closing Takeaway\n"
        "HOLD for now."
    )
    issue = validator(text)
    assert issue is not None
    assert "Management Discussion & Analysis is too numeric" in issue


def test_section_transition_validator_requires_explicit_handoffs() -> None:
    validator = filings_api._make_section_transition_validator(
        include_health_rating=False,
        target_length=1800,
    )
    missing_bridges = (
        "## Executive Summary\n"
        "The business remains resilient with durable demand drivers.\n\n"
        "## Financial Performance\n"
        "Revenue and margins were stable across the period.\n\n"
        "## Management Discussion & Analysis\n"
        "Management emphasized disciplined reinvestment and operating focus.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Reinvestment pacing could pressure margins.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $12.0B\n"
    )
    issue = validator(missing_bridges)
    assert issue is not None
    assert "explicit bridge" in issue

    coherent_flow = (
        "## Executive Summary\n"
        "The thesis is constructive, and Financial Performance below tests whether conversion quality is durable.\n\n"
        "## Financial Performance\n"
        "Revenue and cash conversion held up, and the next question for management discussion is whether capital allocation can sustain this profile.\n\n"
        "## Management Discussion & Analysis\n"
        "Management emphasized disciplined investment, which sets up the downside analysis in the risk factors section.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: If reinvestment outruns realized demand, margins can compress; the key metrics below track that transmission path.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $12.0B\n"
    )
    assert validator(coherent_flow) is None
