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

