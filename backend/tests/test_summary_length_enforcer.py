from app.api import filings as filings_api
from app.services.summary_budget_controller import (
    compute_depth_plan,
    compute_scale_factor,
    get_depth_profile,
    section_budget_tolerance_words,
)
from app.services.summary_length import (
    TARGET_LENGTH_MAX_WORDS,
    enforce_summary_target_length,
)


def _make_words(n: int, token: str) -> str:
    if n <= 0:
        return ""
    return " ".join([token] * n)


def _build_sectioned_summary(word_count: int) -> str:
    sections = [
        (
            "## Executive Summary",
            "Durable cash generation matters only if the current investment cycle still earns a real return.",
        ),
        (
            "## Financial Performance",
            "Revenue improved, but margin durability now depends on whether higher spending translates into stronger conversion.",
        ),
        (
            "## Management Discussion & Analysis",
            "Management says reinvestment pacing is disciplined and that pricing will improve as newer products scale.",
        ),
        (
            "## Risk Factors",
            "**Execution Risk**: If deployments slip, spending could rise faster than realized demand and pressure cash flow.",
        ),
        (
            "## Key Metrics",
            "→ Revenue: $1.0B\n→ Free Cash Flow: $0.2B\n→ Operating Margin: 10%\n→ Capex: $0.3B\n→ Current Ratio: 2.0x",
        ),
        (
            "## Closing Takeaway",
            "The verdict depends on whether the business can convert this spending cycle into durable cash returns.",
        ),
    ]
    text = "\n\n".join(f"{header}\n{body}" for header, body in sections)
    current_words = filings_api._count_words(text)
    if current_words >= word_count:
        return filings_api._truncate_text_to_word_limit(text, word_count)

    filler_targets = [
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
    ]
    filler_needed = word_count - current_words
    per_section = filler_needed // len(filler_targets)
    leftover = filler_needed % len(filler_targets)
    word_idx = 0
    for idx, section_name in enumerate(filler_targets):
        add_words = per_section + (1 if idx < leftover else 0)
        if add_words <= 0:
            continue
        filler_words = [f"detail{word_idx + offset}" for offset in range(add_words)]
        word_idx += add_words
        marker = f"## {section_name}\n"
        start = text.find(marker)
        if start < 0:
            continue
        body_start = start + len(marker)
        next_section = text.find("\n\n## ", body_start)
        if next_section == -1:
            next_section = len(text)
        text = (
            text[:next_section].rstrip()
            + " "
            + " ".join(filler_words)
            + text[next_section:]
        )
    return text


def test_enforce_summary_target_length_caps_markdown_to_target() -> None:
    # Deliberately over-long markdown with headings so trimming must preserve structure.
    base = (
        "# Investment Analysis: ExampleCo\n\n"
        "## TL;DR\n"
        f"{_make_words(120, 'tldr')}.\n\n"
        "## Investment Thesis\n"
        f"{_make_words(220, 'thesis')}.\n\n"
        "## Top 5 Risks\n"
        f"{_make_words(220, 'risk')}.\n\n"
        "## Catalysts\n"
        f"{_make_words(120, 'cat')}.\n\n"
        "## Key KPIs\n"
        f"{_make_words(120, 'kpi')}.\n"
    )

    target = 200
    enforced = enforce_summary_target_length(base, target, tolerance=0)

    assert len(enforced.split()) <= target
    assert filings_api._count_words(enforced) <= target


def test_cleanup_sentence_artifacts_removes_stray_quotes_and_fragments() -> None:
    raw = (
        "## Executive Summary\n"
        "Strong liquidity supports near-term flexibility, but\"\n"
        "\n"
        "## Financial Performance\n"
        "Margins improved and cash flow held, but\n"
        "\"\n"
        "\n"
        "## Key KPIs to Monitor\n"
        "- Monitor leverage and liquidity\n"
    )
    cleaned = filings_api._cleanup_sentence_artifacts(raw)
    assert "\"\n" not in cleaned
    assert "## Key KPIs to Monitor" not in cleaned
    assert "## Key KPIs" in cleaned
    assert "- Monitor leverage and liquidity" not in cleaned
    assert "- leverage and liquidity" in cleaned
    assert "flexibility." in cleaned
    assert "held." in cleaned


def test_enforce_summary_target_length_caps_to_global_max_when_no_target() -> None:
    base = _make_words(TARGET_LENGTH_MAX_WORDS + 25, "word")
    enforced = enforce_summary_target_length(base, None, tolerance=0)

    assert filings_api._count_words(enforced) <= TARGET_LENGTH_MAX_WORDS


