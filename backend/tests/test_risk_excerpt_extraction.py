import re

from app.api import filings as filings_api


class TestLooksLikeTableOfContentsSnippet:
    def test_dot_leaders_detected(self):
        toc = (
            "RISK FACTORS ....................... 49\n"
            "MANAGEMENT DISCUSSION .............. 52\n"
            "FINANCIAL STATEMENTS ............... 78\n"
        )
        assert filings_api._looks_like_table_of_contents_snippet(toc)

    def test_item_heavy_short_snippet(self):
        toc = (
            "Table of Contents\n"
            "ITEM 1. BUSINESS\n"
            "ITEM 1A. RISK FACTORS\n"
            "ITEM 2. PROPERTIES\n"
            "ITEM 3. LEGAL PROCEEDINGS\n"
        )
        assert filings_api._looks_like_table_of_contents_snippet(toc)

    def test_real_risk_content_not_flagged(self):
        real = (
            "The company faces significant competitive pressure from cloud providers "
            "who are vertically integrating their hardware stacks. Our reliance on TSMC "
            "for advanced node manufacturing creates concentration risk that could "
            "adversely affect our ability to deliver products on schedule."
        )
        assert not filings_api._looks_like_table_of_contents_snippet(real)


class TestLooksLikeTocEntry:
    def test_page_number_then_header(self):
        assert filings_api._looks_like_toc_entry("49\nMANAGEMENT DISCUSSION & ANALYSIS")

    def test_predominantly_item_lines(self):
        text = "ITEM 1\nITEM 1A\nITEM 2\n42"
        assert filings_api._looks_like_toc_entry(text)

    def test_real_content_not_flagged(self):
        real = (
            "Our business depends on maintaining strong relationships with key customers "
            "and any loss of a significant customer could adversely affect revenue. "
            "We have experienced increased pricing pressure in our enterprise segment."
        )
        assert not filings_api._looks_like_toc_entry(real)

    def test_empty_text(self):
        assert filings_api._looks_like_toc_entry("")
        assert filings_api._looks_like_toc_entry("   ")


class TestCleanRiskExcerpt:
    def test_strips_page_numbers(self):
        text = "Some risk content here.\n49\nMore risk content."
        cleaned = filings_api._clean_risk_excerpt(text)
        assert "49" not in cleaned.split("\n")
        assert "Some risk content" in cleaned

    def test_strips_bare_item_references(self):
        text = "Risk content.\nITEM 1A\nMore content."
        cleaned = filings_api._clean_risk_excerpt(text)
        assert "ITEM 1A" not in cleaned
        assert "Risk content" in cleaned

    def test_strips_toc_style_lines(self):
        text = "Real risk discussion.\nMANAGEMENT DISCUSSION 52\nAnother sentence."
        cleaned = filings_api._clean_risk_excerpt(text)
        assert "MANAGEMENT DISCUSSION 52" not in cleaned

    def test_strips_forward_looking_disclaimers(self):
        text = (
            "These forward-looking statements involve risks.\n"
            "Our revenue depends on cloud adoption rates."
        )
        cleaned = filings_api._clean_risk_excerpt(text)
        assert "forward-looking" not in cleaned
        assert "cloud adoption" in cleaned

    def test_strips_short_orphan_headers_and_toc_fragments(self):
        text = (
            "ACTUAL EXECUTION\n"
            "Conversion Risk\n"
            "Our reliance on TSMC could delay shipments and backlog conversion."
        )
        cleaned = filings_api._clean_risk_excerpt(text)
        assert "ACTUAL EXECUTION" not in cleaned
        assert "Conversion Risk" not in cleaned
        assert "TSMC" in cleaned

    def test_preserves_real_content(self):
        real = "The company faces concentration risk from TSMC dependency."
        assert filings_api._clean_risk_excerpt(real) == real


class TestRiskExcerptHasSubstance:
    def test_empty_fails(self):
        assert not filings_api._risk_excerpt_has_substance("")
        assert not filings_api._risk_excerpt_has_substance("   ")

    def test_too_short_fails(self):
        assert not filings_api._risk_excerpt_has_substance("short text only")

    def test_no_risk_language_fails(self):
        text = " ".join(f"word{i}" for i in range(40))
        assert not filings_api._risk_excerpt_has_substance(text)

    def test_real_risk_content_passes(self):
        text = (
            "The company faces significant competitive pressure from cloud providers "
            "who are vertically integrating their hardware stacks. Our reliance on TSMC "
            "for advanced manufacturing creates concentration risk that could adversely "
            "affect our ability to deliver products on schedule."
        )
        assert filings_api._risk_excerpt_has_substance(text)


class TestExtractLabeledExcerptSkipsToc:
    def test_skips_toc_picks_real_section(self):
        doc = (
            "Table of Contents\n"
            "RISK FACTORS ....................... 49\n"
            "MANAGEMENT DISCUSSION .............. 52\n\n"
            "SOME OTHER SECTION\n"
            "Content here.\n\n"
            "RISK FACTORS\n"
            "The company operates in a highly competitive market and faces significant "
            "risks related to customer concentration, supply chain disruptions, and "
            "regulatory changes that could adversely affect our business operations "
            "and financial performance over the next several quarters.\n\n"
            "MANAGEMENT DISCUSSION & ANALYSIS\n"
            "Revenue grew 15% year-over-year.\n"
        )
        result = filings_api._extract_labeled_excerpt(doc, "RISK FACTORS")
        assert result is not None
        assert "competitive market" in result
        assert "49" not in result.split("\n")[0]
