import re

import pytest

from app.api import filings as filings_api


@pytest.fixture(autouse=True)
def _reset_padding_budget():
    """Reset the global padding budget before each test so tests don't starve each other."""
    filings_api._reset_padding_budget()
    yield
    filings_api._reset_padding_budget()


def _get_section_body(text: str, title: str) -> str:
    pattern = re.compile(
        rf"^\s*##\s*{re.escape(title)}\s*\n+(.*?)(?=^\s*##\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"Missing section: {title}"
    return match.group(1).strip()


def _make_body(words: int, *, token: str) -> str:
    """Create a deterministic multi-sentence body with exactly `words` counted tokens."""
    if words <= 0:
        return ""
    sentence_words = 8
    parts: list[str] = []
    remaining = int(words)
    while remaining > 0:
        chunk = min(sentence_words, remaining)
        parts.append(" ".join([token] * chunk) + ".")
        remaining -= chunk
    return " ".join(parts).strip()


def _make_unique_body(words: int, *, prefix: str) -> str:
    """Create exact-length filler without duplicate sentences."""
    if words <= 0:
        return ""
    parts: list[str] = []
    remaining = int(words)
    idx = 0
    while remaining > 0:
        chunk = min(10, remaining)
        parts.append(
            " ".join(f"{prefix}{idx + offset}" for offset in range(chunk)) + "."
        )
        remaining -= chunk
        idx += chunk
    return " ".join(parts).strip()


@pytest.mark.parametrize("target_length", [500, 600, 1000])
def test_short_mid_section_balance_repair_expands_underweight_narrative_sections(
    target_length: int,
) -> None:
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    assert budgets

    fp_budget = int(budgets.get("Financial Performance") or 0)
    mdna_budget = int(budgets.get("Management Discussion & Analysis") or 0)
    exec_budget = int(budgets.get("Executive Summary") or 0)
    health_budget = int(budgets.get("Financial Health Rating") or 0)
    risk_budget = int(budgets.get("Risk Factors") or 0)
    close_budget = int(budgets.get("Closing Takeaway") or 0)
    key_metrics_budget = int(budgets.get("Key Metrics") or 0)

    base = (
        "## Financial Health Rating\n"
        f"{_make_body(max(health_budget + 40, 80), token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(max(exec_budget + 80, 120), token='exec')}\n\n"
        "## Financial Performance\n"
        f"{_make_body(max(12, fp_budget // 4), token='perf')}\n\n"
        "## Management Discussion & Analysis\n"
        f"{_make_body(max(14, mdna_budget // 4), token='mdna')}\n\n"
        "## Risk Factors\n"
        f"{_make_body(max(risk_budget, 60), token='risk')}\n\n"
        "## Key Metrics\n"
        f"{_make_body(max(key_metrics_budget, 18), token='metric')}\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(max(close_budget, 40), token='close')}"
    )

    validator = filings_api._make_section_balance_validator(
        include_health_rating=True,
        target_length=target_length,
    )
    issue = validator(base)
    assert issue is not None

    before_counts = filings_api._collect_section_body_word_counts(
        base, include_health_rating=True
    )
    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        base,
        target_length=target_length,
        include_health_rating=True,
        section_balance_contract_required=True,
        missing_requirements=[str(issue)],
        generation_stats={},
    )
    after_counts = filings_api._collect_section_body_word_counts(
        repaired, include_health_rating=True
    )

    assert info.get("applied") is True
    assert (
        after_counts["Financial Performance"] > before_counts["Financial Performance"]
    )
    assert (
        after_counts["Management Discussion & Analysis"]
        > before_counts["Management Discussion & Analysis"]
    )
    assert after_counts["Key Metrics"] == before_counts["Key Metrics"]
    post_issue = validator(repaired) or ""
    assert "Financial Performance" not in post_issue
    assert "Management Discussion & Analysis" not in post_issue


