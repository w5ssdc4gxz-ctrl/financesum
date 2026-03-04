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


@pytest.mark.parametrize("target_length", [300, 550, 1000])
def test_enforce_section_budget_distribution_matches_budgets(
    target_length: int,
) -> None:
    """With padding templates disabled, _enforce_section_budget_distribution can
    only trim overweight sections.  Underweight sections stay underweight, so the
    overall word count may be well below target_length.  This test verifies that:
      1. Overweight sections are trimmed to at most budget + tolerance.
      2. The overall word count does not exceed target + tolerance.
      3. Key Metrics stays under the hard cap for long-form memos.
    """
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )

    # Deliberately imbalanced input to ensure we exercise trimming.
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

    section_tolerance = (
        40
        if target_length >= filings_api.KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS
        else 15
    )
    enforced = filings_api._enforce_section_budget_distribution(
        base,
        target_length=target_length,
        include_health_rating=True,
        section_tolerance=section_tolerance,
    )

    # Overall: output must not exceed target + tolerance (trimming works).
    # It may be well below target since padding is disabled and underweight
    # sections cannot be expanded.
    overall_tol = max(15, target_length // 8)
    assert len(enforced.split()) <= target_length + overall_tol
    assert filings_api._count_words(enforced) <= target_length + overall_tol

    # Per-section: overweight sections must be trimmed. Underweight sections
    # are left as-is (no padding), so we only check the upper bound.
    for section, budget in budgets.items():
        body = _get_section_body(enforced, section)
        wc = filings_api._count_words(body)
        tol = filings_api._section_budget_tolerance_words(
            budget, max_tolerance=section_tolerance
        )
        section_tol = max(tol, overall_tol)
        if (
            target_length >= filings_api.KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS
            and section == "Key Metrics"
        ):
            assert wc <= filings_api.KEY_METRICS_MAX_WORDS
        else:
            # Upper bound only — underweight sections can't be padded.
            assert wc <= budget + section_tol


def test_enforce_section_budget_distribution_regression_realistic_memo() -> None:
    """With padding templates disabled, the budget enforcer can only trim
    overweight sections.  Verify trimming works and overall count doesn't
    exceed the target, but accept that the total may be below target since
    underweight sections cannot be expanded.
    """
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
Uber reported total revenue of $11.53 billion for Q1 2025, a substantial figure that underscores its market presence. Operating income stood at $1.23 billion, yielding an operating margin of 10.67%, which is a healthy improvement, suggesting better cost control or pricing power. The net income of $1.78 billion, translating to a net margin of 15.44%, notably exceeds the operating income due to a significant negative tax provision. This negative provision boosts reported earnings but warrants scrutiny for its recurring nature, as true earnings quality comes from operational performance.

## Management Discussion & Analysis
From my perspective, management appears to be prioritizing profitability and cash generation, aligning with the positive operating margin trajectory and strong free cash flow conversion observed this quarter. The relatively low capital expenditures of $74 million suggest that the core platform is not overly capital-intensive, allowing a substantial portion of operating cash flow to convert into free cash flow. This capital efficiency is something I always look for, as it directly impacts return on capital. Management's strategic focus likely includes disciplined geographic expansion, leveraging network effects in existing markets, and optimizing rider/driver matching algorithms to enhance efficiency and reduce costs.

## Risk Factors
**Regulatory Headwinds Risk**: Uber operates in a highly regulated environment, and any shift in the regulatory landscape could materially impact its business model and profitability.

## Key Metrics
→ Revenue: $11.53B | Operating Income: $1.23B | Net Income: $1.78B
→ Capital Expenditures: $74.00M | Total Assets: $52.82B

Health Score Drivers:
Profitability: operating margin 10.6%, net margin 15.4%.
Cash conversion: operating cash flow $2.32B, FCF $2.25B, FCF margin 19.5%.
Balance sheet: cash and securities of $6.38B, liabilities of $29.92B, leverage 0.6x assets, interest coverage 11.7x.
Liquidity: current ratio 1.0x.

## Closing Takeaway
Uber Technologies Inc is either good or cheap, but not clearly both."""

    enforced = filings_api._enforce_section_budget_distribution(
        draft,
        target_length=target_length,
        include_health_rating=True,
        section_tolerance=10,
    )

    # Overall: must not exceed target + tolerance.  May be below target
    # since padding is disabled and underweight sections stay underweight.
    overall_tol = max(10, target_length // 10)
    assert filings_api._count_words(enforced) <= target_length + overall_tol

    # Per-section: overweight sections trimmed; underweight sections left as-is.
    for section, budget in budgets.items():
        body = _get_section_body(enforced, section)
        wc = filings_api._count_words(body)
        tol = filings_api._section_budget_tolerance_words(budget, max_tolerance=10)
        section_tol = max(tol, overall_tol)
        # Upper bound only — padding is disabled so underweight sections can't grow.
        assert wc <= budget + section_tol


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
    assert 8 <= len(lines) <= 12

    labels = [line.split("|")[0].strip() for line in lines]
    assert labels[:4] == [
        "Revenue",
        "Operating Income",
        "Operating Margin",
        "Net Margin",
    ]


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


def test_enforce_section_budget_distribution_does_not_pad_key_metrics_with_watch_filler() -> None:
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
    assert titles[:3] == [
        "Management Discussion & Analysis",
        "Executive Summary",
        "Closing Takeaway",
    ]
    assert "Management Discussion & Analysis" in guidance
    assert "Executive Summary" in guidance
