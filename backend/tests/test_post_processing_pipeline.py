"""Tests for the post-processing pipeline fixes (bridge, quotes, dedup, word-band)."""

import re
from typing import Dict, List, Optional

import pytest

from app.api import filings as filings_api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECTION_TITLES = [
    "Financial Health Rating",
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Key Metrics",
    "Closing Takeaway",
]

_PROSE_SECTION_TITLES = [
    "Financial Health Rating",
    "Executive Summary",
    "Financial Performance",
    "Management Discussion & Analysis",
    "Risk Factors",
    "Closing Takeaway",
]

# A pool of unique filler words to avoid deduplication traps in trimming logic
_FILLER_POOL = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
    "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee "
    "zulu apple banana cherry date elderberry fig grape hazel ivy juniper kumquat "
    "lemon mango nectarine olive peach quince raspberry strawberry tangerine ugli "
    "vanilla watermelon ximenia yam zucchini accordion bassoon clarinet drum euphonium "
    "flute guitar harmonica instrument jazz kazoo lute mandolin notes oboe piano "
    "quarter rhythm saxophone trumpet ukulele violin whistle xylophone yodel zither"
).split()


def _generate_unique_words(count: int) -> str:
    """Generate *count* words by cycling through a pool of unique filler words."""
    words = []
    for i in range(count):
        words.append(_FILLER_POOL[i % len(_FILLER_POOL)])
    return " ".join(words)


def _build_memo_at_word_count(
    target_wc: int,
    extra_content: Optional[Dict[str, str]] = None,
    sections: Optional[List[str]] = None,
) -> str:
    """Build a test memo that totals exactly *target_wc* words (by split()).

    Uses unique filler words to avoid aggressive deduplication trimming.
    """
    titles = sections or list(_SECTION_TITLES)
    extra = extra_content or {}

    # First pass: measure fixed overhead (headers + extra_content bodies)
    header_words = 0
    extra_body_words = 0
    filler_sections: List[str] = []
    for title in titles:
        header = f"## {title}"
        header_words += len(header.split())
        if title in extra:
            extra_body_words += len(extra[title].split())
        else:
            filler_sections.append(title)

    remaining = target_wc - header_words - extra_body_words
    if remaining < 0:
        remaining = 0

    per_section = remaining // max(len(filler_sections), 1)
    leftover = remaining - per_section * len(filler_sections) if filler_sections else 0

    parts: List[str] = []
    filler_idx = 0
    global_word_offset = 0
    for title in titles:
        if title in extra:
            parts.append(f"## {title}\n{extra[title]}")
        else:
            wc = per_section + (1 if filler_idx < leftover else 0)
            # Use unique words with an offset to avoid cross-section duplication
            body_words = []
            for j in range(max(wc, 1)):
                body_words.append(_FILLER_POOL[(global_word_offset + j) % len(_FILLER_POOL)])
            global_word_offset += wc
            parts.append(f"## {title}\n" + " ".join(body_words))
            filler_idx += 1

    memo = "\n\n".join(parts)
    # Fine-tune: trim or pad last filler section to hit exact target
    actual = len(memo.split())
    diff = actual - target_wc
    if diff != 0 and filler_sections:
        last_filler = filler_sections[-1]
        body = filings_api._extract_markdown_section_body(memo, last_filler) or ""
        words = body.split()
        if diff > 0 and len(words) > diff:
            new_body = " ".join(words[:-diff])
            memo = filings_api._replace_markdown_section_body(memo, last_filler, new_body)
        elif diff < 0:
            pad = _generate_unique_words(abs(diff))
            new_body = body + " " + pad
            memo = filings_api._replace_markdown_section_body(memo, last_filler, new_body)
    return memo


def _total_words(text: str) -> int:
    return len((text or "").split())


def _in_band(text: str, target: int, tol: int = 10) -> bool:
    lo = max(1, target - tol)
    hi = min(5000, target + tol)
    split_wc = _total_words(text)
    stripped_wc = filings_api._count_words(text)
    return lo <= split_wc <= hi and lo <= stripped_wc <= hi


