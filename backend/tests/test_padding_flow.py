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
    padded = " ".join(filings_api._generate_padding_sentences(80))
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

    padded = filings_api._distribute_padding_across_sections(base, required_words=25)

    # Padding should not be dumped into a single section. For this synthetic base,
    # we intentionally avoid padding Executive Summary / Risk Factors / Closing Takeaway,
    # so the added words should land in MD&A and Financial Performance first.
    assert _get_section_body(padded, "Executive Summary") == _get_section_body(
        base, "Executive Summary"
    )
    assert _get_section_body(padded, "Financial Health Rating") == _get_section_body(
        base, "Financial Health Rating"
    )
    assert _get_section_body(padded, "Key Metrics") == _get_section_body(base, "Key Metrics")
    assert _get_section_body(padded, "Closing Takeaway") == _get_section_body(
        base, "Closing Takeaway"
    )
    assert _get_section_body(padded, "Risk Factors") == _get_section_body(
        base, "Risk Factors"
    )

    mdna_body = _get_section_body(padded, "Management Discussion & Analysis")
    base_mdna = _get_section_body(base, "Management Discussion & Analysis")
    assert len(mdna_body.split()) > len(base_mdna.split())

    fp_body = _get_section_body(padded, "Financial Performance")
    assert len(fp_body.split()) > len(_get_section_body(base, "Financial Performance").split())