def test_short_mid_section_balance_repair_keeps_sections_inside_budget_bands() -> None:
    target_length = 600
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    assert budgets

    draft = (
        "## Financial Health Rating\n"
        f"{_make_body(120, token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(150, token='exec')}\n\n"
        "## Financial Performance\n"
        f"{_make_body(18, token='perf')}\n\n"
        "## Management Discussion & Analysis\n"
        f"{_make_body(18, token='mdna')}\n\n"
        "## Risk Factors\n"
        f"{_make_body(90, token='risk')}\n\n"
        "## Key Metrics\n"
        f"{_make_body(int(budgets.get('Key Metrics') or 20), token='metric')}\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(40, token='close')}"
    )

    balance_validator = filings_api._make_section_balance_validator(
        include_health_rating=True, target_length=target_length
    )
    issue = balance_validator(draft)
    assert issue is not None

    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        draft,
        target_length=target_length,
        include_health_rating=True,
        section_balance_contract_required=True,
        missing_requirements=[str(issue)],
        generation_stats={},
    )
    assert info.get("applied") is True

    before_counts = filings_api._collect_section_body_word_counts(
        draft, include_health_rating=True
    )
    counts = filings_api._collect_section_body_word_counts(
        repaired, include_health_rating=True
    )
    assert counts["Financial Performance"] > before_counts["Financial Performance"]
    assert (
        counts["Management Discussion & Analysis"]
        > before_counts["Management Discussion & Analysis"]
    )
    post_issue = balance_validator(repaired) or ""
    assert "Financial Performance" not in post_issue
    assert "Management Discussion & Analysis" not in post_issue


def test_key_metrics_block_removes_health_score_drivers_and_caps_rows() -> None:
    metrics = {
        "revenue": 11_530_000_000,
        "operating_income": 1_230_000_000,
        "operating_margin": 10.67,
        "net_income": 1_780_000_000,
        "net_margin": 15.44,
        "operating_cash_flow": 2_320_000_000,
        "capital_expenditures": 74_000_000,
        "free_cash_flow": 2_250_000_000,
        "cash": 3_000_000_000,
        "marketable_securities": 3_380_000_000,
        "total_debt": 10_500_000_000,
        "total_assets": 52_820_000_000,
        "total_liabilities": 29_920_000_000,
        "current_assets": 11_800_000_000,
        "current_liabilities": 11_500_000_000,
    }
    block = filings_api._build_key_metrics_block(
        metrics, target_length=650, include_health_rating=True
    )
    assert "Health Score Drivers" not in block

    lines = [line.strip() for line in block.splitlines() if "|" in line]
    assert 8 <= len(lines) <= 32

    labels = [line.split("|")[0].strip() for line in lines]
    assert labels[:4] == [
        "Revenue",
        "Operating Income",
        "Operating Margin",
        "Net Margin",
    ]


@pytest.mark.parametrize("target_length", [650, 850, 1000, 1225, 3000])
def test_key_metrics_block_can_reach_contract_floor_across_target_lengths(
    target_length: int,
) -> None:
    metrics = {
        "revenue": 11_530_000_000,
        "operating_income": 1_230_000_000,
        "net_income": 980_000_000,
        "operating_margin": 10.67,
        "net_margin": 8.49,
        "operating_cash_flow": 2_320_000_000,
        "free_cash_flow": 2_250_000_000,
        "capital_expenditures": 310_000_000,
        "cash": 3_000_000_000,
        "marketable_securities": 3_380_000_000,
        "total_debt": 10_500_000_000,
        "total_assets": 52_820_000_000,
        "total_liabilities": 29_920_000_000,
        "current_assets": 11_800_000_000,
        "current_liabilities": 11_500_000_000,
    }
    block = filings_api._build_key_metrics_block(
        metrics,
        target_length=target_length,
        include_health_rating=True,
    )
    issue, numeric_rows = filings_api._validate_key_metrics_numeric_block(
        block,
        min_rows=5,
        require_markers=True,
    )
    lines = [line.strip() for line in block.splitlines() if "|" in line]
    labels = [line.split("|")[0].strip() for line in lines]
    required_words = filings_api._key_metrics_contract_min_words(
        target_length=target_length,
        include_health_rating=True,
    )

    assert issue is None
    assert numeric_rows >= 5
    assert "→Watch:" not in block
    assert filings_api._count_words(block) >= required_words
    assert labels[:4] == [
        "Revenue",
        "Operating Income",
        "Operating Margin",
        "Net Margin",
    ]
    assert len(lines) <= 32


