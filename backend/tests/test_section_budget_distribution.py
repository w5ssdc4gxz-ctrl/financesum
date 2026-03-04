import pytest
import re

from app.api import filings as filings_api
from app.services.summary_budget_controller import (
    compute_proportional_floors,
    get_closing_takeaway_shape,
    get_financial_health_shape,
    get_risk_factors_shape,
)


_NARRATIVE_SECTIONS_WITH_HEALTH = [
    "Financial Health Rating",
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Closing Takeaway",
]
_NARRATIVE_SECTIONS_WITHOUT_HEALTH = [
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Closing Takeaway",
]


def _heading_word_count(include_health_rating: bool) -> int:
    sections = [
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ]
    if include_health_rating:
        sections = ["Financial Health Rating", *sections]
    return sum(len(re.findall(r"\b\w+\b", section_name)) for section_name in sections)


def _assert_narrative_floors(
    budgets: dict[str, int],
    *,
    include_health_rating: bool,
) -> None:
    narrative_sections = (
        _NARRATIVE_SECTIONS_WITH_HEALTH
        if include_health_rating
        else _NARRATIVE_SECTIONS_WITHOUT_HEALTH
    )
    narrative_target = sum(budgets[section_name] for section_name in narrative_sections)
    floors = compute_proportional_floors(narrative_target, narrative_sections)
    for section_name in narrative_sections:
        assert budgets[section_name] >= floors[section_name]
    assert 20 <= budgets["Key Metrics"] <= 90


def test_section_word_budgets_follow_v2_distribution_at_550_words() -> None:
    budgets = filings_api._calculate_section_word_budgets(550, include_health_rating=True)

    assert sum(budgets.values()) == 550 - _heading_word_count(True)
    assert budgets == {
        "Financial Health Rating": 86,
        "Executive Summary": 75,
        "Financial Performance": 86,
        "Management Discussion & Analysis": 86,
        "Risk Factors": 95,
        "Key Metrics": 43,
        "Closing Takeaway": 63,
    }


def test_section_word_budgets_follow_v2_distribution_at_1000_words() -> None:
    budgets = filings_api._calculate_section_word_budgets(1000, include_health_rating=True)

    assert sum(budgets.values()) == 1000 - _heading_word_count(True)
    assert budgets == {
        "Financial Health Rating": 159,
        "Executive Summary": 138,
        "Financial Performance": 159,
        "Management Discussion & Analysis": 159,
        "Risk Factors": 174,
        "Key Metrics": 79,
        "Closing Takeaway": 116,
    }


def test_section_word_budgets_without_health_rating_follow_v2_distribution() -> None:
    budgets = filings_api._calculate_section_word_budgets(
        1000,
        include_health_rating=False,
    )

    assert sum(budgets.values()) == 1000 - _heading_word_count(False)
    assert "Financial Health Rating" not in budgets
    assert budgets == {
        "Executive Summary": 168,
        "Financial Performance": 200,
        "Management Discussion & Analysis": 200,
        "Risk Factors": 204,
        "Key Metrics": 79,
        "Closing Takeaway": 136,
    }


@pytest.mark.parametrize("target_length", [300, 450, 650, 1000])
def test_short_form_section_word_budgets_apply_narrative_floors_with_health(
    target_length: int,
) -> None:
    budgets = filings_api._calculate_section_word_budgets(
        target_length,
        include_health_rating=True,
    )

    assert sum(budgets.values()) == target_length - _heading_word_count(True)
    _assert_narrative_floors(budgets, include_health_rating=True)


@pytest.mark.parametrize("target_length", [300, 450, 650, 1000])
def test_short_form_section_word_budgets_apply_narrative_floors_without_health(
    target_length: int,
) -> None:
    budgets = filings_api._calculate_section_word_budgets(
        target_length,
        include_health_rating=False,
    )

    assert sum(budgets.values()) == target_length - _heading_word_count(False)
    assert "Financial Health Rating" not in budgets
    _assert_narrative_floors(budgets, include_health_rating=False)


