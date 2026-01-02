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

    # Body target = 534 words, weights = 10/15/20/20/15/10/10.
    # Rounding is deterministic based on remainder ordering.
    assert budgets["Financial Health Rating"] == 54
    assert budgets["Executive Summary"] == 80
    assert budgets["Financial Performance"] == 107
    assert budgets["Management Discussion & Analysis"] == 107
    assert budgets["Risk Factors"] == 80
    assert budgets["Key Metrics"] == 53
    assert budgets["Closing Takeaway"] == 53


def test_section_word_budgets_follow_fixed_distribution_large_target() -> None:
    budgets = filings_api._calculate_section_word_budgets(1000, include_health_rating=True)
    assert sum(budgets.values()) == 1000 - 16

    # Body target = 984 words.
    assert budgets["Financial Health Rating"] == 98
    assert budgets["Executive Summary"] == 148
    assert budgets["Financial Performance"] == 197
    assert budgets["Management Discussion & Analysis"] == 197
    assert budgets["Risk Factors"] == 148
    assert budgets["Key Metrics"] == 98
    assert budgets["Closing Takeaway"] == 98