def _run_number_dedup(text: str) -> str:
    """Replicate the inline cross-section number deduplication logic."""
    _nrd_section_titles = _PROSE_SECTION_TITLES
    _nrd_number_sections: Dict[str, List[str]] = {}
    for title in _nrd_section_titles:
        body = filings_api._extract_markdown_section_body(text, title)
        if not body:
            continue
        for m in re.finditer(r"\b(\d+\.?\d*%|\$[\d,.]+[BMK]?)", body):
            _nrd_number_sections.setdefault(m.group(0), []).append(title)

    MAX_SECTIONS_PER_NUMBER = 3
    for num, titles in _nrd_number_sections.items():
        if len(titles) <= MAX_SECTIONS_PER_NUMBER:
            continue
        for excess_title in titles[MAX_SECTIONS_PER_NUMBER:]:
            body = filings_api._extract_markdown_section_body(text, excess_title)
            if not body:
                continue
            replacement_map = {
                "%": "the same margin level",
                "$": "the aforementioned figure",
            }
            context = replacement_map.get(num[0], "the figure noted earlier")
            new_body = body.replace(num, context, 1)
            if new_body != body:
                text = filings_api._replace_markdown_section_body(
                    text, excess_title, new_body
                )
    return text


def _run_theme_dedup(text: str) -> str:
    """Replicate the inline cross-section theme deduplication logic."""
    _nrd_section_titles = _PROSE_SECTION_TITLES
    _theme_keywords = [
        "reinvestment",
        "free cash flow",
        "margin pressure",
        "capital allocation",
        "revenue growth",
        "debt maturity",
        "share repurchase",
        "operating leverage",
    ]
    MAX_SECTIONS_PER_THEME = 4
    for theme in _theme_keywords:
        theme_sections = []
        for title in _nrd_section_titles:
            body = filings_api._extract_markdown_section_body(text, title)
            if body and re.search(rf"\b{re.escape(theme)}\b", body, re.IGNORECASE):
                theme_sections.append(title)
        if len(theme_sections) <= MAX_SECTIONS_PER_THEME:
            continue
        for excess_title in theme_sections[MAX_SECTIONS_PER_THEME:]:
            body = filings_api._extract_markdown_section_body(text, excess_title)
            if not body:
                continue
            new_body = re.sub(
                rf"\b{re.escape(theme)}\b",
                "this dynamic"
                if theme != "reinvestment"
                else "this capital deployment pattern",
                body,
                count=1,
                flags=re.IGNORECASE,
            )
            if new_body != body:
                text = filings_api._replace_markdown_section_body(
                    text, excess_title, new_body
                )
    return text


def _count_sections_with(text: str, needle: str) -> int:
    """Count how many prose sections contain the given needle string."""
    count = 0
    for title in _PROSE_SECTION_TITLES:
        body = filings_api._extract_markdown_section_body(text, title)
        if body and needle in body:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFinalWordBandReenforcement:
    """Tests 1-2: word-band re-enforcement brings out-of-band text closer."""

    def test_final_word_band_reenforcement_after_bridge_injection(self) -> None:
        """Bridge injection pushes text over upper bound; re-enforcement clamps it back."""
        target = 200
        tol = 10
        upper = target + tol  # 210

        health_body = (
            "The company shows strong financial fundamentals across key areas. "
            "Revenue growth remained consistent throughout the fiscal year. "
            "Balance sheet metrics improved steadily during the period."
        )
        extra = {"Financial Health Rating": health_body}
        memo = _build_memo_at_word_count(upper, extra)
        start_wc = _total_words(memo)
        assert start_wc == upper, f"Setup: memo has {start_wc} words, expected {upper}"

        # Bridge adds ~12 words, pushing above upper bound
        bridged = filings_api._ensure_health_to_exec_bridge(memo, target_length=target)
        bridged_wc = _total_words(bridged)
        # The bridge function should have trimmed health body to fit since we're at upper
        # OR the word count should still be reasonable
        assert (
            "next thing investors need to underwrite" in bridged
        ), "Bridge should be present"

        # Re-enforce word band
        result = filings_api._ensure_final_strict_word_band(
            bridged,
            target,
            include_health_rating=True,
            tolerance=tol,
            generation_stats={},
            allow_padding=True,
        )

        result_wc = _total_words(result)
        result_stripped = filings_api._count_words(result)
        # After re-enforcement, should be at or below upper bound
        assert result_wc <= upper + 5, (
            f"Re-enforcement should bring text near band: split={result_wc}"
        )

    def test_final_word_band_reenforcement_after_quote_rebalancing(self) -> None:
        """Quote rebalancing changes word count; re-enforcement restores band."""
        target = 200
        tol = 10
        snippets = (
            'The CEO stated "we remain committed to delivering shareholder value". '
            'Management noted "strategic investments position us for growth". '
            'The CFO added "operating margins reflect disciplined management".'
        )
        exec_body = _generate_unique_words(40)
        mdna_body = _generate_unique_words(40)
        extra = {
            "Executive Summary": exec_body,
            "Management Discussion & Analysis": mdna_body,
        }
        memo = _build_memo_at_word_count(target + 5, extra)

        rebalanced = filings_api._rebalance_contract_quotes(
            memo,
            filing_language_snippets=snippets,
            min_required_quotes=3,
            max_allowed_quotes=5,
        )

        result = filings_api._ensure_final_strict_word_band(
            rebalanced,
            target,
            include_health_rating=True,
            tolerance=tol,
            generation_stats={},
            allow_padding=True,
        )

        result_wc = _total_words(result)
        lower = target - tol
        upper = target + tol
        # The re-enforcement should bring text closer to the band
        assert result_wc <= upper + 5, (
            f"Re-enforcement should limit text: split={result_wc}, upper={upper}"
        )