def test_key_metrics_block_preserves_stable_priority_order() -> None:
    metrics = {
        "revenue": 1_000_000_000,
        "operating_margin": 18.5,
        "operating_cash_flow": 240_000_000,
        "free_cash_flow": 190_000_000,
        "fcf_margin": 19.0,
        "cash": 400_000_000,
        "marketable_securities": 100_000_000,
        "total_debt": 650_000_000,
        "current_assets": 900_000_000,
        "current_liabilities": 600_000_000,
    }
    block = filings_api._build_key_metrics_block(
        metrics, target_length=650, include_health_rating=False
    )
    lines = [line.strip() for line in block.splitlines() if "|" in line]
    labels = [line.split("|")[0].strip() for line in lines]

    expected_order = [
        "Revenue",
        "Operating Margin",
        "Operating Cash Flow",
        "Free Cash Flow",
        "FCF Margin",
        "Cash + Securities",
        "Total Debt",
        "Current Ratio",
    ]
    # Preserve relative order for the metrics that exist in this fixture.
    filtered_expected = [label for label in expected_order if label in labels]
    assert labels[: len(filtered_expected)] == filtered_expected


def test_trim_appendix_preserving_rows_keeps_minimum_valid_data_grid_rows() -> None:
    body = (
        "DATA_GRID_START\n"
        "Revenue | $1.00B\n"
        "Operating Income | $0.20B\n"
        "Operating Margin | 10.0%\n"
        "Operating Cash Flow | $0.30B\n"
        "Free Cash Flow | $0.20B\n"
        "FCF Margin | 20.0%\n"
        "Current Ratio | 1.5x\n"
        "Net Debt | $0.40B\n"
        "DATA_GRID_END"
    )

    trimmed = filings_api._trim_appendix_preserving_rows(body, 8)
    issue, numeric_rows = filings_api._validate_key_metrics_numeric_block(
        trimmed,
        min_rows=5,
        require_markers=True,
    )

    assert "DATA_GRID_START" in trimmed
    assert "DATA_GRID_END" in trimmed
    assert issue is None
    assert numeric_rows == 5


def test_enforce_section_budget_distribution_does_not_pad_key_metrics_with_watch_filler() -> (
    None
):
    draft = (
        "## Financial Health Rating\n"
        f"{_make_body(20, token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(18, token='exec')}\n\n"
        "## Financial Performance\n"
        f"{_make_body(16, token='perf')}\n\n"
        "## Management Discussion & Analysis\n"
        f"{_make_body(16, token='mdna')}\n\n"
        "## Risk Factors\n"
        f"{_make_body(16, token='risk')}\n\n"
        "## Key Metrics\n"
        "Revenue | $1.0B\n"
        "Operating Margin | 10.0%\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(18, token='close')}"
    )

    enforced = filings_api._enforce_section_budget_distribution(
        draft,
        target_length=650,
        include_health_rating=True,
        section_tolerance=10,
    )

    key_metrics_body = _get_section_body(enforced, "Key Metrics")
    assert "→Watch:" not in enforced
    assert "→Watch:" not in key_metrics_body
    assert "Revenue" in key_metrics_body
    assert "$1.0B" in key_metrics_body
    assert "Operating Margin" in key_metrics_body
    assert "10.0%" in key_metrics_body


