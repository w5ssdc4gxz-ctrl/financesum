import re

import pytest

from app.api import filings as filings_api


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


@pytest.mark.parametrize("target_length", [300, 550, 1000])
def test_enforce_section_budget_distribution_matches_budgets(target_length: int) -> None:
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )

    # Deliberately imbalanced input to ensure we exercise both trimming and padding.
    base = (
        "## Financial Health Rating\n"
        f"{_make_body(200, token='health')}\n\n"
        "## Executive Summary\n"
        f"{_make_body(200, token='exec')}\n\n"
        "## Financial Performance\n"
        f"{_make_body(20, token='perf')}\n\n"
        "## Management Discussion & Analysis\n"
        f"{_make_body(20, token='mdna')}\n\n"
        "## Risk Factors\n"
        f"{_make_body(20, token='risk')}\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n"
        "→ Operating Margin: 10%\n\n"
        "## Closing Takeaway\n"
        f"{_make_body(15, token='close')}"
    )

    enforced = filings_api._enforce_section_budget_distribution(
        base,
        target_length=target_length,
        include_health_rating=True,
        section_tolerance=10,
    )

    # Overall band (both whitespace split + MS-word style) must be inside ±10.
    assert target_length - 10 <= len(enforced.split()) <= target_length + 10
    assert (
        target_length - 10
        <= filings_api._count_words(enforced)
        <= target_length + 10
    )

    # Each section body must land within the fixed proportional budget band.
    for section, budget in budgets.items():
        body = _get_section_body(enforced, section)
        wc = filings_api._count_words(body)
        tol = filings_api._section_budget_tolerance_words(budget, max_tolerance=10)
        assert max(1, budget - tol) <= wc <= budget + tol


def test_enforce_section_budget_distribution_regression_realistic_memo() -> None:
    target_length = 650
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )

    # Realistic memo structure: long Financial Performance/MD&A, short Risk/Closing,
    # plus punctuation-heavy Key Metrics (pipes, list bullets) that can inflate
    # whitespace token counts.
    draft = """## Financial Health Rating
Uber Technologies Inc receives a Financial Health Rating of 68/100 - Watch because operating margin of 10.6% supports the earnings base, free cash flow of $2.25B funds reinvestment, and $29.92B liabilities against $6.38B cash frames the margin for error.

The score weights profitability and margin quality most heavily because it best captures durability in this setup. Operating margin of 10.6% and net margin of 15.4% describe the profitability profile.

## Executive Summary
My conviction on Uber Technologies Inc is currently Neutral with a Medium conviction. This company matters right now because it continues to consolidate its leadership in the global mobility and delivery sectors, demonstrating an ability to generate significant free cash flow.

## Financial Performance
Uber reported total revenue of $11.53 billion for Q1 2025, a substantial figure that underscores its market presence. Operating income stood at $1.23 billion, yielding an operating margin of 10.67%, which is a healthy improvement, suggesting better cost control or pricing power. The net income of $1.78 billion, translating to a net margin of 15.44%, notably exceeds the operating income due to a significant negative tax provision. This negative provision boosts reported earnings but warrants scrutiny for its recurring nature, as true earnings quality comes from operational performance. I focus on the bridge from operating profit to free cash flow, because that is where working-capital timing and capex show up in earnings quality. If profitability moved, I want to know whether mix and pricing drove it, or whether incentives and cost timing are masking pressure. When revenue, margins, and cash tell the same story, the signal is durable; when they diverge, the sustainability question gets louder. I focus on the bridge from operating profit to free cash flow, because that is where working-capital timing and capex show up in earnings quality. If profitability moved, I want to know whether mix and pricing drove it, or whether incentives and cost timing are masking pressure. When revenue, margins, and cash tell the same story, the signal is durable; when they diverge, the sustainability question gets louder.

## Management Discussion & Analysis
From my perspective, management appears to be prioritizing profitability and cash generation, aligning with the positive operating margin trajectory and strong free cash flow conversion observed this quarter. The relatively low capital expenditures of $74 million suggest that the core platform is not overly capital-intensive, allowing a substantial portion of operating cash flow to convert into free cash flow. This capital efficiency is something I always look for, as it directly impacts return on capital. Management's strategic focus likely includes disciplined geographic expansion, leveraging network effects in existing markets, and optimizing rider/driver matching algorithms to enhance efficiency and reduce costs. I prefer to underwrite the business off operating profitability and cash, because below-the-line items can be noisy quarter to quarter. For me, the thread to pull is durability: repeatable cash conversion is more informative than a single strong quarter. The risk-reward tends to shift when cash conversion and balance-sheet flexibility move in the same direction as margins.

## Risk Factors
**Regulatory Headwinds Risk**: Uber operates in a highly regulated environment, and any shift in the regulatory landscape could materially impact its business model and profitability.

## Key Metrics
→ Revenue: $11.53B | Operating Income: $1.23B | Net Income: $1.78B
→ Capital Expenditures: $74.00M | Total Assets: $52.82B

Health Score Drivers:
- Profitability: operating margin 10.6%, net margin 15.4%.
- Cash conversion: operating cash flow $2.32B, FCF $2.25B, FCF margin 19.5%.
- Balance sheet: cash + securities of $6.38B, liabilities of $29.92B, leverage 0.6x assets, interest coverage 11.7x.
- Liquidity: current ratio 1.0x.

## Closing Takeaway
Uber Technologies Inc is either good or cheap, but not clearly both."""

    enforced = filings_api._enforce_section_budget_distribution(
        draft,
        target_length=target_length,
        include_health_rating=True,
        section_tolerance=10,
    )

    assert target_length - 10 <= len(enforced.split()) <= target_length + 10
    assert (
        target_length - 10
        <= filings_api._count_words(enforced)
        <= target_length + 10
    )

    for section, budget in budgets.items():
        body = _get_section_body(enforced, section)
        wc = filings_api._count_words(body)
        tol = filings_api._section_budget_tolerance_words(budget, max_tolerance=10)
        assert max(1, budget - tol) <= wc <= budget + tol
