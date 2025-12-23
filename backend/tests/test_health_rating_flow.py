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
    assert "TestCo receives a Financial Health Rating of 72/100 - Watch" in result
    assert "Under a value-investor lens" in result
    assert "profitability and margin quality" in result
    assert "This health snapshot provides the balance-sheet backdrop" in result
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
    assert "Operating cash flow of $1.10B" in result
    assert "free cash flow of $600.00M" in result
    assert "## Executive Summary" in result
