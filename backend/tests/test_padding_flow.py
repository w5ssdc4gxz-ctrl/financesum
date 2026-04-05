import re


from app.api import filings as filings_api


def _get_section_body(text: str, title: str) -> str:
    """Extract section body for a given '## {title}' heading."""
    pattern = re.compile(
        rf"^\s*##\s*{re.escape(title)}\s*\n+(.*?)(?=^\s*##\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"Missing section: {title}"
    return match.group(1).strip()


def test_padding_templates_avoid_legacy_micro_slogans() -> None:
    padded = " ".join(
        filings_api._generate_padding_sentences(80, section="Financial Performance")
    )
    banned = [
        "Earnings quality is the key question.",
        "Durability matters more than optics.",
        "Focus on what is repeatable.",
        "Cash flow anchors the thesis.",
        "Margins must hold through competition.",
        "Leverage shapes downside risk.",
        "Scale must translate to profit.",
        "One-off gains should be discounted.",
        "One‑off gains should be discounted.",
        "Unit economics should improve with scale.",
        "Valuation should match durability.",
    ]
    for phrase in banned:
        assert phrase not in padded


def test_padding_is_spread_across_shortest_sections() -> None:
    """Padding system is disabled — _distribute_padding_across_sections is a no-op.

    Verify that no sections grow (padding templates are intentionally killed).
    """
    base = (
        "## Financial Health Rating\n"
        "ExampleCo receives a Financial Health Rating of 72/100 - Healthy because operating margin strength and cash conversion outweigh leverage.\n\n"
        "## Executive Summary\n"
        "Bulls point to its moat and long runway, while bears highlight regulation and reinvestment intensity.\n\n"
        "## Financial Performance\n"
        "Revenue grew and margins were steady, but cash conversion is the key variable to watch.\n\n"
        "## Management Discussion & Analysis\n"
        "Management emphasized product velocity, capex pacing, and operating discipline.\n\n"
        "## Risk Factors\n"
        "Regulation, competition, and execution are the main risks.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n"
        "→ Operating Margin: 10%\n\n"
        "## Closing Takeaway\n"
        "Overall, this is a business to watch, but the risk-reward is not obviously asymmetric today."
    )

    padded = filings_api._distribute_padding_across_sections(base, required_words=60)

    # Padding is disabled: output should be identical to input.
    assert padded == base
