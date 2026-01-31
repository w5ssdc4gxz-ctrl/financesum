from app.api import filings as filings_api


def test_section_word_budgets_follow_fixed_distribution() -> None:
    """Budgets must follow the fixed section distribution regardless of target length.

    NOTE: Budgets are for section BODY words (headings excluded).
    """

    budgets = filings_api._calculate_section_word_budgets(550, include_health_rating=True)

    # Heading-title words for all 7 sections:
    # Financial Health Rating (3) + Executive Summary (2) + Financial Performance (2)
    # + Management Discussion & Analysis (3) + Risk Factors (2) + Key Metrics (2)
    # + Closing Takeaway (2) = 16
    assert sum(budgets.values()) == 550 - 16

    # Body target = 534 words. Distribution is fixed and deterministic.
    assert budgets["Financial Health Rating"] == 75
    assert budgets["Executive Summary"] == 75
    assert budgets["Financial Performance"] == 80
    assert budgets["Management Discussion & Analysis"] == 80
    assert budgets["Risk Factors"] == 75
    assert budgets["Key Metrics"] == 75
    assert budgets["Closing Takeaway"] == 74


def test_section_word_budgets_follow_fixed_distribution_large_target() -> None:
    budgets = filings_api._calculate_section_word_budgets(1000, include_health_rating=True)
    assert sum(budgets.values()) == 1000 - 16

    # For long targets, Key Metrics is capped and the remaining budget is redistributed.
    assert budgets["Key Metrics"] == filings_api.KEY_METRICS_FIXED_BUDGET_WORDS
    assert budgets["Financial Health Rating"] == 103
    assert budgets["Executive Summary"] == 103
    assert budgets["Financial Performance"] == 111
    assert budgets["Management Discussion & Analysis"] == 111
    assert budgets["Risk Factors"] == 103
    assert budgets["Closing Takeaway"] == 103
