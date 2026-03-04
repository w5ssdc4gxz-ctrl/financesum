"""Multi-industry test fixtures for the evaluation harness.

Each fixture provides a filing excerpt (simulating source text) and a
well-formed sample summary at a given target length.  Summaries are
hand-crafted to pass all eval-harness checks so they can serve as
positive baselines in automated tests.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _make_words(n: int, token: str = "word") -> str:
    """Generate *n* repeated tokens separated by spaces."""
    if n <= 0:
        return ""
    return " ".join([token] * n)


# ---------------------------------------------------------------------------
# Helper to build a well-formed summary skeleton at an exact word count.
# ---------------------------------------------------------------------------

def build_valid_summary(target: int, *, include_health_rating: bool = False) -> str:
    """Return a deterministic, well-structured summary that hits *target* words.

    The content is distributed across all required sections so that section-
    completeness checks pass.  Narrative connectors and attribution phrases
    are sprinkled in to satisfy flow and quote-validation checks.
    """
    # Budget allocation across sections (approximate percentages).
    sections_budget = {
        "Executive Summary": 0.20,
        "Financial Performance": 0.20,
        "Management Discussion & Analysis": 0.20,
        "Risk Factors": 0.15,
        "Key Metrics": 0.10,
        "Closing Takeaway": 0.15,
    }

    health_words = 0
    health_block = ""
    if include_health_rating:
        health_block = (
            "## Financial Health Rating\n"
            "The company receives a Financial Health Rating of 74 out of 100 "
            "indicating a Healthy balance sheet with adequate liquidity and "
            "manageable leverage for the current operating environment.\n\n"
        )
        health_words = 21  # approximate

    remaining = target - health_words
    lines: List[str] = []
    if health_block:
        lines.append(health_block)

    # Narrative connector pool (rotated across sections).
    connectors = [
        "However, ",
        "This suggests ",
        "Looking ahead, ",
        "Importantly, ",
        "Notably, ",
        "Consequently, ",
        "Meanwhile, ",
    ]
    connector_idx = 0

    running_total = 0
    section_items = list(sections_budget.items())
    for idx, (title, pct) in enumerate(section_items):
        budget = int(remaining * pct)
        # Last section gets the remainder to hit exact target.
        if idx == len(section_items) - 1:
            budget = target - running_total - health_words

        lines.append(f"## {title}")

        if title == "Key Metrics":
            # Key Metrics uses arrow-prefixed lines (non-prose).
            metric_lines = [
                "Revenue: $52.0B",
                "Operating Margin: 33.0%",
                "Free Cash Flow: $12.5B",
                "Net Margin: 28.5%",
                "Current Ratio: 1.8x",
            ]
            body_parts: List[str] = []
            for ml in metric_lines:
                body_parts.append(f"\u2192 {ml}")
            body = "\n".join(body_parts)
            section_wc = len(body.split())
        elif title == "Risk Factors":
            connector = connectors[connector_idx % len(connectors)]
            connector_idx += 1
            risk_intro = (
                f"**Execution Risk**: {connector}if reinvestment outpaces demand, "
                "margins can compress before cost actions catch up. "
                "The early warning signal is sustained deterioration in cash "
                "conversion relative to operating profit."
            )
            filler_needed = max(0, budget - len(risk_intro.split()))
            body = risk_intro
            if filler_needed > 0:
                body += " " + _make_words(filler_needed, "steady")
            section_wc = len(body.split())
        elif title == "Closing Takeaway":
            body = (
                "HOLD remains appropriate while operating execution stabilizes. "
                "I would upgrade to BUY if operating margin is above 35 percent "
                "over the next two quarters. "
                "I would downgrade to SELL if free cash flow falls below eight "
                "billion dollars over the next twelve months."
            )
            filler_needed = max(0, budget - len(body.split()))
            if filler_needed > 0:
                body += " " + _make_words(filler_needed, "balanced")
            section_wc = len(body.split())
        else:
            connector = connectors[connector_idx % len(connectors)]
            connector_idx += 1
            seed = (
                f"{connector}the setup is constructive and execution quality "
                "is stabilizing across pricing and reinvestment choices. "
                "Management noted that capital allocation remains disciplined "
                "while preserving flexibility if demand softens."
            )
            filler_needed = max(0, budget - len(seed.split()))
            body = seed
            if filler_needed > 0:
                body += " " + _make_words(filler_needed, "stable")
            section_wc = len(body.split())

        lines.append(body)
        lines.append("")  # blank line between sections
        running_total += section_wc

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Industry fixtures
# ---------------------------------------------------------------------------

TECH_AAPL: Dict[str, Any] = {
    "ticker": "AAPL",
    "company": "Apple Inc.",
    "sector": "Technology",
    "filing_excerpt": (
        'Apple reported quarterly revenue of $94.8 billion, up 6 percent year over year. '
        'Management stated "we are very pleased with our record results driven by iPhone and Services." '
        'Services revenue reached an all-time high of $23.1 billion. '
        'The company returned over $27 billion to shareholders during the quarter. '
        'Management noted "our installed base of active devices reached a new all-time high across all products and all geographic segments." '
        'Gross margin was 46.6 percent, up from 45.3 percent in the year-ago quarter. '
        'Operating cash flow was $39.9 billion for the trailing twelve months.'
    ),
    "target_length": 600,
}

HEALTHCARE_JNJ: Dict[str, Any] = {
    "ticker": "JNJ",
    "company": "Johnson & Johnson",
    "sector": "Healthcare",
    "filing_excerpt": (
        'Johnson & Johnson reported worldwide sales of $22.5 billion for the quarter, an increase of 5.2 percent. '
        'The Innovative Medicine segment delivered sales of $14.3 billion, reflecting strong demand for key oncology and immunology assets. '
        'Management stated "our diversified portfolio and pipeline strength position us well for sustainable long-term growth." '
        'The MedTech segment reported sales of $8.2 billion, with growth across orthopedics and cardiovascular platforms. '
        'Adjusted earnings per share were $2.71, exceeding consensus estimates. '
        'Management noted "we continue to advance our pipeline with over 100 programs in development."'
    ),
    "target_length": 900,
}

FINANCIAL_JPM: Dict[str, Any] = {
    "ticker": "JPM",
    "company": "JPMorgan Chase & Co.",
    "sector": "Financial",
    "filing_excerpt": (
        'JPMorgan Chase reported record quarterly net income of $14.3 billion on revenue of $43.7 billion. '
        'The Consumer & Community Banking segment generated net income of $5.1 billion. '
        'Management stated "credit costs were manageable and the consumer remains in a healthy position." '
        'Net interest income was $23.5 billion, up 5 percent year over year. '
        'The CET1 capital ratio stood at 15.0 percent, well above the regulatory minimum. '
        'Investment banking fees of $2.3 billion reflected strength in advisory and underwriting. '
        'Management noted "our fortress balance sheet and diversified business model delivered strong results across every line of business."'
    ),
    "target_length": 1200,
}

ENERGY_XOM: Dict[str, Any] = {
    "ticker": "XOM",
    "company": "Exxon Mobil Corporation",
    "sector": "Energy",
    "filing_excerpt": (
        'Exxon Mobil reported quarterly earnings of $9.2 billion on revenue of $84.3 billion. '
        'Upstream earnings were $6.1 billion, reflecting strong production volumes in the Permian Basin and Guyana. '
        'Production reached a record 4.2 million barrels of oil equivalent per day. '
        'Management stated "our integrated model and structural cost reductions continue to deliver industry-leading returns." '
        'Downstream earnings of $2.1 billion benefited from improved refining margins. '
        'Capital expenditures were $6.3 billion, focused on high-return development projects. '
        'Management noted "we remain committed to our capital allocation framework balancing investment, shareholder returns, and balance sheet strength."'
    ),
    "target_length": 2599,
}

CONSUMER_PG: Dict[str, Any] = {
    "ticker": "PG",
    "company": "Procter & Gamble Co.",
    "sector": "Consumer",
    "filing_excerpt": (
        'Procter & Gamble reported net sales of $21.9 billion, an increase of 3 percent versus the prior year. '
        'Organic sales grew 4 percent, driven by pricing and mix improvements across most categories. '
        'The Beauty segment delivered 5 percent organic growth led by SK-II and Olay. '
        'Management stated "our strategy of superiority, productivity, and constructive disruption continues to deliver strong results." '
        'Diluted net earnings per share were $1.84, up 10 percent year over year. '
        'Operating cash flow was $4.8 billion, with free cash flow productivity of 95 percent. '
        'Management noted "we are investing in innovation and brand-building while maintaining disciplined cost management."'
    ),
    "target_length": 600,
}

INDUSTRIAL_CAT: Dict[str, Any] = {
    "ticker": "CAT",
    "company": "Caterpillar Inc.",
    "sector": "Industrial",
    "filing_excerpt": (
        'Caterpillar reported quarterly revenue of $16.1 billion, up 4 percent versus the prior year. '
        'Construction Industries segment sales were $6.3 billion, reflecting solid demand in North America and improving trends in Asia-Pacific. '
        'The order backlog stood at $28.5 billion, down slightly from the prior quarter but above year-ago levels. '
        'Management stated "services revenue reached a record and continues to be a focus of our strategy for profitable growth." '
        'Operating profit margin expanded to 22.1 percent from 20.8 percent in the year-ago quarter. '
        'Management noted "our balanced capital allocation approach returned $3.2 billion to shareholders through dividends and share repurchases."'
    ),
    "target_length": 3000,
}


ALL_FIXTURES: List[Dict[str, Any]] = [
    TECH_AAPL,
    HEALTHCARE_JNJ,
    FINANCIAL_JPM,
    ENERGY_XOM,
    CONSUMER_PG,
    INDUSTRIAL_CAT,
]


def get_fixture_by_ticker(ticker: str) -> Dict[str, Any]:
    """Look up a fixture by ticker symbol."""
    for f in ALL_FIXTURES:
        if f["ticker"] == ticker:
            return f
    raise KeyError(f"No fixture for ticker {ticker!r}")
