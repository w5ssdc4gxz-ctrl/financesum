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
        "Financial Health Rating": 85,
        "Executive Summary": 74,
        "Financial Performance": 85,
        "Management Discussion & Analysis": 85,
        "Risk Factors": 93,
        "Key Metrics": 43,
        "Closing Takeaway": 69,
    }


def test_section_word_budgets_follow_v2_distribution_at_1000_words() -> None:
    budgets = filings_api._calculate_section_word_budgets(1000, include_health_rating=True)

    assert sum(budgets.values()) == 1000 - _heading_word_count(True)
    assert budgets == {
        "Financial Health Rating": 157,
        "Executive Summary": 135,
        "Financial Performance": 157,
        "Management Discussion & Analysis": 157,
        "Risk Factors": 172,
        "Key Metrics": 79,
        "Closing Takeaway": 127,
    }


def test_section_word_budgets_without_health_rating_follow_v2_distribution() -> None:
    budgets = filings_api._calculate_section_word_budgets(
        1000,
        include_health_rating=False,
    )

    assert sum(budgets.values()) == 1000 - _heading_word_count(False)
    assert "Financial Health Rating" not in budgets
    assert budgets == {
        "Executive Summary": 165,
        "Financial Performance": 195,
        "Management Discussion & Analysis": 195,
        "Risk Factors": 201,
        "Key Metrics": 79,
        "Closing Takeaway": 152,
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
                "Executive Summary": 39,
                "Financial Performance": 45,
                "Management Discussion & Analysis": 45,
                "Risk Factors": 51,
                "Key Metrics": 23,
                "Closing Takeaway": 37,
            },
        ),
        (
            301,
            False,
            {
                "Executive Summary": 48,
                "Financial Performance": 56,
                "Management Discussion & Analysis": 56,
                "Risk Factors": 60,
                "Key Metrics": 23,
                "Closing Takeaway": 45,
            },
        ),
        (
            742,
            True,
            {
                "Financial Health Rating": 116,
                "Executive Summary": 100,
                "Financial Performance": 116,
                "Management Discussion & Analysis": 115,
                "Risk Factors": 127,
                "Key Metrics": 58,
                "Closing Takeaway": 94,
            },
        ),
        (
            742,
            False,
            {
                "Executive Summary": 122,
                "Financial Performance": 144,
                "Management Discussion & Analysis": 144,
                "Risk Factors": 149,
                "Key Metrics": 58,
                "Closing Takeaway": 112,
            },
        ),
        (
            1183,
            True,
            {
                "Financial Health Rating": 187,
                "Executive Summary": 161,
                "Financial Performance": 187,
                "Management Discussion & Analysis": 187,
                "Risk Factors": 204,
                "Key Metrics": 90,
                "Closing Takeaway": 151,
            },
        ),
        (
            1183,
            False,
            {
                "Executive Summary": 196,
                "Financial Performance": 232,
                "Management Discussion & Analysis": 232,
                "Risk Factors": 239,
                "Key Metrics": 90,
                "Closing Takeaway": 181,
            },
        ),
        (
            2999,
            True,
            {
                "Financial Health Rating": 501,
                "Executive Summary": 434,
                "Financial Performance": 501,
                "Management Discussion & Analysis": 501,
                "Risk Factors": 550,
                "Key Metrics": 90,
                "Closing Takeaway": 406,
            },
        ),
        (
            2999,
            False,
            {
                "Executive Summary": 525,
                "Financial Performance": 622,
                "Management Discussion & Analysis": 622,
                "Risk Factors": 641,
                "Key Metrics": 90,
                "Closing Takeaway": 486,
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

    assert budgets["Financial Health Rating"] == 501
    assert health_shape.min_sentences == 8
    assert health_shape.max_sentences == 10
    assert health_shape.preferred_paragraphs == 2

    assert budgets["Risk Factors"] == 550
    assert risk_shape.risk_count == 2
    assert risk_shape.per_risk_min_sentences == 2
    assert risk_shape.per_risk_max_sentences == 3
    assert risk_shape.requires_early_warning_signal is False

    assert budgets["Closing Takeaway"] == 406
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