@pytest.mark.parametrize(
    ("target_length", "include_health_rating", "expected"),
    [
        (
            301,
            True,
            {
                "Financial Health Rating": 45,
                "Executive Summary": 40,
                "Financial Performance": 45,
                "Management Discussion & Analysis": 45,
                "Risk Factors": 52,
                "Key Metrics": 23,
                "Closing Takeaway": 35,
            },
        ),
        (
            301,
            False,
            {
                "Executive Summary": 49,
                "Financial Performance": 57,
                "Management Discussion & Analysis": 57,
                "Risk Factors": 61,
                "Key Metrics": 23,
                "Closing Takeaway": 41,
            },
        ),
        (
            742,
            True,
            {
                "Financial Health Rating": 118,
                "Executive Summary": 101,
                "Financial Performance": 118,
                "Management Discussion & Analysis": 118,
                "Risk Factors": 128,
                "Key Metrics": 58,
                "Closing Takeaway": 85,
            },
        ),
        (
            742,
            False,
            {
                "Executive Summary": 124,
                "Financial Performance": 148,
                "Management Discussion & Analysis": 147,
                "Risk Factors": 151,
                "Key Metrics": 58,
                "Closing Takeaway": 101,
            },
        ),
        (
            1183,
            True,
            {
                "Financial Health Rating": 190,
                "Executive Summary": 164,
                "Financial Performance": 189,
                "Management Discussion & Analysis": 189,
                "Risk Factors": 207,
                "Key Metrics": 90,
                "Closing Takeaway": 138,
            },
        ),
        (
            1183,
            False,
            {
                "Executive Summary": 200,
                "Financial Performance": 237,
                "Management Discussion & Analysis": 237,
                "Risk Factors": 244,
                "Key Metrics": 90,
                "Closing Takeaway": 162,
            },
        ),
        (
            2999,
            True,
            {
                "Financial Health Rating": 509,
                "Executive Summary": 440,
                "Financial Performance": 509,
                "Management Discussion & Analysis": 509,
                "Risk Factors": 556,
                "Key Metrics": 90,
                "Closing Takeaway": 370,
            },
        ),
        (
            2999,
            False,
            {
                "Executive Summary": 536,
                "Financial Performance": 637,
                "Management Discussion & Analysis": 637,
                "Risk Factors": 652,
                "Key Metrics": 90,
                "Closing Takeaway": 434,
            },
        ),
    ],
)
def test_section_word_budgets_match_exact_v2_distribution_for_arbitrary_targets(
    target_length: int,
    include_health_rating: bool,
    expected: dict[str, int],
) -> None:
    budgets = filings_api._calculate_section_word_budgets(
        target_length,
        include_health_rating=include_health_rating,
    )

    assert budgets == expected
    assert sum(budgets.values()) == target_length - _heading_word_count(include_health_rating)
    _assert_narrative_floors(budgets, include_health_rating=include_health_rating)


def test_long_form_section_shapes_scale_with_2999_word_budgets() -> None:
    budgets = filings_api._calculate_section_word_budgets(2999, include_health_rating=True)

    health_shape = get_financial_health_shape(budgets["Financial Health Rating"])
    risk_shape = get_risk_factors_shape(budgets["Risk Factors"])
    closing_shape = get_closing_takeaway_shape(budgets["Closing Takeaway"])

    assert budgets["Financial Health Rating"] == 509
    assert health_shape.min_sentences == 8
    assert health_shape.max_sentences == 10
    assert health_shape.preferred_paragraphs == 2

    assert budgets["Risk Factors"] == 556
    assert risk_shape.risk_count == 3
    assert risk_shape.per_risk_min_sentences == 4
    assert risk_shape.per_risk_max_sentences == 5
    assert risk_shape.requires_early_warning_signal is True

    assert budgets["Closing Takeaway"] == 370
    assert closing_shape.min_sentences == 7
    assert closing_shape.max_sentences == 9
    assert closing_shape.min_paragraphs == 2
    assert closing_shape.max_paragraphs == 3
    assert closing_shape.requires_exactly_one_stance is True


def test_section_weight_overrides_ignore_key_metrics_and_dropped_health_weights() -> None:
    baseline = filings_api._calculate_section_word_budgets(
        1000,
        include_health_rating=False,
        weight_overrides={
            "Executive Summary": 2,
            "Financial Performance": 3,
            "Management Discussion & Analysis": 3,
            "Risk Factors": 4,
            "Closing Takeaway": 2,
        },
    )
    with_ignored_overrides = filings_api._calculate_section_word_budgets(
        1000,
        include_health_rating=False,
        weight_overrides={
            "Financial Health Rating": 999,
            "Executive Summary": 2,
            "Financial Performance": 3,
            "Management Discussion & Analysis": 3,
            "Risk Factors": 4,
            "Key Metrics": 999,
            "Closing Takeaway": 2,
        },
    )

    assert baseline == with_ignored_overrides
    assert with_ignored_overrides["Key Metrics"] == baseline["Key Metrics"] == 79
    assert "Financial Health Rating" not in with_ignored_overrides