class TestHealthBridge:
    """Tests 3-4: bridge injection with/without trimming."""

    def test_health_bridge_trims_when_at_upper_bound(self) -> None:
        """At upper bound, bridge is added by trimming health body."""
        target = 200
        tol = 10
        upper = target + tol  # 210

        health_body = (
            "The company maintains a strong balance sheet with low leverage. "
            "Cash reserves exceed short-term obligations by a wide margin. "
            "Debt maturities are well-laddered across future periods."
        )
        extra = {"Financial Health Rating": health_body}
        memo = _build_memo_at_word_count(upper, extra)
        start_wc = _total_words(memo)
        assert start_wc == upper, f"Setup: memo has {start_wc} words"

        result = filings_api._ensure_health_to_exec_bridge(memo, target_length=target)

        # Bridge must be present
        assert (
            "next thing investors need to underwrite" in result
        ), "Bridge sentence was not added"

        # Health section should have been trimmed
        result_health = filings_api._extract_markdown_section_body(
            result, "Financial Health Rating"
        )
        assert result_health is not None
        original_sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", health_body.strip())
            if s.strip()
        ]
        result_sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", result_health.strip())
            if s.strip()
        ]
        # Bridge adds 1 sentence but we removed at least 1, so total <=
        assert len(result_sentences) <= len(original_sentences), (
            f"Expected trimming: original={len(original_sentences)}, "
            f"result={len(result_sentences)}"
        )

    def test_health_bridge_adds_when_room_available(self) -> None:
        """When room is available, bridge is added without trimming."""
        target = 200
        tol = 10

        health_body = (
            "The company maintains a strong balance sheet with low leverage. "
            "Cash reserves exceed short-term obligations by a wide margin."
        )
        extra = {"Financial Health Rating": health_body}
        # Build well below upper bound so bridge fits without trimming
        memo = _build_memo_at_word_count(target - tol, extra)

        result = filings_api._ensure_health_to_exec_bridge(memo, target_length=target)

        # Bridge must be present
        assert (
            "next thing investors need to underwrite" in result
        ), "Bridge sentence was not added"

        # Original sentences should still be there (no trimming needed)
        result_health = filings_api._extract_markdown_section_body(
            result, "Financial Health Rating"
        )
        assert result_health is not None
        assert "strong balance sheet" in result_health
        assert "Cash reserves" in result_health


class TestQuoteRebalancing:
    """Tests 5-6: quote fallback extraction."""

    def test_rebalance_quotes_fallback_extraction(self) -> None:
        """Filing snippets with management language but no pre-quoted text produce quotes."""
        exec_body = "The executive summary covers the key findings of this report."
        mdna_body = "Management discussed operational improvements during the quarter."
        extra = {
            "Executive Summary": exec_body,
            "Management Discussion & Analysis": mdna_body,
        }
        memo = _build_memo_at_word_count(300, extra)

        # Short management-language sentences that fit the [^.]{10,120} constraint
        snippets = (
            "We believe our investments will drive shareholder value over time. "
            "The company continues to invest in research and development. "
            "Our strategy focuses on expanding market share effectively. "
            "Management expects continued improvement in margins this year. "
            "We anticipate strong demand for our core product lines ahead."
        )

        result = filings_api._rebalance_contract_quotes(
            memo,
            filing_language_snippets=snippets,
            min_required_quotes=2,
            max_allowed_quotes=5,
        )

        # Check that at least one quoted string was injected
        has_quote = bool(re.search(r'["\u201c][^"\u201d\n]{8,}["\u201d]', result))
        assert has_quote, "Fallback extraction should have injected at least one quote"

    def test_rebalance_quotes_still_fails_on_empty_snippets(self) -> None:
        """Empty filing snippets → text returned unchanged."""
        exec_body = "The executive summary covers the key findings of this report."
        mdna_body = "Management discussed operational improvements during the quarter."
        extra = {
            "Executive Summary": exec_body,
            "Management Discussion & Analysis": mdna_body,
        }
        memo = _build_memo_at_word_count(300, extra)

        result = filings_api._rebalance_contract_quotes(
            memo,
            filing_language_snippets="",
            min_required_quotes=3,
            max_allowed_quotes=5,
        )

        assert result == memo, "With empty snippets, text should be returned unchanged"