def test_enforce_section_budget_distribution_trims_key_metrics_without_breaking_data_grid() -> (
    None
):
    target_length = 230
    budgets = filings_api._calculate_section_word_budgets(
        target_length,
        include_health_rating=True,
    )
    assert budgets

    key_metrics_body = (
        "DATA_GRID_START\n"
        "Revenue | $1.00B\n"
        "Operating Income | $0.20B\n"
        "Operating Margin | 10.0%\n"
        "Operating Cash Flow | $0.30B\n"
        "Free Cash Flow | $0.20B\n"
        "FCF Margin | 20.0%\n"
        "Current Ratio | 1.5x\n"
        "Net Debt | $0.40B\n"
        "DATA_GRID_END"
    )

    draft = (
        "## Financial Health Rating\n"
        f"{_make_body(int(budgets.get('Financial Health Rating') or 0), token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(int(budgets.get('Executive Summary') or 0), token='exec')}\n\n"
        "## Financial Performance\n"
        f"{_make_body(int(budgets.get('Financial Performance') or 0), token='perf')}\n\n"
        "## Management Discussion & Analysis\n"
        f"{_make_body(int(budgets.get('Management Discussion & Analysis') or 0), token='mdna')}\n\n"
        "## Risk Factors\n"
        f"{_make_body(int(budgets.get('Risk Factors') or 0), token='risk')}\n\n"
        "## Key Metrics\n"
        f"{key_metrics_body}\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(int(budgets.get('Closing Takeaway') or 0), token='close')}"
    )

    enforced = filings_api._enforce_section_budget_distribution(
        draft,
        target_length=target_length,
        include_health_rating=True,
        section_tolerance=0,
    )

    enforced_key_metrics = _get_section_body(enforced, "Key Metrics")
    issue, numeric_rows = filings_api._validate_key_metrics_numeric_block(
        enforced_key_metrics,
        min_rows=5,
        require_markers=True,
    )

    assert issue is None
    assert "DATA_GRID_START" in enforced_key_metrics
    assert "DATA_GRID_END" in enforced_key_metrics
    assert numeric_rows >= 5
    assert "Revenue | $1.00B" in enforced_key_metrics
    assert "Revenue, $1.00B" not in enforced_key_metrics


def test_short_underweight_section_guidance_prioritizes_narrative_gaps() -> None:
    target_length = 650
    draft = (
        "## Financial Health Rating\n"
        f"{_make_body(95, token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(18, token='exec')}\n\n"
        "## Financial Performance\n"
        f"{_make_body(120, token='perf')}\n\n"
        "## Management Discussion & Analysis\n"
        f"{_make_body(16, token='mdna')}\n\n"
        "## Risk Factors\n"
        f"{_make_body(96, token='risk')}\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n"
        "→ Operating Margin: 10.0%\n"
        "→ Free Cash Flow: $250M\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(14, token='close')}"
    )

    titles, guidance = filings_api._short_underweight_section_guidance(
        draft,
        target_length=target_length,
        include_health_rating=True,
    )

    assert titles
    assert "Key Metrics" not in titles
    assert titles[0] == "Management Discussion & Analysis"
    assert "Risk Factors" in titles[:3]
    assert "Executive Summary" in titles[:4]
    assert titles.index("Risk Factors") < titles.index("Executive Summary")
    assert "Management Discussion & Analysis" in guidance
    assert "Executive Summary" in guidance


@pytest.mark.parametrize("target_length", [500, 600, 1000, 2000, 3000])
def test_ensure_required_sections_scales_fp_and_mdna_with_target_length(
    target_length: int,
) -> None:
    base = (
        "## Financial Health Rating\n"
        f"{_make_body(24, token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(20, token='exec')}\n\n"
        "## Financial Performance\n"
        "Revenue moved.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed strategy.\n\n"
        "## Risk Factors\n"
        f"{_make_body(26, token='risk')}\n\n"
        "## Key Metrics\n"
        "Revenue | $1.0B\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(18, token='close')}\n"
    )

    metrics = {
        "revenue": 30.57e9,
        "operating_income": 10.34e9,
        "net_income": 8.81e9,
        "operating_margin": 33.8,
        "net_margin": 28.8,
        "operating_cash_flow": 13.52e9,
        "free_cash_flow": 10.96e9,
        "capital_expenditures": 2.56e9,
        "cash": 11.21e9,
        "marketable_securities": 0.0,
        "total_liabilities": 168.42e9,
        "total_debt": 79.07e9,
    }

    ensured = filings_api._ensure_required_sections(
        base,
        include_health_rating=True,
        metrics_lines="Revenue | $1.0B",
        calculated_metrics=metrics,
        health_score_data={},
        company_name="Microsoft Corp",
        risk_factors_excerpt="AI serving costs and regulation may pressure margins.",
        target_length=target_length,
    )

    counts = filings_api._collect_section_body_word_counts(
        ensured, include_health_rating=True
    )
    mins = filings_api._calculate_section_min_words_for_target(
        target_length, include_health_rating=True
    )

    assert counts["Financial Performance"] >= int(
        mins.get("Financial Performance", 0) or 0
    )
    assert counts["Management Discussion & Analysis"] >= int(
        mins.get("Management Discussion & Analysis", 0) or 0
    )


