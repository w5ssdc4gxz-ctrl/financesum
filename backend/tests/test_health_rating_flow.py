from app.api import filings as filings_api


def test_ensure_health_rating_section_strips_inline_score_lines():
    summary_text = (
        "## Executive Summary\n"
        "A short executive summary.\n\n"
        "Financial Health Rating: 72/100 - Watch\n\n"
        "## Financial Performance\n"
        "A short financial performance section.\n"
    )

    health_score_data = {"overall_score": 72, "score_band": "Watch"}
    calculated_metrics = {
        "operating_margin": 12.3,
        "net_margin": 6.1,
        "revenue": 5_000_000_000,
        "operating_cash_flow": 1_100_000_000,
        "free_cash_flow": 600_000_000,
        "cash": 800_000_000,
        "total_liabilities": 3_000_000_000,
        "current_assets": 1_500_000_000,
        "current_liabilities": 1_000_000_000,
    }
    health_config = {
        "framework": "value_investor_default",
        "primary_factor_weighting": "profitability_margins",
    }

    result = filings_api._ensure_health_rating_section(
        summary_text,
        health_score_data,
        calculated_metrics,
        "TestCo",
        health_rating_config=health_config,
        target_length=650,
    )

    assert result.startswith("## Financial Health Rating")
    assert "Financial Health Rating:" not in result
    assert "At 72/100 - Watch, TestCo" in result
    assert "operating margin" in result.lower()
    assert "This health snapshot sets the balance-sheet backdrop" in result
    assert "## Executive Summary" in result


def test_ensure_health_rating_section_rebuilds_and_removes_drivers_block():
    summary_text = (
        "## Financial Health Rating\n"
        "TestCo receives a Financial Health Rating of 72/100 - Watch.\n\n"
        "Health Score Drivers:\n"
        "- Profitability: operating margin 12.3%, net margin 6.1%.\n"
        "- Cash conversion: operating cash flow $1.10B, FCF $0.60B.\n\n"
        "## Executive Summary\n"
        "A short executive summary.\n"
    )

    health_score_data = {"overall_score": 72, "score_band": "Watch"}
    calculated_metrics = {
        "operating_margin": 12.3,
        "net_margin": 6.1,
        "revenue": 5_000_000_000,
        "operating_cash_flow": 1_100_000_000,
        "free_cash_flow": 600_000_000,
        "cash": 800_000_000,
        "total_liabilities": 3_000_000_000,
        "current_assets": 1_500_000_000,
        "current_liabilities": 1_000_000_000,
    }

    result = filings_api._ensure_health_rating_section(
        summary_text,
        health_score_data,
        calculated_metrics,
        "TestCo",
        target_length=650,
    )

    assert "Health Score Drivers" not in result
    assert result.count("/100") == 1
    assert "operating margin" in result.lower()
    assert "## Executive Summary" in result


def test_ensure_health_rating_section_rebuilds_when_under_budget_for_long_target():
    summary_text = (
        "## Financial Health Rating\n"
        "TestCo receives a Financial Health Rating of 72/100 - Watch.\n\n"
        "Operating margin remains positive and liquidity is adequate. Cash flow also helps support the score.\n\n"
        "## Executive Summary\n"
        "A short executive summary.\n"
    )

    health_score_data = {"overall_score": 72, "score_band": "Watch"}
    calculated_metrics = {
        "operating_margin": 12.3,
        "net_margin": 6.1,
        "revenue": 5_000_000_000,
        "operating_cash_flow": 1_100_000_000,
        "free_cash_flow": 600_000_000,
        "cash": 800_000_000,
        "marketable_securities": 150_000_000,
        "total_liabilities": 3_000_000_000,
        "current_assets": 1_500_000_000,
        "current_liabilities": 1_000_000_000,
        "current_ratio": 1.5,
        "capital_expenditures": 500_000_000,
    }

    result = filings_api._ensure_health_rating_section(
        summary_text,
        health_score_data,
        calculated_metrics,
        "TestCo",
        target_length=1225,
    )

    health_body = (
        filings_api._extract_markdown_section_body(result, "Financial Health Rating")
        or ""
    )
    budget = filings_api._calculate_section_word_budgets(
        1225, include_health_rating=True
    )["Financial Health Rating"]
    tolerance = filings_api.canonical_section_budget_tolerance_words(
        "Financial Health Rating",
        budget,
    )
    lower_bound = max(1, int(budget) - int(tolerance))

    assert filings_api._count_words(health_body) >= lower_bound
    assert "TestCo" in result
    assert "This health snapshot sets the balance-sheet backdrop" in result