def test_effective_word_band_tolerance_uses_forty_for_short_sectioned_targets() -> None:
    for target in (500, 600, 1000):
        assert filings_api._effective_word_band_tolerance(target) == 40


def test_target_word_band_bounds_uses_short_forty_word_acceptance_contract() -> None:
    assert filings_api._target_word_band_bounds(500) == (460, 540, 40)
    assert filings_api._target_word_band_bounds(600) == (560, 640, 40)
    assert filings_api._target_word_band_bounds(1000) == (960, 1040, 40)


def test_rewrite_target_word_band_tolerance_keeps_tighter_short_targeting() -> None:
    for target in (500, 600, 1000):
        assert filings_api._rewrite_target_word_band_tolerance(target) == 20


def test_tighten_short_mid_precision_target_resteers_near_miss_contract_safe_outputs() -> (
    None
):
    target = 1000
    for current_words in (971, 1029):
        text = _build_sectioned_summary(current_words)
        tightened = filings_api._tighten_short_mid_precision_target(
            text,
            target_length=target,
            include_health_rating=False,
            generation_stats={},
        )
        split_wc = len(tightened.split())
        stripped_wc = filings_api._count_words(tightened)
        assert 980 <= split_wc <= 1020
        assert 980 <= stripped_wc <= 1020
        assert "## Key Metrics" in tightened
        assert "## Closing Takeaway" in tightened


def test_explicit_short_mid_precision_target_detection() -> None:
    assert (
        filings_api._is_explicit_short_mid_precision_target(
            target_length=600,
            explicit_target_requested=True,
        )
        is True
    )
    assert (
        filings_api._is_explicit_short_mid_precision_target(
            target_length=600,
            explicit_target_requested=False,
        )
        is False
    )
    assert (
        filings_api._is_explicit_short_mid_precision_target(
            target_length=3000,
            explicit_target_requested=True,
        )
        is False
    )


def test_requires_section_balance_contract_for_explicit_short_mid_targets() -> None:
    assert (
        filings_api._requires_section_balance_contract(
            target_length=600,
            explicit_target_requested=True,
            v2_enabled=False,
        )
        is True
    )
    assert (
        filings_api._requires_section_balance_contract(
            target_length=600,
            explicit_target_requested=False,
            v2_enabled=False,
        )
        is False
    )
    assert (
        filings_api._requires_section_balance_contract(
            target_length=3000,
            explicit_target_requested=True,
            v2_enabled=False,
        )
        is False
    )
    assert (
        filings_api._requires_section_balance_contract(
            target_length=3000,
            explicit_target_requested=False,
            v2_enabled=True,
        )
        is True
    )


# ---------------------------------------------------------------------------
# compute_scale_factor — continuous [300, 3000] → [0.0, 1.0] mapping
# ---------------------------------------------------------------------------

def test_compute_scale_factor_at_minimum() -> None:
    assert compute_scale_factor(300) == 0.0


def test_compute_scale_factor_at_maximum() -> None:
    assert compute_scale_factor(3000) == 1.0


def test_compute_scale_factor_midpoint() -> None:
    sf = compute_scale_factor(1650)
    assert abs(sf - 0.5) < 0.01


def test_compute_scale_factor_clamps_below_min() -> None:
    assert compute_scale_factor(0) == 0.0
    assert compute_scale_factor(100) == 0.0


def test_compute_scale_factor_clamps_above_max() -> None:
    assert compute_scale_factor(5000) == 1.0


def test_compute_scale_factor_monotone() -> None:
    targets = [300, 500, 750, 1000, 1500, 2000, 2500, 3000]
    factors = [compute_scale_factor(t) for t in targets]
    for i in range(len(factors) - 1):
        assert factors[i] <= factors[i + 1], "scale_factor must be monotonically non-decreasing"


# ---------------------------------------------------------------------------
# get_depth_profile — booleans keyed by depth feature
# ---------------------------------------------------------------------------

def test_get_depth_profile_at_zero() -> None:
    profile = get_depth_profile(0.0)
    assert profile["expand_yoy"] is False
    assert profile["expand_leverage"] is False
    assert profile["expand_cash_conversion"] is False
    assert profile["expand_balance_sheet"] is False
    assert profile["expand_scenarios"] is False


