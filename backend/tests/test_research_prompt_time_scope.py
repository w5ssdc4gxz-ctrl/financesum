from app.services.openai_client import build_company_research_prompt


def test_research_prompt_enforces_filing_date_time_scope() -> None:
    prompt = build_company_research_prompt(
        company_name="Microsoft Corp",
        ticker="MSFT",
        filing_type="10-Q",
        filing_date="2015-03-31",
    )

    assert "filing-date-grounded background knowledge" in prompt
    assert "Use only facts/events dated on or before 2015-03-31." in prompt
    assert "Do NOT reference events after 2015-03-31." in prompt
    assert "If a source date cannot be verified, omit that fact." in prompt
