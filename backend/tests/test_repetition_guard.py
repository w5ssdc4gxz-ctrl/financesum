"""Tests for repetition_guard.py — regression and unit tests."""

from __future__ import annotations

import pytest

from app.services.repetition_guard import (
    RepetitionReport,
    check_repetition,
    detect_duplicate_sentences,
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