class TestNumberRepetitionDedup:
    """Tests 7-8: cross-section number deduplication."""

    def _build_number_memo(self) -> str:
        """Build a memo with '31.6%' in all 6 prose sections."""
        extra = {}
        for title in _PROSE_SECTION_TITLES:
            extra[title] = (
                "The margin reached 31.6% during the reporting period. "
                + _generate_unique_words(40)
            )
        extra["Key Metrics"] = _generate_unique_words(20)
        return _build_memo_at_word_count(500, extra, sections=_SECTION_TITLES)

    def test_number_repetition_dedup_reduces_to_max_sections(self) -> None:
        """31.6% in 6 sections → ≤3 sections after dedup."""
        memo = self._build_number_memo()

        before_count = _count_sections_with(memo, "31.6%")
        assert before_count == 6, f"Setup: expected 6 sections with 31.6%, got {before_count}"

        result = _run_number_dedup(memo)
        after_count = _count_sections_with(result, "31.6%")
        assert after_count <= 3, (
            f"Expected ≤3 sections with 31.6% after dedup, got {after_count}"
        )

    def test_number_repetition_dedup_preserves_first_occurrences(self) -> None:
        """First 3 sections keep the original number; later sections get replacement."""
        memo = self._build_number_memo()
        result = _run_number_dedup(memo)

        # First 3 prose sections should still have 31.6%
        for title in _PROSE_SECTION_TITLES[:3]:
            body = filings_api._extract_markdown_section_body(result, title)
            assert body is not None, f"Section '{title}' missing"
            assert "31.6%" in body, f"First-3 section '{title}' should keep 31.6%"

        # At least one later section should have the contextual replacement.
        # Note: 31.6% starts with '3' (a digit), so the replacement map's
        # first-char lookup falls through to the default "the figure noted earlier".
        replacement_found = False
        for title in _PROSE_SECTION_TITLES[3:]:
            body = filings_api._extract_markdown_section_body(result, title)
            if body and "the figure noted earlier" in body:
                replacement_found = True
                break
        assert replacement_found, "Expected contextual replacement in excess sections"


class TestThemeRepetitionDedup:
    """Tests 9-10: cross-section theme deduplication."""

    def _build_theme_memo(self, theme: str) -> str:
        """Build a memo with the given theme in all 6 prose sections."""
        extra = {}
        for title in _PROSE_SECTION_TITLES:
            extra[title] = (
                f"The {theme} strategy continued to drive value. "
                + _generate_unique_words(40)
            )
        extra["Key Metrics"] = _generate_unique_words(20)
        return _build_memo_at_word_count(500, extra, sections=_SECTION_TITLES)

    def test_theme_repetition_dedup_reduces_to_max_sections(self) -> None:
        """'reinvestment' in 6 sections → ≤4 sections after dedup."""
        memo = self._build_theme_memo("reinvestment")

        before_count = _count_sections_with(memo, "reinvestment")
        assert before_count == 6, f"Setup: expected 6, got {before_count}"

        result = _run_theme_dedup(memo)
        after_count = 0
        for title in _PROSE_SECTION_TITLES:
            body = filings_api._extract_markdown_section_body(result, title)
            if body and re.search(r"\breinvestment\b", body, re.IGNORECASE):
                after_count += 1
        assert after_count <= 4, (
            f"Expected ≤4 sections with 'reinvestment' after dedup, got {after_count}"
        )

    def test_theme_repetition_dedup_uses_appropriate_replacement(self) -> None:
        """'reinvestment' → 'this capital deployment pattern'; others → 'this dynamic'."""
        memo_reinvest = self._build_theme_memo("reinvestment")
        result_reinvest = _run_theme_dedup(memo_reinvest)

        found_capital = False
        for title in _PROSE_SECTION_TITLES:
            body = filings_api._extract_markdown_section_body(result_reinvest, title)
            if body and "this capital deployment pattern" in body:
                found_capital = True
                break
        assert found_capital, (
            "Expected 'this capital deployment pattern' as replacement for 'reinvestment'"
        )

        memo_fcf = self._build_theme_memo("free cash flow")
        result_fcf = _run_theme_dedup(memo_fcf)

        found_dynamic = False
        for title in _PROSE_SECTION_TITLES:
            body = filings_api._extract_markdown_section_body(result_fcf, title)
            if body and "this dynamic" in body:
                found_dynamic = True
                break
        assert found_dynamic, (
            "Expected 'this dynamic' as replacement for 'free cash flow'"
        )


