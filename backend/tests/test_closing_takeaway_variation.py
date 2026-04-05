import re

from app.api import filings as filings_api


def test_ensure_personal_verdict_respects_existing_personal_recommendation():
    closing = "Balanced setup. For my own portfolio, I'd HOLD TestCo at this valuation."
    assert filings_api._ensure_personal_verdict(closing, "TestCo") == closing


def test_ensure_personal_verdict_appends_action_and_company():
    closing = "Balanced setup."
    result = filings_api._ensure_personal_verdict(
        closing,
        "TestCo",
        strengths=["strong cash generation"],
        concerns=["elevated leverage"],
    )
    assert "TestCo" in result
    assert re.search(r"\b(buy|hold|sell)\b", result, re.IGNORECASE)


def test_repair_closing_recommendation_in_summary_appends_personal_verdict_when_only_objective_stance_exists():
    summary = (
        "## Closing Takeaway\n"
        "MICROSOFT CORP looks balanced here. A HOLD rating is appropriate for MICROSOFT CORP."
    )
    repaired = filings_api._repair_closing_recommendation_in_summary(
        summary,
        company_name="MICROSOFT CORP",
        calculated_metrics={
            "operating_margin": 28.0,
            "net_margin": 18.0,
            "free_cash_flow": 10.0,
            "revenue": 100.0,
        },
        persona_requested=True,
    )
    closing = filings_api._extract_markdown_section_body(repaired, "Closing Takeaway")
    assert closing is not None
    assert filings_api._contains_explicit_personal_recommendation(closing)
    assert "MICROSOFT CORP" in closing


def test_repair_closing_recommendation_in_summary_appends_objective_verdict_when_hold_is_only_a_verb():
    summary = (
        "## Closing Takeaway\n"
        "The thesis can hold if margin durability improves and cash conversion stays disciplined."
    )
    repaired = filings_api._repair_closing_recommendation_in_summary(
        summary,
        company_name="TestCo",
        calculated_metrics={
            "operating_margin": 22.0,
            "net_margin": 12.0,
            "free_cash_flow": 15.0,
            "revenue": 100.0,
        },
        persona_requested=False,
    )
    closing = filings_api._extract_markdown_section_body(repaired, "Closing Takeaway")
    assert closing is not None
    assert filings_api._contains_explicit_objective_recommendation(closing)
    assert "TestCo" in closing


def test_persona_fallback_mixed_avoids_repeated_cycle_opener():
    dalio = filings_api._generate_persona_flavored_closing(
        "Ray Dalio",
        "TestCo",
        strengths=["strong cash generation"],
        concerns=["elevated leverage"],
        quality="mixed",
        is_positive=False,
        is_mixed=True,
        revenue=None,
        operating_margin=None,
    )
    marks = filings_api._generate_persona_flavored_closing(
        "Howard Marks",
        "TestCo",
        strengths=["strong cash generation"],
        concerns=["elevated leverage"],
        quality="mixed",
        is_positive=False,
        is_mixed=True,
        revenue=None,
        operating_margin=None,
    )
    assert "Where are we in the cycle?" not in dalio
    assert "Where are we in the cycle?" not in marks


def test_repair_restores_recommendation_after_word_band_trimming():
    """Recommendation must be present after word-band enforcement strips it and repair re-runs."""
    target = 1000
    tolerance = filings_api._effective_word_band_tolerance(target)
    budgets = filings_api._calculate_section_word_budgets(
        target, include_health_rating=False
    )

    sections = [
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ]
    parts = []
    for title in sections:
        budget = int(budgets.get(title, 0) or 80)
        if title == "Key Metrics":
            body = "\n".join(
                f"-> Metric{i}: ${i * 10}M" for i in range(1, 6)
            )
        elif title == "Closing Takeaway":
            # Pad closing over budget so trimming will target it
            filler = " ".join(
                f"sentence{i} word word word word word." for i in range(budget // 5 + 5)
            )
            body = filler + " A Hold rating appears warranted for TestCo."
        else:
            body = " ".join(f"word{i}" for i in range(budget)) + "."
        parts.append(f"## {title}\n{body}")

    summary = "\n\n".join(parts)

    # Word-band enforcement may chop the recommendation sentence off the end
    trimmed = filings_api._enforce_whitespace_word_band(
        summary,
        target,
        tolerance=tolerance,
        allow_padding=False,
        dedupe=True,
    )

    # Repair should restore it
    repaired = filings_api._repair_closing_recommendation_in_summary(
        trimmed,
        company_name="TestCo",
        calculated_metrics={"operating_margin": 15.0, "revenue": 50.0},
        persona_requested=False,
    )

    closing = filings_api._extract_markdown_section_body(repaired, "Closing Takeaway")
    assert closing is not None
    assert re.search(r"\b(buy|hold|sell)\b", closing, re.IGNORECASE), (
        "Recommendation must be present after word-band trim + repair"
    )