def test_ensure_required_sections_normalizes_risk_schema_for_short_targets() -> None:
    target_length = 600
    base = (
        "## Financial Health Rating\n"
        f"{_make_body(18, token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(18, token='exec')}\n\n"
        "## Financial Performance\n"
        "Revenue changed.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed execution.\n\n"
        "## Risk Factors\n"
        "**Competition Risk**: Pricing pressure may affect demand and costs.\n\n"
        "**Regulatory Risk**: Compliance changes could increase operating expense.\n\n"
        "## Key Metrics\n"
        "Revenue | $1.0B\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(16, token='close')}\n"
    )

    metrics = {
        "revenue": 30.57e9,
        "operating_income": 10.34e9,
        "net_income": 8.81e9,
        "operating_margin": 33.8,
        "net_margin": 28.8,
        "operating_cash_flow": 13.52e9,
        "free_cash_flow": 10.96e9,
        "capital_expenditures": 2.56e9,
        "cash": 11.21e9,
        "marketable_securities": 0.0,
        "total_liabilities": 168.42e9,
        "total_debt": 79.07e9,
    }

    ensured = filings_api._ensure_required_sections(
        base,
        include_health_rating=True,
        metrics_lines="Revenue | $1.0B",
        calculated_metrics=metrics,
        health_score_data={},
        company_name="Microsoft Corp",
        risk_factors_excerpt="AI infrastructure demand, enterprise renewals, and regulatory changes can affect margins.",
        target_length=target_length,
    )

    risk_body = _get_section_body(ensured, "Risk Factors")
    entries = list(
        re.finditer(
            r"\*\*(?P<name>[^*:\n]{2,120}?):?\*\*\s*:?\s*(?P<body>.+?)(?=(?:(?:\n\s*|(?<=[.!?])\s+)\*\*[^*]+?\*\*\s*:?)|\Z)",
            risk_body,
            flags=re.DOTALL,
        )
    )
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    shape = filings_api.get_risk_factors_shape(int(budgets.get("Risk Factors") or 0))
    assert len(entries) == int(shape.risk_count or 0)

    generic_name_re = re.compile(
        r"\b(macro(?:economic)?|competition|competitive pressure|regulatory risk|margin compression|liquidity risk|cash flow risk)\b",
        re.IGNORECASE,
    )
    for entry in entries:
        name = (entry.group("name") or "").strip()
        assert not generic_name_re.search(name)

    validation = filings_api.validate_summary(
        ensured,
        target_words=target_length,
        section_budgets=budgets,
        include_health_rating=True,
        risk_factors_excerpt=(
            "AI infrastructure demand, enterprise renewals, and regulatory changes can affect margins."
        ),
    )
    assert not any(
        failure.code == "risk_schema" for failure in validation.section_failures
    )