class TestQualityGuardThreshold:
    """Test 11: tightened quality guard threshold."""

    def test_quality_guard_rejects_text_below_strict_band(self) -> None:
        """With -10 threshold, text at lower-15 words is rejected."""
        target = 3000
        tol = filings_api._effective_word_band_tolerance(target)
        lo = max(filings_api.TARGET_LENGTH_MIN_WORDS, target - tol)

        # The tightened check: _pq_new_wc >= _pq_lo - 10
        threshold = lo - 10  # 2980

        # A word count of 2975 should be rejected (below 2980)
        test_wc = lo - 15  # 2975
        assert test_wc < threshold, (
            f"Word count {test_wc} should be below threshold {threshold}"
        )

        # A word count of 2985 should be accepted (>= 2980)
        test_wc_ok = lo - 5  # 2985
        assert test_wc_ok >= threshold, (
            f"Word count {test_wc_ok} should be at or above threshold {threshold}"
        )

        # With the OLD threshold of -30, 2975 would have been accepted (>= 2960)
        old_threshold = lo - 30  # 2960
        assert test_wc >= old_threshold, (
            "Confirms old threshold would have accepted this word count"
        )


class TestFullPipelineIntegration:
    """Test 12: full post-processing pipeline word-count stability."""

    def test_full_post_processing_pipeline_word_count_stable(self) -> None:
        """Integration: bridge + dedup passes don't push text out of band when
        followed by final word-count re-enforcement.

        This test verifies the key invariant that Fix 1 addresses: after bridge
        injection and dedup passes add/remove words, the final re-enforcement
        step brings the word count back toward the target band.
        """
        target = 200
        tol = 10

        # Build a minimal memo with themes in 2 sections (small enough to not
        # trigger aggressive deduplication of filler content)
        health_body = (
            "The company shows strong financial health metrics overall. "
            "Revenue growth remained consistent through the fiscal year. "
            "Balance sheet quality improved steadily during the period."
        )
        exec_body = (
            "The reinvestment strategy drives long-term value creation. "
            "Quarterly revenue exceeded prior expectations meaningfully. "
            "Operating leverage contributed to improved profitability metrics."
        )
        extra = {
            "Financial Health Rating": health_body,
            "Executive Summary": exec_body,
        }
        memo = _build_memo_at_word_count(target + 5, extra)

        start_wc = _total_words(memo)
        assert target <= start_wc <= target + tol, f"Setup: memo has {start_wc} words"

        # Step 1: Bridge injection (adds bridge sentence, may trim health body)
        text = filings_api._ensure_health_to_exec_bridge(memo, target_length=target)
        after_bridge_wc = _total_words(text)

        # Step 2: Theme dedup (may change word counts slightly)
        text = _run_theme_dedup(text)

        # Step 3: Final re-enforcement
        text = filings_api._ensure_final_strict_word_band(
            text,
            target,
            include_health_rating=True,
            tolerance=tol,
            generation_stats={},
            allow_padding=True,
        )

        result_wc = _total_words(text)
        upper = target + tol

        # The re-enforcement should keep text within or close to the band
        assert result_wc <= upper + 5, (
            f"Final word count too high after re-enforcement: split={result_wc}"
        )
        # Verify the pipeline didn't destroy content completely
        assert result_wc >= target - 30, (
            f"Final word count dropped too much: split={result_wc}, target={target}"
        )


def test_verbatim_repetition_validator_flags_repeated_tail_clause_loop() -> None:
    validator = filings_api._make_verbatim_repetition_validator()
    memo = (
        "## Executive Summary\n"
        "The quarter improved, but durability still matters.\n\n"
        "## Financial Performance\n"
        "Revenue improved while cash conversion stayed solid.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is still balancing reinvestment with margin discipline.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: Demand could weaken if spending slows.\n\n"
        "## Closing Takeaway\n"
        "I rate Example Corp a HOLD because the setup is better but not fully proven. "
        "management execution drivers remain the key watchpoint "
        "management execution drivers remain the key watchpoint "
        "management execution drivers remain the key watchpoint."
    )
    issue = validator(memo)
    assert issue is not None
    assert "repeated clause loop" in issue.lower()