def test_get_depth_profile_at_one() -> None:
    profile = get_depth_profile(1.0)
    assert profile["expand_yoy"] is True
    assert profile["expand_leverage"] is True
    assert profile["expand_cash_conversion"] is True
    assert profile["expand_balance_sheet"] is True
    assert profile["expand_scenarios"] is True


def test_get_depth_profile_thresholds() -> None:
    assert get_depth_profile(0.3)["expand_yoy"] is True
    assert get_depth_profile(0.3)["expand_leverage"] is False
    assert get_depth_profile(0.5)["expand_leverage"] is True
    assert get_depth_profile(0.5)["expand_cash_conversion"] is False
    assert get_depth_profile(0.6)["expand_cash_conversion"] is True
    assert get_depth_profile(0.6)["expand_balance_sheet"] is False
    assert get_depth_profile(0.7)["expand_balance_sheet"] is True
    assert get_depth_profile(0.7)["expand_scenarios"] is False
    assert get_depth_profile(0.9)["expand_scenarios"] is True


def test_compute_depth_plan_clamps_out_of_range_scale_factors() -> None:
    low = compute_depth_plan(-1.0)
    high = compute_depth_plan(2.0)

    assert all(value == 0.0 for value in low.__dict__.values())
    assert all(value == 1.0 for value in high.__dict__.values())


def test_compute_depth_plan_scores_are_monotone() -> None:
    scale_factors = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    plans = [compute_depth_plan(value) for value in scale_factors]

    for field_name in plans[0].__dict__.keys():
        values = [getattr(plan, field_name) for plan in plans]
        assert values == sorted(values), f"{field_name} must increase monotonically"


def test_compute_depth_plan_progressively_adds_later_features() -> None:
    early = compute_depth_plan(0.2)
    mid = compute_depth_plan(0.55)
    late = compute_depth_plan(0.9)

    assert early.yoy_score > 0.0
    assert early.scenario_score == 0.0
    assert mid.leverage_score > 0.0
    assert mid.balance_sheet_score > 0.0
    assert late.scenario_score > 0.0
    assert late.capital_allocation_score > mid.capital_allocation_score


# ---------------------------------------------------------------------------
# section_budget_tolerance_words — now 3% of budget
# ---------------------------------------------------------------------------

def test_section_budget_tolerance_is_five_percent_under_250() -> None:
    # 200 words * 5% = 10
    tol = section_budget_tolerance_words("Executive Summary", 200)
    assert tol == max(8, round(200 * 0.05))


def test_section_budget_tolerance_is_three_percent_over_250() -> None:
    # 300 words * 3% = 9, floor 10
    tol = section_budget_tolerance_words("Executive Summary", 300)
    assert tol == max(10, round(300 * 0.03))


def test_section_budget_tolerance_floor_at_ten() -> None:
    # Very small budget — floor kicks in
    tol = section_budget_tolerance_words("Closing Takeaway", 50)
    assert tol == 10  # 50 * 0.05 = 2.5 < 10 → floor


def test_key_metrics_tolerance_is_small() -> None:
    # Key Metrics has a small fixed tolerance (±3) to avoid 422s from
    # minor row-length variance while staying tighter than narrative sections.
    assert section_budget_tolerance_words("Key Metrics", 100) == 3


def test_tolerance_scales_with_budget() -> None:
    small = section_budget_tolerance_words("Financial Performance", 200)
    large = section_budget_tolerance_words("Financial Performance", 800)
    assert large > small


def test_risk_factors_tolerance_wider_at_mid_range_budgets() -> None:
    """Risk Factors at 110-250 word budgets uses 8% band (not 5%)."""
    tol = section_budget_tolerance_words("Risk Factors", 172)
    assert tol == max(10, round(172 * 0.08))  # 14
    # Non-Risk section at same budget unchanged
    tol_exec = section_budget_tolerance_words("Executive Summary", 172)
    assert tol_exec == max(10, round(172 * 0.05))  # 10 (floor)
    assert tol > tol_exec


def test_risk_factors_tolerance_normal_outside_mid_range() -> None:
    """Risk Factors below 110 or above 250 uses the standard rate."""
    # Below the 3-risk zone
    tol_low = section_budget_tolerance_words("Risk Factors", 90)
    tol_low_other = section_budget_tolerance_words("Executive Summary", 90)
    assert tol_low == tol_low_other  # both use 5% → floor 10
    # Above the squeeze zone
    tol_high = section_budget_tolerance_words("Risk Factors", 300)
    tol_high_other = section_budget_tolerance_words("Executive Summary", 300)
    assert tol_high == tol_high_other  # both use 3%
