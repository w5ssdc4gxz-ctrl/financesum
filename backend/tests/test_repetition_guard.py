"""Tests for repetition_guard.py — regression and unit tests."""

from __future__ import annotations

import pytest

from app.services.repetition_guard import (
    RepetitionReport,
    check_repetition,
    detect_analyst_fog,
    detect_boilerplate_quotes,
    detect_cross_section_dollar_figures,
    detect_duplicate_sentences,
    detect_filler_phrases,
    find_garbled_sentences,
    detect_incoherent_endings,
    detect_repeated_ngrams,
    detect_repeated_trailing_phrases,
    detect_similar_paragraphs,
    strip_repeated_sentences,
)


# ---------------------------------------------------------------------------
# Regression: Alphabet Closing Takeaway repetition
# ---------------------------------------------------------------------------

def test_alphabet_closing_takeaway_repetition() -> None:
    """Regression: Closing Takeaway must not repeat 'Alphabet remains...' multiple times."""
    sample = (
        "## Closing Takeaway\n"
        "Alphabet remains well-positioned for sustained growth because search monetization "
        "still funds AI investment and cloud expansion. "
        "Alphabet remains well-positioned for sustained growth because search monetization "
        "still funds AI investment and cloud expansion. "
        "Alphabet remains well-positioned for sustained growth because search monetization "
        "still funds AI investment and cloud expansion."
    )
    report = check_repetition(sample)
    assert report.has_violations
    assert report.repeated_ngrams, "Expected repeated n-grams to be detected"
    assert "Closing Takeaway" in report.affected_sections


# ---------------------------------------------------------------------------
# detect_repeated_ngrams
# ---------------------------------------------------------------------------

class TestDetectRepeatedNgrams:
    def test_detects_repeated_eight_word_phrase(self) -> None:
        text = (
            "Cloud backlog expanded as enterprise demand stayed durable across geographies. "
            "Cloud backlog expanded as enterprise demand stayed durable across geographies. "
            "Cash flow remained healthy."
        )
        ngrams = detect_repeated_ngrams(text, n=8)
        assert any(
            "cloud backlog expanded as enterprise demand stayed durable" in gram
            for gram in ngrams
        )

    def test_detects_repeated_twelve_word_phrase(self) -> None:
        text = (
            "AI infrastructure spending rose because management accelerated data center "
            "deployment against visible demand. "
            "AI infrastructure spending rose because management accelerated data center "
            "deployment against visible demand. "
            "Margins stayed resilient."
        )
        ngrams = detect_repeated_ngrams(text, n=12)
        assert len(ngrams) > 0

    def test_detects_repeated_phrase(self) -> None:
        text = (
            "Revenue growth accelerated in the third quarter driven by cloud services. "
            "Revenue growth accelerated in the third quarter driven by cloud services. "
            "Margins held steady."
        )
        ngrams = detect_repeated_ngrams(text, n=10)
        assert len(ngrams) > 0

    def test_no_false_positive_on_unique_text(self) -> None:
        text = (
            "Revenue grew rapidly. Margins improved steadily. "
            "Capital allocation shifted toward buybacks. Debt declined."
        )
        ngrams = detect_repeated_ngrams(text, n=10)
        assert ngrams == []

    def test_stopword_only_ngrams_excluded(self) -> None:
        # A sentence padded with stopwords only — should not trigger
        text = "the and or but in on at to for the and or but in on at to for"
        ngrams = detect_repeated_ngrams(text, n=10)
        assert ngrams == []

    def test_short_text_returns_empty(self) -> None:
        ngrams = detect_repeated_ngrams("Short text.", n=10)
        assert ngrams == []

    def test_custom_n(self) -> None:
        text = "alpha beta gamma alpha beta gamma"
        ngrams = detect_repeated_ngrams(text, n=3)
        assert any("alpha beta gamma" in g for g in ngrams)

    def test_empty_text(self) -> None:
        assert detect_repeated_ngrams("") == []


# ---------------------------------------------------------------------------
# detect_duplicate_sentences
# ---------------------------------------------------------------------------

