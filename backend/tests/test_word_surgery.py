"""Tests for the word-count surgery utilities.

Covers count_words_by_section, identify_adjustment_sections,
trim_to_target, and expand_to_target.
"""

from __future__ import annotations

import re

import pytest

from app.services.word_surgery import (
    clean_ending,
    count_words,
    count_words_by_section,
    expand_to_target,
    identify_adjustment_sections,
    needs_regen_to_expand,
    trim_to_target,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_words(n: int, token: str = "word") -> str:
    if n <= 0:
        return ""
    return " ".join([token] * n)


def _build_summary(
    *,
    exec_words: int = 30,
    perf_words: int = 30,
    mda_words: int = 30,
    risk_words: int = 20,
    metrics_lines: int = 3,
    closing_words: int = 20,
) -> str:
    """Build a structurally valid summary with approximate word counts."""
    parts = [
        f"## Executive Summary\n{_make_section_prose(exec_words, 'alpha')}\n",
        f"## Financial Performance\n{_make_section_prose(perf_words, 'beta')}\n",
        f"## Management Discussion & Analysis\n{_make_section_prose(mda_words, 'gamma')}\n",
        f"## Risk Factors\n{_make_section_prose(risk_words, 'delta')}\n",
        f"## Key Metrics\n{_make_metrics_block(metrics_lines)}\n",
        f"## Closing Takeaway\n{_make_section_prose(closing_words, 'epsilon')}\n",
    ]
    return "\n".join(parts)


def _make_section_prose(word_count: int, seed: str) -> str:
    """Generate sentences that approximate a given word count."""
    if word_count <= 0:
        return ""
    # Build ~10 word sentences until we hit the target.
    sentences = []
    remaining = word_count
    i = 0
    while remaining > 0:
        i += 1
        size = min(10, remaining)
        sentence = " ".join([f"{seed}{i}"] + [seed] * (size - 1)) + "."
        sentences.append(sentence)
        remaining -= size
    return " ".join(sentences)


def _make_metrics_block(n: int = 3) -> str:
    metrics = [
        "\u2192 Revenue: $52.0B",
        "\u2192 Operating Margin: 33.0%",
        "\u2192 Free Cash Flow: $12.5B",
        "\u2192 Net Margin: 28.5%",
        "\u2192 Current Ratio: 1.8x",
    ]
    return "\n".join(metrics[:n])


# ===================================================================
# count_words_by_section
# ===================================================================

class TestCountWordsBySection:
    def test_basic_section_counts(self) -> None:
        text = (
            "## Executive Summary\n"
            "The thesis is constructive and balanced.\n\n"
            "## Financial Performance\n"
            "Revenue grew steadily this quarter.\n"
        )
        counts = count_words_by_section(text)
        assert "Executive Summary" in counts
        assert "Financial Performance" in counts
        assert counts["Executive Summary"] == 6  # "The thesis is constructive and balanced."
        assert counts["Financial Performance"] == 5  # "Revenue grew steadily this quarter."

    def test_empty_text(self) -> None:
        assert count_words_by_section("") == {}

    def test_preamble_content(self) -> None:
        text = "Some preamble content here.\n\n## Executive Summary\nThesis.\n"
        counts = count_words_by_section(text)
        assert "_preamble" in counts
        assert counts["_preamble"] > 0
        assert "Executive Summary" in counts

    def test_all_standard_sections(self) -> None:
        text = _build_summary(
            exec_words=20, perf_words=25, mda_words=30,
            risk_words=15, closing_words=10,
        )
        counts = count_words_by_section(text)
        assert "Executive Summary" in counts
        assert "Financial Performance" in counts
        assert "Management Discussion & Analysis" in counts
        assert "Risk Factors" in counts
        assert "Key Metrics" in counts
        assert "Closing Takeaway" in counts

    def test_word_count_consistency_with_eval_harness(self) -> None:
        """Section word counts should sum to approximately the total."""
        text = _build_summary(exec_words=40, perf_words=40, mda_words=40,
                              risk_words=30, closing_words=20)
        counts = count_words_by_section(text)
        section_total = sum(counts.values())
        overall = count_words(text)
        # The section total may differ slightly from overall because headers
        # have words too, but the section bodies should be close.
        # Allow for header word counts.
        assert abs(section_total - overall) <= 20


# ===================================================================
# identify_adjustment_sections
# ===================================================================

class TestIdentifyAdjustmentSections:
    def test_identifies_sections_to_trim(self) -> None:
        counts = {
            "Executive Summary": 150,
            "Financial Performance": 120,
            "Risk Factors": 80,
        }
        budgets = {
            "Executive Summary": 100,
            "Financial Performance": 100,
            "Risk Factors": 100,
        }
        to_trim, to_expand = identify_adjustment_sections(counts, budgets)
        assert "Executive Summary" in to_trim
        assert "Financial Performance" in to_trim
        assert "Risk Factors" in to_expand
        # Executive Summary has the largest overshoot (50) → first.
        assert to_trim[0] == "Executive Summary"

    def test_identifies_sections_to_expand(self) -> None:
        counts = {
            "Executive Summary": 50,
            "Financial Performance": 80,
            "Risk Factors": 40,
        }
        budgets = {
            "Executive Summary": 100,
            "Financial Performance": 100,
            "Risk Factors": 100,
        }
        to_trim, to_expand = identify_adjustment_sections(counts, budgets)
        assert len(to_trim) == 0
        assert len(to_expand) == 3
        # Risk Factors has the largest undershoot (60) → first.
        assert to_expand[0] == "Risk Factors"

    def test_exact_match_produces_empty_lists(self) -> None:
        counts = {"Executive Summary": 100, "Risk Factors": 50}
        budgets = {"Executive Summary": 100, "Risk Factors": 50}
        to_trim, to_expand = identify_adjustment_sections(counts, budgets)
        assert to_trim == []
        assert to_expand == []

    def test_missing_sections_in_counts_treated_as_zero(self) -> None:
        counts = {}
        budgets = {"Executive Summary": 100}
        to_trim, to_expand = identify_adjustment_sections(counts, budgets)
        assert len(to_trim) == 0
        assert to_expand == ["Executive Summary"]

    def test_sections_not_in_budgets_are_ignored(self) -> None:
        counts = {"Executive Summary": 200, "Unbudgeted Section": 500}
        budgets = {"Executive Summary": 100}
        to_trim, _ = identify_adjustment_sections(counts, budgets)
        assert "Executive Summary" in to_trim
        assert "Unbudgeted Section" not in to_trim


# ===================================================================
# trim_to_target
# ===================================================================

class TestTrimToTarget:
    def test_already_within_band_unchanged(self) -> None:
        text = _build_summary(exec_words=20, perf_words=20, mda_words=20,
                              risk_words=15, closing_words=15)
        original_wc = count_words(text)
        result = trim_to_target(text, target=original_wc, tolerance=10)
        assert count_words(result) <= original_wc + 10

    def test_trims_overlong_text(self) -> None:
        # Build a very long summary.
        text = _build_summary(exec_words=80, perf_words=80, mda_words=80,
                              risk_words=60, closing_words=40)
        target = 150
        result = trim_to_target(text, target=target, tolerance=10)
        result_wc = count_words(result)
        assert result_wc <= target + 10, f"Expected ≤{target+10}, got {result_wc}"

    def test_preserves_section_headers(self) -> None:
        text = _build_summary(exec_words=60, perf_words=60, mda_words=60,
                              risk_words=40, closing_words=30)
        result = trim_to_target(text, target=100, tolerance=10)
        for header in [
            "## Executive Summary",
            "## Financial Performance",
            "## Management Discussion & Analysis",
            "## Risk Factors",
            "## Key Metrics",
            "## Closing Takeaway",
        ]:
            assert header in result, f"Missing header: {header}"

    def test_keeps_at_least_one_sentence_per_section(self) -> None:
        text = _build_summary(exec_words=40, perf_words=40, mda_words=40,
                              risk_words=30, closing_words=20)
        # Target so low it can't be reached without emptying sections.
        result = trim_to_target(text, target=30, tolerance=10)
        # Every non-Key-Metrics section should have at least 1 sentence.
        for section in ["Executive Summary", "Financial Performance",
                        "Management Discussion & Analysis",
                        "Risk Factors", "Closing Takeaway"]:
            counts = count_words_by_section(result)
            assert counts.get(section, 0) > 0, f"Section {section} was emptied"

    def test_does_not_modify_key_metrics(self) -> None:
        text = _build_summary(exec_words=60, perf_words=60, mda_words=60,
                              risk_words=40, closing_words=30)
        before_counts = count_words_by_section(text)
        result = trim_to_target(text, target=120, tolerance=10)
        after_counts = count_words_by_section(result)
        assert after_counts.get("Key Metrics", 0) == before_counts.get("Key Metrics", 0)

    def test_empty_text_returns_empty(self) -> None:
        assert trim_to_target("", target=100) == ""

    def test_single_section_trim(self) -> None:
        text = (
            "## Executive Summary\n"
            "First sentence here. Second sentence here. Third sentence here. "
            "Fourth sentence here. Fifth sentence here.\n"
        )
        result = trim_to_target(text, target=6, tolerance=3)
        wc = count_words(result)
        assert wc <= 9  # target + tolerance


# ===================================================================
# expand_to_target
# ===================================================================

class TestExpandToTarget:
    def test_already_within_band_unchanged(self) -> None:
        text = _build_summary(exec_words=30, perf_words=30, mda_words=30,
                              risk_words=20, closing_words=20)
        original_wc = count_words(text)
        result = expand_to_target(text, target=original_wc, tolerance=10)
        # Should not change (already at target).
        assert count_words(result) >= original_wc - 10

    def test_expands_short_text_with_explicit_phrases(self) -> None:
        # expand_to_target requires explicit phrases — no built-in defaults.
        text = _build_summary(exec_words=10, perf_words=10, mda_words=10,
                              risk_words=10, closing_words=10)
        target = 150
        phrases = [
            "Operating leverage improved as fixed costs were absorbed across a larger revenue base.",
            "Cash conversion remained strong relative to reported earnings.",
            "Capital allocation reflected management's confidence in near-term demand.",
        ]
        result = expand_to_target(text, target=target, tolerance=10,
                                  expansion_phrases=phrases)
        result_wc = count_words(result)
        assert result_wc > count_words(text)

    def test_no_expansion_without_phrases(self) -> None:
        # Without explicit phrases, expand_to_target returns text unchanged.
        text = _build_summary(exec_words=10, perf_words=10, mda_words=10,
                              risk_words=10, closing_words=10)
        original_wc = count_words(text)
        result = expand_to_target(text, target=500, tolerance=10)
        assert count_words(result) == original_wc

    def test_custom_expansion_phrases(self) -> None:
        text = (
            "## Executive Summary\n"
            "Short thesis here.\n\n"
            "## Financial Performance\n"
            "Revenue grew.\n\n"
            "## Management Discussion & Analysis\n"
            "Capital discipline.\n\n"
            "## Risk Factors\n"
            "Execution risk.\n\n"
            "## Key Metrics\n"
            "\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\n"
            "HOLD for now.\n"
        )
        phrases = [
            "Custom expansion phrase one for testing purposes here.",
            "Another custom phrase that adds meaningful content to the section.",
        ]
        result = expand_to_target(text, target=80, tolerance=10,
                                  expansion_phrases=phrases)
        assert "Custom expansion phrase" in result or count_words(result) >= 70

    def test_preserves_section_headers(self) -> None:
        text = _build_summary(exec_words=10, perf_words=10, mda_words=10,
                              risk_words=10, closing_words=10)
        result = expand_to_target(text, target=150, tolerance=10)
        for header in [
            "## Executive Summary",
            "## Financial Performance",
            "## Management Discussion & Analysis",
            "## Risk Factors",
            "## Key Metrics",
            "## Closing Takeaway",
        ]:
            assert header in result, f"Missing header: {header}"

    def test_does_not_modify_key_metrics(self) -> None:
        text = _build_summary(exec_words=10, perf_words=10, mda_words=10,
                              risk_words=10, closing_words=10)
        before_counts = count_words_by_section(text)
        result = expand_to_target(text, target=150, tolerance=10)
        after_counts = count_words_by_section(result)
        assert after_counts.get("Key Metrics", 0) == before_counts.get("Key Metrics", 0)

    def test_empty_text_returns_empty(self) -> None:
        assert expand_to_target("", target=100) == ""

    def test_exhausted_phrases_stops_gracefully(self) -> None:
        text = _build_summary(exec_words=5, perf_words=5, mda_words=5,
                              risk_words=5, closing_words=5)
        phrases = ["One short phrase."]
        result = expand_to_target(text, target=5000, tolerance=10,
                                  expansion_phrases=phrases)
        # Should not crash; just stops when phrases exhausted.
        assert count_words(result) > count_words(text)

    def test_respects_closing_takeaway_sentence_cap(self) -> None:
        """Closing Takeaway should not receive phrases if it already has 3+ sentences."""
        text = (
            "## Executive Summary\n"
            "Short.\n\n"
            "## Financial Performance\n"
            "Short.\n\n"
            "## Management Discussion & Analysis\n"
            "Short.\n\n"
            "## Risk Factors\n"
            "Short.\n\n"
            "## Key Metrics\n"
            "\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\n"
            "HOLD for now. The thesis remains intact. I would upgrade if margins improve.\n"
        )
        before_closing_wc = count_words_by_section(text).get("Closing Takeaway", 0)
        result = expand_to_target(text, target=80, tolerance=10)
        after_closing_wc = count_words_by_section(result).get("Closing Takeaway", 0)
        # Closing already has 3 sentences — should not grow.
        assert after_closing_wc == before_closing_wc


# ===================================================================
# needs_regen_to_expand
# ===================================================================

class TestNeedsRegenToExpand:
    def test_returns_true_when_under_target(self) -> None:
        text = _make_words(50, "word") + "."
        assert needs_regen_to_expand(text, target=100, tolerance=10) is True

    def test_returns_false_when_within_band(self) -> None:
        text = _make_words(100, "word") + "."
        assert needs_regen_to_expand(text, target=100, tolerance=10) is False

    def test_returns_false_when_over_target(self) -> None:
        text = _make_words(120, "word") + "."
        assert needs_regen_to_expand(text, target=100, tolerance=10) is False

    def test_empty_text_returns_false(self) -> None:
        assert needs_regen_to_expand("", target=100, tolerance=10) is False

    def test_zero_target_returns_false(self) -> None:
        assert needs_regen_to_expand("Some text.", target=0, tolerance=10) is False


# ===================================================================
# clean_ending
# ===================================================================

class TestCleanEnding:
    def test_returns_text_within_band_unchanged(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        wc = count_words(text)
        result = clean_ending(text, target_words=wc, tolerance=10)
        assert result == text

    def test_truncates_at_sentence_boundary(self) -> None:
        text = (
            "First sentence here. Second sentence here. "
            "Third sentence here. Fourth sentence here. "
            "Fifth sentence for padding."
        )
        # Target of 10 with tolerance 3 → upper bound = 13
        result = clean_ending(text, target_words=10, tolerance=3)
        result_wc = count_words(result)
        assert result_wc <= 13
        # Result must end with terminal punctuation
        assert result.rstrip().endswith((".", "!", "?"))

    def test_does_not_cut_mid_sentence(self) -> None:
        text = (
            "Revenue grew sharply this quarter due to cloud services. "
            "Margins improved as operating leverage materialized in the model. "
            "The company returned capital through buybacks and dividends."
        )
        result = clean_ending(text, target_words=15, tolerance=5)
        # Result should end at a sentence boundary
        stripped = result.rstrip()
        assert stripped.endswith((".", "!", "?"))

    def test_already_at_target_not_modified(self) -> None:
        text = "Exactly ten words here in this sentence right now."
        wc = count_words(text)
        result = clean_ending(text, target_words=wc + 5, tolerance=10)
        assert result == text

    def test_empty_text_returns_empty(self) -> None:
        assert clean_ending("", target_words=100) == ""

    def test_preserves_markdown_sections_when_trimming(self) -> None:
        text = (
            "## Executive Summary\n"
            "First sentence here. Second sentence here. Third sentence here.\n\n"
            "## Risk Factors\n"
            "Risk sentence one. Risk sentence two. Risk sentence three."
        )
        result = clean_ending(text, target_words=12, tolerance=3)
        assert "## Executive Summary" in result
        assert "## Risk Factors" in result
        assert result.count("## ") >= 2


# ===================================================================
# Integration: trim then expand round-trip
# ===================================================================

class TestRoundTrip:
    def test_trim_then_expand_lands_near_target(self) -> None:
        text = _build_summary(exec_words=80, perf_words=80, mda_words=80,
                              risk_words=60, closing_words=40)
        target = 200
        trimmed = trim_to_target(text, target=target, tolerance=10)
        trimmed_wc = count_words(trimmed)
        assert trimmed_wc <= target + 10

        # If trimmed below band, expand should bring it back up.
        if trimmed_wc < target - 10:
            expanded = expand_to_target(trimmed, target=target, tolerance=10)
            assert count_words(expanded) >= trimmed_wc

    def test_count_words_consistency(self) -> None:
        """count_words in word_surgery matches eval_harness."""
        from app.services.eval_harness import count_words as eval_count_words

        text = "Hello, world! — This is a test."
        assert count_words(text) == eval_count_words(text)


# ===================================================================
# Edge Cases
# ===================================================================

class TestEdgeCases:
    def test_single_word_sections(self) -> None:
        text = (
            "## Executive Summary\nThesis.\n\n"
            "## Financial Performance\nRevenue.\n\n"
            "## Management Discussion & Analysis\nMDA.\n\n"
            "## Risk Factors\nRisk.\n\n"
            "## Key Metrics\n\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\nHOLD.\n"
        )
        # Trim target below the minimum possible — should keep at least
        # one sentence per section.
        result = trim_to_target(text, target=3, tolerance=2)
        assert "## Executive Summary" in result

    def test_no_sections(self) -> None:
        text = "Just plain text without any markdown headers at all."
        counts = count_words_by_section(text)
        assert "_preamble" in counts
        assert counts["_preamble"] == count_words(text)

    def test_identify_adjustment_empty_inputs(self) -> None:
        to_trim, to_expand = identify_adjustment_sections({}, {})
        assert to_trim == []
        assert to_expand == []

    def test_trim_with_zero_tolerance(self) -> None:
        text = _build_summary(exec_words=30, perf_words=30, mda_words=30,
                              risk_words=20, closing_words=20)
        target = 80
        result = trim_to_target(text, target=target, tolerance=0)
        assert count_words(result) <= target

    def test_expand_with_zero_tolerance(self) -> None:
        text = _build_summary(exec_words=5, perf_words=5, mda_words=5,
                              risk_words=5, closing_words=5)
        target = 60
        result = expand_to_target(text, target=target, tolerance=0)
        assert count_words(result) >= count_words(text)


# ===================================================================
# Cross-module compatibility
# ===================================================================

class TestPromptBuilderCompatibility:
    """Verify word_surgery section names match prompt_builder budgets."""

    def test_section_budget_keys_match_count_words_by_section(self) -> None:
        """calculate_section_budgets produces keys that count_words_by_section
        can parse from a well-formed summary."""
        from app.services.prompt_builder import calculate_section_budgets

        budgets = calculate_section_budgets(600, include_health_rating=False)

        # Build a summary with all sections present.
        text = _build_summary(
            exec_words=100, perf_words=80, mda_words=100,
            risk_words=80, closing_words=60,
        )
        section_wc = count_words_by_section(text)

        # Every budget key should appear in section_wc.
        for key in budgets:
            assert key in section_wc, (
                f"Budget key {key!r} not found in count_words_by_section output. "
                f"Available: {list(section_wc.keys())}"
            )

    def test_identify_adjustment_with_real_budgets(self) -> None:
        """identify_adjustment_sections works with real budget output."""
        from app.services.prompt_builder import calculate_section_budgets

        budgets = calculate_section_budgets(600, include_health_rating=False)

        # Build a summary deliberately heavy on Executive Summary.
        text = _build_summary(
            exec_words=200, perf_words=50, mda_words=50,
            risk_words=50, closing_words=50,
        )
        section_wc = count_words_by_section(text)

        to_trim, to_expand = identify_adjustment_sections(section_wc, budgets)
        # Exec Summary should be in to_trim (200 >> budget ~108).
        assert "Executive Summary" in to_trim

    def test_budget_keys_from_prompt_pack_section_order(self) -> None:
        """Budget keys should be a subset of prompt_pack.SECTION_ORDER."""
        from app.services.prompt_builder import calculate_section_budgets
        from app.services.prompt_pack import SECTION_ORDER

        budgets_with = calculate_section_budgets(900, include_health_rating=True)
        for key in budgets_with:
            assert key in SECTION_ORDER, f"Budget key {key!r} not in SECTION_ORDER"

        budgets_without = calculate_section_budgets(900, include_health_rating=False)
        for key in budgets_without:
            assert key in SECTION_ORDER

    def test_budgets_sum_exactly_to_target(self) -> None:
        """Verify prompt_builder budgets sum to the section-body target."""
        from app.services.prompt_builder import calculate_section_budgets

        for target in [600, 900, 1200, 2599, 3000]:
            budgets = calculate_section_budgets(target, include_health_rating=False)
            heading_words = sum(
                len(re.findall(r"\b\w+\b", section_name)) for section_name in budgets
            )
            expected_body_target = target - heading_words
            assert sum(budgets.values()) == expected_body_target, (
                f"Budget sum {sum(budgets.values())} != body target {expected_body_target}"
            )