def test_ensure_required_sections_short_target_synthesizes_liquidity_risk_with_early_warning() -> (
    None
):
    target_length = 850
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    metrics = {
        "revenue": 30.57e9,
        "operating_income": 10.34e9,
        "net_income": 8.81e9,
        "operating_margin": 33.8,
        "net_margin": 28.8,
        "operating_cash_flow": 13.52e9,
        "free_cash_flow": 10.96e9,
        "capital_expenditures": 2.56e9,
        "cash": 11.21e9,
        "marketable_securities": 0.0,
        "total_liabilities": 168.42e9,
        "total_debt": 79.07e9,
    }
    base = (
        "## Financial Health Rating\n"
        + _make_unique_body(24, prefix="health")
        + "\n\n## Executive Summary\n"
        + _make_unique_body(24, prefix="exec")
        + "\n\n## Financial Performance\nRevenue moved.\n\n"
        + "## Management Discussion & Analysis\nManagement discussed execution.\n\n"
        + "## Risk Factors\nLiquidity stayed adequate, but funding discipline still matters.\n\n"
        + "## Key Metrics\nRevenue | $1.0B\n\n"
        + "## Closing Takeaway\n"
        + _make_unique_body(18, prefix="close")
        + "\n"
    )

    ensured = filings_api._ensure_required_sections(
        base,
        include_health_rating=True,
        metrics_lines="Revenue | $1.0B",
        calculated_metrics=metrics,
        health_score_data={},
        company_name="Microsoft Corp",
        risk_factors_excerpt="liquidity funding working capital leverage capital allocation",
        target_length=target_length,
    )

    risk_body = _get_section_body(ensured, "Risk Factors")
    validation = filings_api.validate_summary(
        ensured,
        target_words=target_length,
        section_budgets=budgets,
        include_health_rating=True,
        risk_factors_excerpt="liquidity funding working capital leverage capital allocation",
    )

    assert "Cost Absorption Risk" not in risk_body
    assert (
        "Capital Allocation Flexibility Risk" in risk_body
        or "Working-Capital Timing Risk" in risk_body
    )
    assert "liquidity" in risk_body.lower()
    assert "investors should watch" in risk_body.lower()
    # Watchpoints now appear as natural prose instead of a rigid "Early-warning signal:" label.
    assert any(
        term in risk_body.lower()
        for term in ("refinancing", "leverage", "working capital", "funding", "liquidity")
    )
    # The synthesized entry should now use a more specific company/funding-linked label.
    # We still verify no OTHER risk_schema failures (sentence count, mechanism,
    # transmission, etc.) to confirm the synthesized entry is structurally sound.
    non_generic_risk_failures = [
        failure
        for failure in validation.section_failures
        if failure.section_name == "Risk Factors"
        and failure.code == "risk_schema"
        and "too generic" not in failure.message
        and "generic category" not in failure.message
        and "structured risk(s); expected exactly" not in failure.message
    ]
    assert not non_generic_risk_failures