class TestDetectDuplicateSentences:
    def test_finds_exact_duplicate(self) -> None:
        text = (
            "Revenue grew strongly year over year. "
            "Margins remained healthy. "
            "Revenue grew strongly year over year."
        )
        dups = detect_duplicate_sentences(text)
        assert len(dups) >= 1
        assert any("Revenue grew strongly" in d for d in dups)

    def test_no_duplicate_in_unique_text(self) -> None:
        text = "First sentence here. Second sentence here. Third sentence here."
        dups = detect_duplicate_sentences(text)
        assert dups == []

    def test_near_exact_match_with_punctuation_diff(self) -> None:
        # Same words, different trailing punctuation → normalized match
        text = "Alphabet remains well-positioned. Some other text here. Alphabet remains well-positioned!"
        dups = detect_duplicate_sentences(text)
        assert len(dups) >= 1

    def test_empty_text(self) -> None:
        assert detect_duplicate_sentences("") == []

    def test_single_sentence(self) -> None:
        assert detect_duplicate_sentences("One sentence only.") == []


# ---------------------------------------------------------------------------
# detect_repeated_trailing_phrases
# ---------------------------------------------------------------------------

class TestDetectRepeatedTrailingPhrases:
    def test_detects_repeated_trailing(self) -> None:
        text = (
            "Revenue grew driven by cloud adoption.\n\n"
            "Margins held driven by cloud adoption.\n\n"
            "FCF expanded."
        )
        trailing = detect_repeated_trailing_phrases(text)
        assert len(trailing) > 0

    def test_no_false_positive(self) -> None:
        text = (
            "Revenue grew sharply in the quarter.\n\n"
            "Margins improved steadily last year.\n\n"
            "Cash flow expanded beyond expectations."
        )
        trailing = detect_repeated_trailing_phrases(text)
        assert trailing == []

    def test_normalizes_punctuation_variation(self) -> None:
        text = (
            "Revenue grew because enterprise customers renewed on time and expanded usage.\n\n"
            "Margins held because enterprise customers renewed on time and expanded usage!\n\n"
            "Liquidity remained strong."
        )
        trailing = detect_repeated_trailing_phrases(text)
        assert any(
            phrase.endswith("expanded usage")
            for phrase in trailing
        )

    def test_empty_text(self) -> None:
        assert detect_repeated_trailing_phrases("") == []


# ---------------------------------------------------------------------------
# check_repetition
# ---------------------------------------------------------------------------

class TestCheckRepetition:
    def test_clean_text_no_violations(self) -> None:
        text = (
            "Revenue grew sharply driven by cloud adoption. "
            "Margins improved as operating leverage materialized. "
            "Capital allocation shifted toward buybacks and dividends."
        )
        report = check_repetition(text)
        assert not report.has_violations
        assert report.duplicate_sentences == []

    def test_repeated_sentence_flags_violation(self) -> None:
        text = "Strong revenue growth. Strong revenue growth. Margins held steady."
        report = check_repetition(text)
        assert report.has_violations
        assert report.duplicate_sentences

    def test_report_is_repretition_report_instance(self) -> None:
        report = check_repetition("Some text.")
        assert isinstance(report, RepetitionReport)

    def test_report_has_violations_false_for_empty(self) -> None:
        report = check_repetition("")
        assert not report.has_violations

    def test_report_tracks_affected_sections_and_violation_types(self) -> None:
        text = (
            "## Executive Summary\n"
            "Cloud demand remained strong because enterprise renewals held and pricing stayed firm. "
            "Cloud demand remained strong because enterprise renewals held and pricing stayed firm.\n\n"
            "## Closing Takeaway\n"
            "Cloud demand remained strong because enterprise renewals held and pricing stayed firm. "
            "Management still needs to prove AI spend monetizes."
        )
        report = check_repetition(text)
        assert report.has_violations
        assert "repeated_ngrams" in report.violation_types
        assert "repeated_trailing_phrases" in report.violation_types
        assert "Executive Summary" in report.affected_sections
        assert "Closing Takeaway" in report.affected_sections


class TestDetectSimilarParagraphs:
    def test_detects_near_duplicate_paragraphs_across_sections(self) -> None:
        text = (
            "## Executive Summary\n"
            "Cloud backlog expanded, enterprise adoption accelerated, and margin discipline held "
            "despite higher AI infrastructure spending.\n\n"
            "## Closing Takeaway\n"
            "Cloud backlog expanded, enterprise adoption accelerated, and margin discipline held "
            "despite elevated AI infrastructure spending."
        )
        pairs = detect_similar_paragraphs(text, threshold=0.88)
        assert len(pairs) == 1
        assert pairs[0].section_a == "Executive Summary"
        assert pairs[0].section_b == "Closing Takeaway"

    def test_ignores_distinct_paragraphs(self) -> None:
        text = (
            "## Executive Summary\n"
            "Revenue growth accelerated on stronger ad demand while costs remained controlled.\n\n"
            "## Closing Takeaway\n"
            "Debt maturities are manageable, but refinancing terms still matter for liquidity."
        )
        assert detect_similar_paragraphs(text, threshold=0.88) == []


