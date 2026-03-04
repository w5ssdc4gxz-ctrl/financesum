from app.api import filings as filings_api


def test_transition_validator_flags_missing_handoffs() -> None:
    validator = filings_api._make_section_transition_validator(
        include_health_rating=True, target_length=650
    )
    text = (
        "## Financial Health Rating\n"
        "ExampleCo receives a Financial Health Rating of 72/100 - Healthy.\n"
        "The balance sheet looks fine and liquidity is adequate.\n\n"
        "## Executive Summary\n"
        "The business is improving but valuation discipline matters.\n"
        "The story is still mixed.\n\n"
        "## Financial Performance\n"
        "Revenue rose and margins were steady, while cash conversion was uneven.\n\n"
        "## Management Discussion & Analysis\n"
        "Management highlighted initiatives and spending discipline.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Projects can slip and create cost overruns.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n\n"
        "## Closing Takeaway\n"
        "Hold pending cleaner cash conversion."
    )
    assert validator(text) is not None


def test_transition_validator_accepts_bridges() -> None:
    validator = filings_api._make_section_transition_validator(
        include_health_rating=True, target_length=650
    )
    text = (
        "## Financial Health Rating\n"
        "ExampleCo receives a Financial Health Rating of 72/100 - Healthy.\n"
        "Liquidity looks adequate and leverage does not appear binding.\n"
        "This balance-sheet backdrop frames the thesis that follows.\n\n"
        "## Executive Summary\n"
        "The setup is constructive, but the decision hinges on whether operating gains translate into durable cash conversion.\n"
        "That call ultimately rests on the numbers, margins, and cash bridge in Financial Performance.\n\n"
        "## Financial Performance\n"
        "Revenue grew and margins held, but working-capital swings kept free cash flow noisier than earnings.\n"
        "That puts the spotlight on management’s capital allocation and reinvestment cadence in MD&A.\n\n"
        "## Management Discussion & Analysis\n"
        "Spend discipline and reinvestment pacing look directionally rational, but execution needs to stay tight to protect margins.\n"
        "Those choices define the downside, so the next step is to stress-test the concrete risk factors.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Projects can slip and create cost overruns; severity/likelihood: Medium/Medium. "
        "This does not dominate the thesis yet, but it would if cash conversion deteriorates further, and the scoreboard is the Key Metrics lines below.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n\n"
        "## Closing Takeaway\n"
        "Hold until cash conversion stabilizes and margins prove durable."
    )
    assert validator(text) is None