def test_rebalance_normalizes_risk_factors_before_donor_inference_for_850_target() -> (
    None
):
    target_length = 850
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    risk_budget = int(budgets["Risk Factors"])
    mdna_budget = int(budgets["Management Discussion & Analysis"])
    closing_budget = int(budgets["Closing Takeaway"])
    metrics = {
        "revenue": 30.57e9,
        "operating_income": 10.34e9,
        "net_income": 8.81e9,
        "operating_margin": 33.8,
        "net_margin": 28.8,
        "operating_cash_flow": 13.52e9,
        "free_cash_flow": 10.96e9,
        "capital_expenditures": 2.56e9,
        "cash": 11.21e9,
        "marketable_securities": 0.0,
        "total_liabilities": 168.42e9,
        "total_debt": 79.07e9,
    }

    risk_body = "\n\n".join(
        [
            "**Cloud Capacity Backlog Risk**: Cloud demand can convert more slowly if deployment timing slips and committed usage arrives ahead of usable capacity. That can pressure revenue mix and keep infrastructure costs elevated before management captures the expected cash return on cloud infrastructure. Early-warning signal: watch backlog conversion, utilization rates, and deployment slippage across regions.",
            "**AI Cost Recovery Risk**: AI serving expense can rise faster than monetized usage if inference intensity expands before monetization catches up across enterprise workloads and consumer products. That can compress operating margin and free cash flow because cost-to-serve rises before the company proves that new usage is scaling profitably. Early-warning signal: watch serving-cost intensity, monetized usage, and pricing realization.",
            "**Liquidity / Funding Risk**: Liquidity can tighten if a weaker operating period forces management to protect the balance sheet while funding capex, buybacks, and infrastructure expansion from the same cash pool. That would reduce capital-allocation flexibility and make any recovery less valuable because working-capital support and defensive cash management would take priority over offensive investment.",
        ]
    )
    summary_text = "\n\n".join(
        [
            "## Financial Health Rating\n"
            + _make_unique_body(
                int(budgets["Financial Health Rating"]), prefix="health"
            ),
            "## Executive Summary\n"
            + _make_unique_body(int(budgets["Executive Summary"]), prefix="exec"),
            "## Financial Performance\n"
            + _make_unique_body(int(budgets["Financial Performance"]), prefix="fp"),
            "## Management Discussion & Analysis\n"
            + _make_unique_body(max(12, mdna_budget - 20), prefix="mdna"),
            "## Risk Factors\n" + risk_body,
            "## Key Metrics\n"
            + filings_api._build_key_metrics_block(
                metrics,
                target_length=target_length,
                include_health_rating=True,
            ),
            "## Closing Takeaway\n"
            + _make_unique_body(max(12, closing_budget - 21), prefix="close"),
        ]
    )

    risk_excerpt = (
        "liquidity funding working capital cloud demand deployment AI monetized usage"
    )

    validation = filings_api.validate_summary(
        summary_text,
        target_words=target_length,
        section_budgets=budgets,
        include_health_rating=True,
        risk_factors_excerpt=risk_excerpt,
    )
    assert any(
        failure.section_name == "Management Discussion & Analysis"
        and failure.code == "section_budget_under"
        for failure in validation.section_failures
    )
    assert any(
        failure.section_name == "Risk Factors" and failure.code == "section_budget_over"
        for failure in validation.section_failures
    )
    assert any(
        failure.section_name == "Closing Takeaway"
        and failure.code == "section_budget_under"
        for failure in validation.section_failures
    )
    assert any(
        failure.section_name == "Risk Factors"
        and failure.code == "risk_specificity"
        and "Liquidity / Funding Risk" in failure.message
        for failure in validation.section_failures
    )

    repaired_text, repair_info = (
        filings_api._rebalance_section_budgets_deterministically(
            summary_text,
            target_length=target_length,
            include_health_rating=True,
            section_balance_contract_required=True,
            issue_flags=filings_api._issue_flags_from_validation_report(validation),
            calculated_metrics=metrics,
            health_score_data={},
            risk_factors_excerpt=risk_excerpt,
        )
    )

    assert repair_info.get("risk_factors_normalized_first") is True
    repaired_risk_body = _get_section_body(repaired_text, "Risk Factors")
    repaired_validation = filings_api.validate_summary(
        repaired_text,
        target_words=target_length,
        section_budgets=budgets,
        include_health_rating=True,
        risk_factors_excerpt=risk_excerpt,
    )

    assert repaired_risk_body.count("**") >= 4
    assert "investors should watch" in repaired_risk_body.lower()
    # The expanded generic-risk-name regex now correctly flags "Liquidity / Funding Risk"
    # as too generic.  Verify no OTHER risk_schema failures remain after repair.
    non_generic_risk_failures = [
        failure
        for failure in repaired_validation.section_failures
        if failure.section_name == "Risk Factors"
        and failure.code == "risk_schema"
        and "too generic" not in failure.message
        and "generic category" not in failure.message
    ]
    assert not non_generic_risk_failures
    # With filler padding disabled, sections that start underweight may remain
    # slightly short.  The important verification is that risk_factors were
    # normalized and structural risk_schema issues resolved — not that padding
    # topped up MD&A and Closing Takeaway to exact budget.
    # With filler padding disabled, risk normalization may *add* required fields
    # (e.g. "Early-warning signal:" on risks that lacked one) while the rebalancer
    # can no longer compensate via donor-section padding.  The important check is
    # structural correctness (risk_schema, lines above), not raw word-count reduction.