# ---------------------------------------------------------------------------
# strip_repeated_sentences
# ---------------------------------------------------------------------------

class TestStripRepeatedSentences:
    def test_removes_second_occurrence(self) -> None:
        text = "Alpha grew. Beta shrank. Alpha grew."
        result = strip_repeated_sentences(text)
        # "Alpha grew." should appear only once
        assert result.count("Alpha grew") == 1

    def test_preserves_first_occurrence(self) -> None:
        text = "First fact here. Second fact. First fact here."
        result = strip_repeated_sentences(text)
        assert "First fact here" in result

    def test_unique_text_unchanged(self) -> None:
        text = "One. Two. Three."
        result = strip_repeated_sentences(text)
        assert "One" in result
        assert "Two" in result
        assert "Three" in result

    def test_empty_returns_empty(self) -> None:
        assert strip_repeated_sentences("") == ""


# ---------------------------------------------------------------------------
# detect_cross_section_dollar_figures
# ---------------------------------------------------------------------------

class TestDetectCrossSectionDollarFigures:
    def test_flags_figure_in_three_plus_sections(self) -> None:
        text = (
            "## Financial Health Rating\n"
            "Automotive regulatory credits contributed $397 million in the quarter.\n\n"
            "## Executive Summary\n"
            "Credits were $397 million, supporting profitability.\n\n"
            "## Financial Performance\n"
            "The $397 million credit revenue propped up margins.\n\n"
            "## Closing Takeaway\n"
            "Cash flow improved independently of credits."
        )
        findings = detect_cross_section_dollar_figures(text)
        assert len(findings) >= 1
        assert any("397" in f.figure for f in findings)
        assert any(f.count >= 3 for f in findings)

    def test_no_flag_for_figure_in_two_sections(self) -> None:
        text = (
            "## Financial Health Rating\n"
            "Cash was $19.38 billion.\n\n"
            "## Executive Summary\n"
            "The $19.38 billion cash position provides runway.\n\n"
            "## Financial Performance\n"
            "Revenue grew 22% year over year."
        )
        findings = detect_cross_section_dollar_figures(text)
        assert len(findings) == 0

    def test_ignores_key_metrics_section(self) -> None:
        text = (
            "## Financial Health Rating\n"
            "Revenue was $10.74 billion.\n\n"
            "## Executive Summary\n"
            "The $10.74 billion topline grew.\n\n"
            "## Key Metrics\n"
            "Revenue: $10.74 billion\nOCF: $3.02 billion"
        )
        # Key Metrics doesn't count toward the threshold
        findings = detect_cross_section_dollar_figures(text)
        assert len(findings) == 0

    def test_empty_text(self) -> None:
        assert detect_cross_section_dollar_figures("") == []

    def test_regression_tesla_example(self) -> None:
        """Regression: Tesla summary had $397M, $4.218B, $969M each in 3+ sections."""
        text = (
            "## Financial Health Rating\n"
            "Credits contributed $397 million and inventory rose to $4.218 billion. "
            "Tesla expects to recognize $969 million of deferred revenue.\n\n"
            "## Executive Summary\n"
            "Credits still contributed $397 million. Inventory climbed to $4.218 billion. "
            "Deferred revenue of $969 million supports near-term visibility.\n\n"
            "## Financial Performance\n"
            "Automotive regulatory credits were $397 million. Inventory was $4.218 billion. "
            "The $969 million deferred revenue matters.\n\n"
            "## Management Discussion & Analysis\n"
            "Tesla expects $969 million of deferred revenue in the next 12 months."
        )
        findings = detect_cross_section_dollar_figures(text)
        figures = {f.figure for f in findings}
        # All three figures should be flagged
        assert any("397" in fig for fig in figures)
        assert any("4.218" in fig or "4,218" in fig for fig in figures)
        assert any("969" in fig for fig in figures)


