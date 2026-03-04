from app.api import filings as filings_api


def test_closing_structure_validator_accepts_three_part_close() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Executive Summary\n"
        "Hold stance given mixed evidence.\n\n"
        "## Closing Takeaway\n"
        "HOLD the position for now. "
        "The primary risk is that cash conversion weakens if working capital stays unfavorable. "
        "I would upgrade to BUY if operating cash flow margin is above 20% for the next two quarters. "
        "I would downgrade to SELL if free-cash-flow margin falls below 8% in the next 12 months."
    )
    assert validator(text) is None


def test_closing_structure_validator_requires_at_least_one_measurable_trigger() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Closing Takeaway\n"
        "BUY the stock. "
        "The primary risk is margin compression from competitive pricing. "
        "Execution has improved, but conviction remains balanced."
    )
    issue = validator(text)
    assert issue is not None
    assert "at least one measurable monitoring trigger" in issue


def test_closing_structure_validator_rejects_parenthetical_filler() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Closing Takeaway\n"
        "HOLD the stock. "
        "The primary risk is execution slippage (on a forward basis). "
        "I would upgrade to BUY if operating margin is above 18% next two quarters. "
        "I would downgrade to SELL if free cash flow drops below $500M next 12 months."
    )
    issue = validator(text)
    assert issue is not None
    assert "parenthetical buzzword filler" in issue


def test_closing_structure_validator_rejects_repeated_time_window_phrases() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Closing Takeaway\n"
        "HOLD the stock. "
        "The primary risk is execution slippage. "
        "I would upgrade to BUY if operating margin is above 18% over the next two quarters. "
        "I would downgrade to SELL if free cash flow drops below $500M over the next two quarters."
    )
    issue = validator(text)
    assert issue is not None
    assert "repeats the same monitoring-window phrase" in issue


def test_closing_structure_validator_rejects_parenthetical_chain_spam() -> None:
    validator = filings_api._make_closing_structure_validator()
    text = (
        "## Closing Takeaway\n"
        "HOLD the stock for now (key swing factor) (operating backdrop) (execution priority) (within this framework). "
        "I would upgrade to BUY if operating margin is above 18% over the next two quarters. "
        "I would downgrade to SELL if free cash flow drops below $500M in the next 12 months."
    )
    issue = validator(text)
    assert issue is not None
    assert "parenthetical" in issue.lower()
