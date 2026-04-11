"""Canonical section-budget logic for filing summaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional


CANONICAL_SECTION_ORDER = [
    "Financial Health Rating",
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Key Metrics",
    "Closing Takeaway",
]

DEFAULT_SECTION_WEIGHTS_WITH_HEALTH: Dict[str, int] = {
    "Financial Health Rating": 20,
    "Executive Summary": 15,
    "Financial Performance": 20,
    "Management Discussion & Analysis": 20,
    "Risk Factors": 15,
    "Key Metrics": 8,
    "Closing Takeaway": 13,
}

DEFAULT_SECTION_WEIGHTS_WITHOUT_HEALTH: Dict[str, int] = {
    "Executive Summary": 15,
    "Financial Performance": 20,
    "Management Discussion & Analysis": 20,
    "Risk Factors": 15,
    "Key Metrics": 8,
    "Closing Takeaway": 13,
}

NARRATIVE_SECTION_TOLERANCE_CAP = 12  # advisory; not enforced in tolerance calc
NARRATIVE_SECTION_TOLERANCE_FLOOR = 10

# Continuous scale: [300, 3000] → [0.0, 1.0]
_SCALE_MIN_WORDS = 300
_SCALE_MAX_WORDS = 3000
SHORT_FORM_SECTIONED_TARGET_MIN_WORDS = _SCALE_MIN_WORDS
SHORT_FORM_SECTIONED_TARGET_MAX_WORDS = 1500
KEY_METRICS_MIN_WORDS = 20
KEY_METRICS_MAX_WORDS = 90
KEY_METRICS_WEIGHT = 0.08
KEY_METRICS_FIXED_BUDGET_THRESHOLD_WORDS = 1000
KEY_METRICS_FIXED_BUDGET_WORDS = KEY_METRICS_MAX_WORDS
NARRATIVE_FLOOR_MIN_WORDS = 24
RISK_FLOOR_MIN_WORDS = 36
NARRATIVE_FLOOR_PCT = 0.08
RISK_FLOOR_PCT = 0.12
RISK_FACTORS_TWO_RISK_MAX_BUDGET = 109

_KEY_METRICS_SECTION = "Key Metrics"
_RISK_FACTORS_SECTION = "Risk Factors"
_NARRATIVE_SECTION_NAMES_WITH_HEALTH = [
    "Financial Health Rating",
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Closing Takeaway",
]
_NARRATIVE_SECTION_NAMES_WITHOUT_HEALTH = [
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Closing Takeaway",
]


@dataclass(frozen=True)
class DepthPlan:
    """Continuous analytical-depth scores derived from the total word target."""

    yoy_score: float
    sequential_score: float
    leverage_score: float
    cash_conversion_score: float
    balance_sheet_score: float
    capital_allocation_score: float
    scenario_score: float
    example_score: float


@dataclass(frozen=True)
class SectionShape:
    """Canonical structural expectations for a narrative section."""

    section_name: str
    min_sentences: int
    max_sentences: int
    min_paragraphs: int
    max_paragraphs: int
    preferred_paragraphs: Optional[int] = None
    risk_count: Optional[int] = None
    per_risk_min_sentences: Optional[int] = None
    per_risk_max_sentences: Optional[int] = None
    requires_company_specific_mechanism: bool = False
    requires_financial_transmission_path: bool = False
    requires_early_warning_signal: bool = False
    requires_exactly_one_stance: bool = False
    requires_must_stay_true_trigger: bool = False
    requires_breaks_thesis_trigger: bool = False
    requires_capital_allocation_implication: bool = False


def get_financial_health_shape(budget_words: int) -> SectionShape:
    """Return the canonical Financial Health Rating shape for the budget."""
    budget = int(budget_words or 0)
    if budget < 120:
        return SectionShape(
            section_name="Financial Health Rating",
            min_sentences=2,
            max_sentences=3,
            min_paragraphs=1,
            max_paragraphs=1,
            preferred_paragraphs=1,
        )
    if budget <= 260:
        return SectionShape(
            section_name="Financial Health Rating",
            min_sentences=4,
            max_sentences=5,
            min_paragraphs=1,
            max_paragraphs=1,
            preferred_paragraphs=1,
        )
    if budget <= 420:
        return SectionShape(
            section_name="Financial Health Rating",
            min_sentences=6,
            max_sentences=8,
            min_paragraphs=1,
            max_paragraphs=2,
            preferred_paragraphs=2,
        )
    return SectionShape(
        section_name="Financial Health Rating",
        min_sentences=8,
        max_sentences=10,
        min_paragraphs=2,
        max_paragraphs=2,
        preferred_paragraphs=2,
    )


def get_risk_factors_shape(budget_words: int) -> SectionShape:
    """Return the canonical Risk Factors shape for the budget."""
    budget = int(budget_words or 0)
    risk_count = risk_budget_target_count(budget)
    per_risk_min_sentences = 2
    per_risk_max_sentences = 3
    return SectionShape(
        section_name="Risk Factors",
        min_sentences=per_risk_min_sentences * risk_count,
        max_sentences=per_risk_max_sentences * risk_count,
        min_paragraphs=risk_count,
        max_paragraphs=risk_count,
        preferred_paragraphs=risk_count,
        risk_count=risk_count,
        per_risk_min_sentences=per_risk_min_sentences,
        per_risk_max_sentences=per_risk_max_sentences,
        requires_company_specific_mechanism=True,
        requires_financial_transmission_path=True,
        requires_early_warning_signal=False,
    )


def get_closing_takeaway_shape(budget_words: int) -> SectionShape:
    """Return the canonical Closing Takeaway shape for the budget."""
    budget = int(budget_words or 0)
    if budget < 120:
        return SectionShape(
            section_name="Closing Takeaway",
            min_sentences=2,
            max_sentences=3,
            min_paragraphs=1,
            max_paragraphs=1,
            preferred_paragraphs=1,
            requires_exactly_one_stance=True,
        )
    if budget <= 220:
        return SectionShape(
            section_name="Closing Takeaway",
            min_sentences=4,
            max_sentences=5,
            min_paragraphs=1,
            max_paragraphs=2,
            preferred_paragraphs=2,
            requires_exactly_one_stance=True,
            requires_must_stay_true_trigger=True,
            requires_breaks_thesis_trigger=True,
            requires_capital_allocation_implication=True,
        )
    if budget <= 320:
        return SectionShape(
            section_name="Closing Takeaway",
            min_sentences=5,
            max_sentences=7,
            min_paragraphs=2,
            max_paragraphs=2,
            preferred_paragraphs=2,
            requires_exactly_one_stance=True,
            requires_must_stay_true_trigger=True,
            requires_breaks_thesis_trigger=True,
            requires_capital_allocation_implication=True,
        )
    return SectionShape(
        section_name="Closing Takeaway",
        min_sentences=7,
        max_sentences=9,
        min_paragraphs=2,
        max_paragraphs=3,
        preferred_paragraphs=2,
        requires_exactly_one_stance=True,
        requires_must_stay_true_trigger=True,
        requires_breaks_thesis_trigger=True,
        requires_capital_allocation_implication=True,
    )


def get_section_shape(section_name: str, budget_words: int) -> Optional[SectionShape]:
    """Return the canonical structural contract for the given section."""
    canonical = str(section_name or "").strip()
    if canonical == "Financial Health Rating":
        return get_financial_health_shape(budget_words)
    if canonical == "Risk Factors":
        return get_risk_factors_shape(budget_words)
    if canonical == "Closing Takeaway":
        return get_closing_takeaway_shape(budget_words)
    return None


def describe_sentence_range(min_sentences: int, max_sentences: int) -> str:
    if int(min_sentences) == int(max_sentences):
        return f"{int(min_sentences)} sentence{'s' if int(min_sentences) != 1 else ''}"
    return f"{int(min_sentences)}-{int(max_sentences)} sentences"


def describe_paragraph_range(
    min_paragraphs: int,
    max_paragraphs: int,
    *,
    short: bool = False,
) -> str:
    if int(min_paragraphs) == int(max_paragraphs):
        count = int(min_paragraphs)
        if count == 1:
            return "1 paragraph"
        prefix = "short " if short else ""
        return f"{count} {prefix}paragraphs"
    prefix = "short " if short else ""
    return f"{int(min_paragraphs)}-{int(max_paragraphs)} {prefix}paragraphs"


def clamp_scale_factor(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _smoothstep(value: float, edge0: float, edge1: float) -> float:
    if edge1 <= edge0:
        return 1.0 if value >= edge1 else 0.0
    x = clamp_scale_factor((float(value) - float(edge0)) / (float(edge1) - float(edge0)))
    return x * x * (3.0 - 2.0 * x)


def compute_scale_factor(total_words: int) -> float:
    """Map total word target [300, 3000] linearly to [0.0, 1.0], clamped."""
    try:
        w = int(total_words or 0)
    except (TypeError, ValueError):
        return 0.5
    if w <= _SCALE_MIN_WORDS:
        return 0.0
    if w >= _SCALE_MAX_WORDS:
        return 1.0
    return (w - _SCALE_MIN_WORDS) / (_SCALE_MAX_WORDS - _SCALE_MIN_WORDS)


def total_word_tolerance_words(target_words: int) -> int:
    """Return the global ±tolerance for a memo target."""
    target = max(0, int(target_words or 0))
    if target <= 0:
        return NARRATIVE_SECTION_TOLERANCE_FLOOR
    return max(NARRATIVE_SECTION_TOLERANCE_FLOOR, int(round(target * 0.03)))


def compute_depth_plan(scale_factor: float) -> DepthPlan:
    """Convert a scale factor into continuous analytical-depth scores."""
    sf = clamp_scale_factor(scale_factor)
    return DepthPlan(
        yoy_score=_smoothstep(sf, 0.10, 0.35),
        sequential_score=_smoothstep(sf, 0.20, 0.45),
        leverage_score=_smoothstep(sf, 0.30, 0.55),
        cash_conversion_score=_smoothstep(sf, 0.40, 0.65),
        balance_sheet_score=_smoothstep(sf, 0.50, 0.75),
        capital_allocation_score=_smoothstep(sf, 0.55, 0.80),
        scenario_score=_smoothstep(sf, 0.70, 0.95),
        example_score=_smoothstep(sf, 0.25, 0.60),
    )


def get_depth_profile(scale_factor: float) -> dict:
    """Return depth-feature booleans for the given scale factor.

    scale_factor is expected in [0.0, 1.0].
    """
    plan = compute_depth_plan(scale_factor)
    return {
        "expand_yoy": plan.yoy_score >= 0.5,
        "expand_leverage": plan.leverage_score >= 0.5,
        "expand_cash_conversion": plan.cash_conversion_score >= 0.5,
        "expand_balance_sheet": plan.balance_sheet_score >= 0.5,
        "expand_scenarios": plan.scenario_score >= 0.5,
    }


def risk_budget_target_count(risk_budget_words: int) -> int:
    """Return the required number of structured risks for the given budget."""
    return 2


def _apply_integer_drift(
    budgets: Dict[str, int],
    exacts: Dict[str, float],
    sections_to_use: list[str],
    *,
    target_total: int,
) -> Dict[str, int]:
    drift = int(target_total) - sum(int(budgets.get(section_name, 0) or 0) for section_name in sections_to_use)
    if drift == 0:
        return budgets

    remainders = {
        section_name: float(exacts.get(section_name, 0.0))
        - int(budgets.get(section_name, 0) or 0)
        for section_name in sections_to_use
    }
    ranked = sorted(
        sections_to_use,
        key=lambda section_name: remainders.get(section_name, 0.0),
        reverse=True,
    )
    step = 1 if drift > 0 else -1
    remaining = abs(int(drift))
    idx = 0
    while ranked and remaining > 0 and idx < 10_000:
        section_name = ranked[idx % len(ranked)]
        next_value = int(budgets.get(section_name, 0) or 0) + step
        if next_value >= 0:
            budgets[section_name] = next_value
            remaining -= 1
        idx += 1
    return budgets


def _narrative_sections(include_health_rating: bool) -> list[str]:
    return (
        list(_NARRATIVE_SECTION_NAMES_WITH_HEALTH)
        if include_health_rating
        else list(_NARRATIVE_SECTION_NAMES_WITHOUT_HEALTH)
    )


def compute_proportional_floors(
    narrative_target_words: int,
    sections_to_use: list[str],
) -> Dict[str, int]:
    """Compute per-section floors proportional to total target length.

    Narrative sections: max(24, narrative_target * 8%).
    Risk Factors: max(36, narrative_target * 12%).
    """
    floors: Dict[str, int] = {}
    for section_name in sections_to_use:
        if section_name == _RISK_FACTORS_SECTION:
            floors[section_name] = max(
                int(RISK_FLOOR_MIN_WORDS),
                int(round(int(narrative_target_words) * float(RISK_FLOOR_PCT))),
            )
        else:
            floors[section_name] = max(
                int(NARRATIVE_FLOOR_MIN_WORDS),
                int(round(int(narrative_target_words) * float(NARRATIVE_FLOOR_PCT))),
            )
    return floors


def _reserve_key_metrics_budget(body_target: int, narrative_floor_total: int) -> int:
    if body_target <= 0:
        return 0
    preferred = int(round(int(body_target) * float(KEY_METRICS_WEIGHT)))
    preferred = max(int(KEY_METRICS_MIN_WORDS), min(int(KEY_METRICS_MAX_WORDS), preferred))
    max_allowed = max(0, int(body_target) - int(narrative_floor_total))
    if max_allowed <= 0:
        return 0
    return max(0, min(int(preferred), int(max_allowed)))


def get_default_section_weights(include_health_rating: bool) -> Dict[str, int]:
    if include_health_rating:
        return dict(DEFAULT_SECTION_WEIGHTS_WITH_HEALTH)
    return dict(DEFAULT_SECTION_WEIGHTS_WITHOUT_HEALTH)


def get_effective_section_weights(
    *,
    include_health_rating: bool,
    weight_overrides: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    narrative_sections = _narrative_sections(include_health_rating)
    defaults = {
        section_name: int(get_default_section_weights(include_health_rating).get(section_name, 0) or 0)
        for section_name in narrative_sections
    }
    if not weight_overrides:
        return defaults

    weights = dict(defaults)
    for section_name in narrative_sections:
        override = weight_overrides.get(section_name)
        if override is None:
            continue
        try:
            parsed = int(override)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            weights[section_name] = parsed

    if sum(weights.values()) <= 0:
        return defaults
    return weights


def calculate_section_word_budgets(
    target_length: int,
    *,
    include_health_rating: bool,
    weight_overrides: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """Calculate section body budgets that sum exactly to the body target."""
    if not target_length or int(target_length) <= 0:
        return {}

    weights = get_effective_section_weights(
        include_health_rating=include_health_rating,
        weight_overrides=weight_overrides,
    )
    narrative_sections = list(weights.keys())
    if not narrative_sections:
        return {}

    all_sections_to_use = list(narrative_sections) + [_KEY_METRICS_SECTION]

    heading_words = sum(
        len(re.findall(r"\b\w+\b", section_name)) for section_name in all_sections_to_use
    )
    body_target = max(0, int(target_length) - int(heading_words))
    if body_target <= 0:
        body_target = int(target_length)

    provisional_key_metrics_budget = max(
        int(KEY_METRICS_MIN_WORDS),
        min(int(KEY_METRICS_MAX_WORDS), int(round(int(body_target) * float(KEY_METRICS_WEIGHT)))),
    )
    provisional_narrative_target = max(0, int(body_target) - int(provisional_key_metrics_budget))
    floors = compute_proportional_floors(provisional_narrative_target, narrative_sections)
    narrative_floor_total = sum(
        int(floors.get(section_name, 0) or 0) for section_name in narrative_sections
    )
    key_metrics_budget = _reserve_key_metrics_budget(body_target, narrative_floor_total)
    narrative_body_target = max(0, int(body_target) - int(key_metrics_budget))
    floors = compute_proportional_floors(narrative_body_target, narrative_sections)
    narrative_floor_total = sum(
        int(floors.get(section_name, 0) or 0) for section_name in narrative_sections
    )

    if narrative_body_target < narrative_floor_total:
        key_metrics_budget = max(0, int(body_target) - int(narrative_floor_total))
        narrative_body_target = max(0, int(body_target) - int(key_metrics_budget))
        floors = compute_proportional_floors(narrative_body_target, narrative_sections)

    total_weight = sum(int(weights.get(section_name, 0) or 0) for section_name in narrative_sections)
    if total_weight <= 0:
        total_weight = len(narrative_sections) if narrative_sections else 1

    budgets: Dict[str, int] = {
        section_name: int(floors.get(section_name, 0) or 0)
        for section_name in narrative_sections
    }
    remaining = max(0, int(narrative_body_target) - sum(budgets.values()))
    exact_extras = {
        section_name: (
            int(weights.get(section_name, 0) or 0) * remaining / total_weight
        )
        for section_name in narrative_sections
    }
    extra_budgets = {
        section_name: int(exact_extras.get(section_name, 0.0) or 0)
        for section_name in narrative_sections
    }
    extra_budgets = _apply_integer_drift(
        extra_budgets,
        exact_extras,
        narrative_sections,
        target_total=int(remaining),
    )
    for section_name in narrative_sections:
        budgets[section_name] = int(budgets.get(section_name, 0) or 0) + int(
            extra_budgets.get(section_name, 0) or 0
        )

    budgets[_KEY_METRICS_SECTION] = int(key_metrics_budget or 0)

    ordered: Dict[str, int] = {}
    for section_name in CANONICAL_SECTION_ORDER:
        if section_name in budgets:
            ordered[section_name] = int(budgets[section_name])
    return ordered


def section_budget_tolerance_words(section_name: str, budget_words: int) -> int:
    """Return tolerance in words for the given section budget.

    Sections with budget <= 250 words use a 5% band to reduce retry churn
    at mid-range targets (1000-1500 total words).  Larger sections keep the
    tighter 3% band.  Key Metrics remains exact because it is validator-driven.

    Risk Factors at mid-range budgets (110-250) uses an 8% band because the
    per-risk word allocation is still tight even with 2 structured risks and LLMs
    frequently undershoot.
    """
    budget_words = max(0, int(budget_words or 0))
    if section_name == _KEY_METRICS_SECTION:
        return 3
    if budget_words <= 0:
        return NARRATIVE_SECTION_TOLERANCE_FLOOR
    # Risk Factors in the 3-risk squeeze zone: use 8% to absorb variance.
    if section_name == _RISK_FACTORS_SECTION and 110 <= budget_words <= 250:
        return max(NARRATIVE_SECTION_TOLERANCE_FLOOR, int(round(budget_words * 0.08)))
    rate = 0.05 if budget_words <= 250 else 0.03
    return max(NARRATIVE_SECTION_TOLERANCE_FLOOR, int(round(budget_words * rate)))