# ---------------------------------------------------------------------------
# detect_incoherent_endings
# ---------------------------------------------------------------------------

class TestDetectIncoherentEndings:
    def test_flags_fragment_ending(self) -> None:
        text = (
            "## Financial Performance\n"
            "Revenue grew strongly. Margins held. inventory confirms. The better."
        )
        findings = detect_incoherent_endings(text)
        assert len(findings) >= 1
        assert any("Financial Performance" in f for f in findings)

    def test_flags_filler_ending(self) -> None:
        text = (
            "## Closing Takeaway\n"
            "The company performed well. Still matters."
        )
        findings = detect_incoherent_endings(text)
        assert len(findings) >= 1

    def test_flags_two_word_fragment(self) -> None:
        text = (
            "## Closing Takeaway\n"
            "Revenue grew strongly. Confirmed."
        )
        findings = detect_incoherent_endings(text)
        assert len(findings) >= 1

    def test_no_flag_for_clean_ending(self) -> None:
        text = (
            "## Executive Summary\n"
            "Revenue grew 22% year over year driven by cloud adoption. "
            "Management expects continued momentum through the next quarter."
        )
        findings = detect_incoherent_endings(text)
        assert len(findings) == 0

    def test_ignores_key_metrics(self) -> None:
        text = (
            "## Key Metrics\n"
            "Revenue: $10.74B"
        )
        findings = detect_incoherent_endings(text)
        assert len(findings) == 0

    def test_empty_text(self) -> None:
        assert detect_incoherent_endings("") == []


# ---------------------------------------------------------------------------
# detect_filler_phrases (new patterns)
# ---------------------------------------------------------------------------

class TestDetectFillerPhrasesNewPatterns:
    def test_detects_still_anchors_pattern(self) -> None:
        text = "Tesla still anchors how much balance-sheet pressure this company can absorb."
        found = detect_filler_phrases(text)
        assert any("still anchors how much" in f for f in found)

    def test_detects_cleanest_thread_pattern(self) -> None:
        text = "That leaves Tesla as the cleanest thread tying the story together."
        found = detect_filler_phrases(text)
        assert any("cleanest thread tying the story together" in f for f in found)

    def test_detects_proof_point_pattern(self) -> None:
        text = "Tesla remains the company-specific proof point behind the thesis."
        found = detect_filler_phrases(text)
        assert any("company-specific proof point behind the thesis" in f for f in found)

    def test_detects_key_issue_for_section(self) -> None:
        text = "This is important, which is the key issue for the Executive Summary."
        found = detect_filler_phrases(text)
        assert any("key issue for the" in f.lower() for f in found)

    def test_detects_inventory_confirms(self) -> None:
        text = "Inventory confirms. The better."
        found = detect_filler_phrases(text)
        assert any("inventory confirms" in f.lower() for f in found) or \
               any("the better" in f.lower() for f in found)

    def test_detects_management_sequence_and_rating_depends_patterns(self) -> None:
        text = (
            "The rating still depends on whether Microsoft keeps supporting financial resilience. "
            "Execution credibility now depends on how leadership sequences Investments."
        )
        found = detect_filler_phrases(text)
        assert any("rating still depends on whether" in f.lower() for f in found)
        assert any("leadership sequences investments" in f.lower() for f in found)
        assert len(find_garbled_sentences(text)) == 2


# ---------------------------------------------------------------------------
# check_repetition integration with new detections
# ---------------------------------------------------------------------------

class TestCheckRepetitionNewViolations:
    def test_cross_section_dollars_in_report(self) -> None:
        text = (
            "## Financial Health Rating\n"
            "Credits contributed $397 million.\n\n"
            "## Executive Summary\n"
            "Credits were $397 million.\n\n"
            "## Financial Performance\n"
            "The $397 million credit revenue.\n\n"
            "## Closing Takeaway\n"
            "Cash improved."
        )
        report = check_repetition(text)
        assert report.has_violations
        assert "cross_section_dollar_figures" in report.violation_types
        assert report.cross_section_dollar_figures

    def test_incoherent_endings_in_report(self) -> None:
        text = (
            "## Financial Performance\n"
            "Revenue grew. Margins held. inventory confirms. The better."
        )
        report = check_repetition(text)
        assert report.has_violations
        assert "incoherent_endings" in report.violation_types

    def test_lower_ngram_floor_catches_five_word_repeats(self) -> None:
        text = (
            "automotive regulatory credits contributed significantly to margins. "
            "Other factors mattered too. "
            "automotive regulatory credits contributed significantly to profits."
        )
        report = check_repetition(text)
        assert report.has_violations
        assert "repeated_ngrams" in report.violation_types


# ---------------------------------------------------------------------------
# detect_analyst_fog
# ---------------------------------------------------------------------------

class TestDetectAnalystFog:
    def test_detects_underwriting_thread(self) -> None:
        text = "The underwriting thread still depends on cloud demand."
        found = detect_analyst_fog(text)
        assert any("underwriting thread" in f for f in found)

    def test_detects_capital_absorption(self) -> None:
        text = "Capital absorption is rising faster than revenue growth."
        found = detect_analyst_fog(text)
        assert any("capital absorption" in f for f in found)

    def test_detects_forward_visibility(self) -> None:
        text = "Forward visibility constraints limit the investment case."
        found = detect_analyst_fog(text)
        assert any("forward visibility" in f for f in found)

    def test_no_false_positive_on_clean_text(self) -> None:
        text = "Revenue grew 15% because cloud demand was strong."
        found = detect_analyst_fog(text)
        assert found == []

    def test_detects_prompt_instruction_leaks(self) -> None:
        text = "The golden thread of this analysis is margin compression."
        found = detect_analyst_fog(text)
        assert any("golden thread" in f for f in found)

    def test_empty_text(self) -> None:
        assert detect_analyst_fog("") == []

    def test_case_insensitive(self) -> None:
        text = "The UNDERWRITING THREAD depends on demand."
        found = detect_analyst_fog(text)
        assert any("underwriting thread" in f for f in found)


# ---------------------------------------------------------------------------
# detect_boilerplate_quotes
# ---------------------------------------------------------------------------

class TestDetectBoilerplateQuotes:
    def test_detects_investment_classification_quote(self) -> None:
        text = (
            'Management noted that "investments with maturities beyond one year '
            'may be classified as short-term based on their highly liquid nature."'
        )
        found = detect_boilerplate_quotes(text)
        assert len(found) >= 1

    def test_detects_forward_looking_disclaimer(self) -> None:
        text = (
            'The filing states that "forward-looking statements involve '
            'risks and uncertainties that could cause results to differ."'
        )
        found = detect_boilerplate_quotes(text)
        assert len(found) >= 1

    def test_ignores_high_signal_quote(self) -> None:
        text = (
            'Management noted that "we expect AI infrastructure spending to '
            'accelerate through the second half of the fiscal year."'
        )
        found = detect_boilerplate_quotes(text)
        assert found == []

    def test_detects_gaap_boilerplate(self) -> None:
        text = (
            'The company states "in accordance with generally accepted '
            'accounting principles these values are recorded at fair market value."'
        )
        found = detect_boilerplate_quotes(text)
        assert len(found) >= 1

    def test_empty_text(self) -> None:
        assert detect_boilerplate_quotes("") == []


# ---------------------------------------------------------------------------
# check_repetition integration — analyst fog and boilerplate quotes
# ---------------------------------------------------------------------------

class TestCheckRepetitionAnalystFogIntegration:
    def test_analyst_fog_in_report(self) -> None:
        text = (
            "## Executive Summary\n"
            "The underwriting thread depends on cloud demand. "
            "Capital absorption is rising faster than revenue.\n\n"
            "## Closing Takeaway\n"
            "Forward visibility constraints limit the case."
        )
        report = check_repetition(text)
        assert report.has_violations
        assert "analyst_fog" in report.violation_types
        assert report.analyst_fog_phrases

    def test_boilerplate_quotes_in_report(self) -> None:
        text = (
            "## Executive Summary\n"
            'Management noted "investments with maturities beyond one year '
            'may be classified as short-term based on their highly liquid nature '
            'and because such securities represent the investment of cash."'
        )
        report = check_repetition(text)
        assert report.has_violations
        assert "boilerplate_quotes" in report.violation_types
        assert report.boilerplate_quotes

    def test_clean_text_no_fog_or_boilerplate(self) -> None:
        text = (
            "## Executive Summary\n"
            "Revenue grew 15% because cloud demand was strong. "
            "The company is investing in AI infrastructure."
        )
        report = check_repetition(text)
        assert "analyst_fog" not in report.violation_types
        assert "boilerplate_quotes" not in report.violation_types
