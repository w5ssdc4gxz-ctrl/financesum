"""Tests for the evaluation harness.

Covers word-count enforcement, repetition detection, numeric density,
narrative flow scoring, quote validation, section completeness,
boilerplate detection, cost tracking, and full-pipeline integration.
"""

from __future__ import annotations

import pytest

from app.services.eval_harness import (
    EvalReport,
    EvalResult,
    PipelineCostTracker,
    StageCost,
    check_boilerplate,
    check_flow_score,
    check_numeric_density,
    check_quotes,
    check_repetition,
    check_section_completeness,
    check_word_count,
    count_words,
    evaluate_summary,
    extract_section_body,
)
from tests.fixtures.industry_samples import (
    ALL_FIXTURES,
    build_valid_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_words(n: int, token: str = "word") -> str:
    if n <= 0:
        return ""
    return " ".join([token] * n)


def _build_minimal_summary(word_target: int) -> str:
    """Build a minimal but structurally complete summary near *word_target* words.

    Distributes words across all required sections so section-completeness
    and word-count checks can both pass.
    """
    # Fixed structural overhead (headings + short mandatory content).
    sections = [
        ("Executive Summary", 0.20),
        ("Financial Performance", 0.20),
        ("Management Discussion & Analysis", 0.20),
        ("Risk Factors", 0.15),
        ("Key Metrics", 0.10),
        ("Closing Takeaway", 0.15),
    ]

    lines: list[str] = []
    running = 0

    for idx, (title, pct) in enumerate(sections):
        budget = int(word_target * pct)
        if idx == len(sections) - 1:
            budget = word_target - running

        lines.append(f"## {title}")

        if title == "Key Metrics":
            body = (
                "\u2192 Revenue: $52.0B\n"
                "\u2192 Operating Margin: 33.0%\n"
                "\u2192 Free Cash Flow: $12.5B"
            )
        elif title == "Closing Takeaway":
            body = (
                "HOLD remains appropriate while execution stabilizes. "
                "I would upgrade to BUY if operating margin is above 35 percent over the next two quarters."
            )
            filler = max(0, budget - len(body.split()))
            if filler:
                body += " " + _make_words(filler, "balanced")
        elif title == "Risk Factors":
            body = (
                "**Execution Risk**: If reinvestment outpaces demand, margins "
                "can compress before cost actions catch up."
            )
            filler = max(0, budget - len(body.split()))
            if filler:
                body += " " + _make_words(filler, "steady")
        else:
            body = (
                "The setup is constructive and execution quality is "
                "stabilizing across pricing and reinvestment choices."
            )
            filler = max(0, budget - len(body.split()))
            if filler:
                body += " " + _make_words(filler, "stable")

        section_wc = len(body.split())
        running += section_wc
        lines.append(body)
        lines.append("")

    text = "\n".join(lines).strip()

    # Fine-tune: trim or pad to hit exact target.
    actual = count_words(text)
    if actual > word_target:
        words = text.split()
        text = " ".join(words[: word_target + (len(words) - actual)])
        # Fallback: aggressive trim
        while count_words(text) > word_target:
            words = text.rsplit(maxsplit=1)
            text = words[0] if len(words) > 1 else text
    elif actual < word_target:
        diff = word_target - actual
        text += " " + _make_words(diff, "balanced")

    return text


# ===================================================================
# Word Count Tests
# ===================================================================

class TestWordCount:
    def test_exact_match_passes(self) -> None:
        text = _make_words(600, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is True
        assert result.hard_fail is False
        assert "diff=0" in result.details

    def test_within_tolerance_passes(self) -> None:
        text = _make_words(608, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is True

    def test_over_tolerance_fails(self) -> None:
        text = _make_words(615, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is False
        assert result.hard_fail is True

    def test_under_tolerance_fails(self) -> None:
        text = _make_words(885, "alpha")
        result = check_word_count(text, target=900, tolerance=10)
        assert result.passed is False
        assert result.hard_fail is True

    def test_at_lower_boundary_passes(self) -> None:
        text = _make_words(590, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is True

    def test_at_upper_boundary_passes(self) -> None:
        text = _make_words(610, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is True

    def test_one_past_lower_boundary_fails(self) -> None:
        text = _make_words(589, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is False

    def test_one_past_upper_boundary_fails(self) -> None:
        text = _make_words(611, "alpha")
        result = check_word_count(text, target=600, tolerance=10)
        assert result.passed is False

    @pytest.mark.parametrize("target", [600, 900, 1200, 2599, 3000])
    def test_targets_parametrized(self, target: int) -> None:
        text = _make_words(target, "alpha")
        result = check_word_count(text, target=target, tolerance=10)
        assert result.passed is True
        assert result.hard_fail is False

    @pytest.mark.parametrize("target", [600, 900, 1200, 2599, 3000])
    def test_targets_over_by_11_fails(self, target: int) -> None:
        text = _make_words(target + 11, "alpha")
        result = check_word_count(text, target=target, tolerance=10)
        assert result.passed is False
        assert result.hard_fail is True

    def test_empty_summary(self) -> None:
        result = check_word_count("", target=600, tolerance=10)
        assert result.passed is False
        assert result.hard_fail is True


# ===================================================================
# Repetition Tests
# ===================================================================

class TestRepetition:
    def test_no_repetition_scores_zero(self) -> None:
        text = (
            "Revenue grew strongly this quarter. "
            "Margins expanded due to operating leverage. "
            "Cash flow improved versus the prior period. "
            "Management maintained disciplined capital allocation."
        )
        result = check_repetition(text)
        assert result.passed is True
        assert result.score == 0.0

    def test_exact_duplicates_flagged(self) -> None:
        text = (
            "Revenue grew strongly this quarter. "
            "Revenue grew strongly this quarter. "
            "Margins expanded due to operating leverage."
        )
        result = check_repetition(text)
        assert result.passed is False
        assert result.score > 0.0
        assert "duplicate_pairs=1" in result.details

    def test_near_duplicate_above_threshold(self) -> None:
        text = (
            "Revenue grew strongly this quarter driven by pricing actions. "
            "Revenue grew strongly this quarter driven by pricing decisions. "
            "Margins expanded due to operating leverage."
        )
        result = check_repetition(text, threshold=0.85)
        assert result.passed is False

    def test_below_threshold_not_flagged(self) -> None:
        text = (
            "Revenue grew strongly this quarter. "
            "The company invested in new product development. "
            "Operating margins improved significantly."
        )
        result = check_repetition(text, threshold=0.85)
        assert result.passed is True

    def test_single_sentence_passes(self) -> None:
        result = check_repetition("Just one sentence here.")
        assert result.passed is True
        assert "fewer than 2" in result.details

    def test_multiple_duplicates_scored_higher(self) -> None:
        text = (
            "Revenue grew strongly this quarter. "
            "Revenue grew strongly this quarter. "
            "Revenue grew strongly this quarter. "
            "Margins expanded."
        )
        result = check_repetition(text)
        assert result.passed is False
        assert result.score > 0.0


# ===================================================================
# Numeric Density Tests
# ===================================================================

class TestNumericDensity:
    def test_low_density_passes(self) -> None:
        text = (
            "## Executive Summary\n"
            "The thesis is constructive and execution quality is stabilizing. "
            "Management maintained discipline while preserving flexibility. "
            "The setup remains investable for the near term.\n\n"
            "## Management Discussion & Analysis\n"
            "Capital allocation stayed balanced and reinvestment was measured. "
            "The practical read-through is positive for margin durability.\n"
        )
        result = check_numeric_density(text)
        assert result.passed is True

    def test_mdna_high_density_flagged(self) -> None:
        text = (
            "## Management Discussion & Analysis\n"
            "Revenue was $10.2B, operating income was $3.1B, margin was 30.4%, "
            "capex was $0.9B, free cash flow was $2.2B, debt was $4.3B, "
            "and current ratio was 2.1x.\n"
        )
        result = check_numeric_density(text)
        assert result.passed is False
        assert "Management Discussion & Analysis" in result.details

    def test_exec_summary_high_density_flagged(self) -> None:
        # prompt_pack cap for Executive Summary is 2 numbers/100w
        text = (
            "## Executive Summary\n"
            "Revenue of $22.0B and operating margin of 31.0% with net margin of 24.0% "
            "define the setup for the next quarter.\n"
        )
        result = check_numeric_density(text)
        assert result.passed is False
        assert "Executive Summary" in result.details

    def test_missing_sections_still_pass(self) -> None:
        # Only sections present in NUMERIC_DENSITY_CAPS are checked.
        # A section not in the caps dict (or not present in the text) is skipped.
        text = (
            "## Some Other Section\n"
            "Revenue was $10B and margins were 30% and cash was $5B and "
            "debt was $20B and ratio was 2.5x.\n"
        )
        result = check_numeric_density(text)
        assert result.passed is True

    def test_risk_factors_high_density_flagged(self) -> None:
        # prompt_pack cap for Risk Factors is 2 numbers/100w
        text = (
            "## Risk Factors\n"
            "Revenue could slip 8%, margin could compress 220 bps, and free cash "
            "flow could drop to $1.20B if discounting persists.\n"
        )
        result = check_numeric_density(text)
        assert result.passed is False
        assert "Risk Factors" in result.details

    def test_closing_takeaway_high_density_flagged(self) -> None:
        # prompt_pack cap for Closing Takeaway is 2 numbers/100w
        text = (
            "## Closing Takeaway\n"
            "HOLD at $45.20 target with 15% upside and 8% downside.\n"
        )
        result = check_numeric_density(text)
        assert result.passed is False
        assert "Closing Takeaway" in result.details


# ===================================================================
# Flow Score Tests
# ===================================================================

class TestFlowScore:
    def test_no_connectors_scores_zero(self) -> None:
        text = "Revenue grew. Margins expanded. Cash flow improved."
        result = check_flow_score(text)
        assert result.score == 0.0
        assert "unique_connectors=0" in result.details

    def test_varied_connectors_high_score(self) -> None:
        text = (
            "However, margins compressed versus the prior period. "
            "This suggests the trend may continue. "
            "Looking ahead, management expects improvement. "
            "Importantly, cash flow remained strong. "
            "Notably, the backlog grew for the third consecutive quarter. "
            "Consequently, the thesis remains constructive. "
            "Meanwhile, reinvestment discipline held."
        )
        result = check_flow_score(text)
        assert result.score >= 0.4
        assert "unique_connectors=7" in result.details

    def test_repeated_single_connector_low_variety(self) -> None:
        text = (
            "However, revenue grew. "
            "However, margins expanded. "
            "However, cash flow improved. "
            "However, leverage stayed flat."
        )
        result = check_flow_score(text)
        assert result.score < 0.15  # only 1 unique / 15 total
        assert "unique_connectors=1" in result.details

    def test_all_connectors_perfect_score(self) -> None:
        from app.services.eval_harness import NARRATIVE_CONNECTORS

        sentences = [f"{c.capitalize()} the trend continued." for c in NARRATIVE_CONNECTORS]
        text = " ".join(sentences)
        result = check_flow_score(text)
        assert result.score == 1.0


# ===================================================================
# Quote Validation Tests
# ===================================================================

class TestQuoteValidation:
    def test_quotes_present_and_grounded_passes(self) -> None:
        source = (
            'Management said "execution discipline remains strong despite macro pressure." '
            'The filing also notes "pricing actions offset most input-cost inflation." '
            'The CEO stated "our pipeline has never been stronger across every segment." '
            'The CFO noted "free cash flow conversion exceeded our internal targets this quarter."'
        )
        summary = (
            '## Executive Summary\n'
            'Management stated "execution discipline remains strong despite macro pressure." '
            'The filing says "pricing actions offset most input-cost inflation." '
            'The CEO highlighted "our pipeline has never been stronger across every segment."\n'
        )
        result = check_quotes(summary, source, min_quotes=3, max_quotes=8)
        assert result.passed is True

    def test_missing_quotes_fails(self) -> None:
        source = (
            'Management said "execution discipline remains strong." '
            'The filing notes "pricing actions offset inflation." '
            'The CEO stated "our pipeline is the strongest ever."'
        )
        summary = (
            "## Executive Summary\n"
            "Management highlighted execution discipline and pricing actions.\n"
        )
        result = check_quotes(summary, source, min_quotes=3, max_quotes=8)
        assert result.passed is False
        assert "too few quotes" in result.details

    def test_too_many_quotes_flagged(self) -> None:
        source = (
            'Management said "execution discipline remains strong." '
            'The filing notes "pricing actions offset inflation." '
        )
        summary = (
            '"execution discipline remains strong." '
            '"pricing actions offset inflation." '
            '"execution discipline remains strong." '
            '"pricing actions offset inflation." '
            '"execution discipline remains strong." '
            '"pricing actions offset inflation." '
            '"execution discipline remains strong." '
            '"pricing actions offset inflation." '
            '"execution discipline remains strong."'
        )
        result = check_quotes(summary, source, min_quotes=3, max_quotes=8)
        assert result.passed is False
        assert "too many quotes" in result.details

    def test_ungrounded_quotes_flagged(self) -> None:
        source = 'Management said "execution discipline remains strong."'
        summary = (
            '## Executive Summary\n'
            'Management said "demand accelerated to all-time highs across every region." '
            'The CEO noted "we have never seen such growth in any segment." '
            'CFO stated "free cash flow conversion is well above historical ranges."\n'
        )
        result = check_quotes(summary, source, min_quotes=3, max_quotes=8)
        assert result.passed is False
        assert "not grounded" in result.details

    def test_no_source_quotes_attribution_check(self) -> None:
        source = "The company reported revenue of $50 billion and margins improved."
        summary = (
            "## Executive Summary\n"
            "Management noted that capital allocation remained disciplined.\n"
        )
        result = check_quotes(summary, source)
        assert result.passed is True
        assert "attribution_phrases_found=" in result.details

    def test_no_source_quotes_no_attribution_fails(self) -> None:
        source = "Revenue grew and margins improved this quarter."
        summary = (
            "## Executive Summary\n"
            "Revenue grew and margins improved.\n"
        )
        result = check_quotes(summary, source)
        assert result.passed is False


# ===================================================================
# Section Completeness Tests
# ===================================================================

class TestSectionCompleteness:
    def test_all_sections_present_passes(self) -> None:
        text = (
            "## Executive Summary\nThesis here.\n\n"
            "## Financial Performance\nRevenue grew.\n\n"
            "## Management Discussion & Analysis\nCapital allocation.\n\n"
            "## Risk Factors\nExecution risk.\n\n"
            "## Key Metrics\n\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\nHOLD for now.\n"
        )
        result = check_section_completeness(text)
        assert result.passed is True
        assert result.hard_fail is False

    def test_missing_section_fails(self) -> None:
        text = (
            "## Executive Summary\nThesis here.\n\n"
            "## Financial Performance\nRevenue grew.\n\n"
            "## Risk Factors\nExecution risk.\n\n"
            "## Key Metrics\n\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\nHOLD for now.\n"
        )
        result = check_section_completeness(text)
        assert result.passed is False
        assert result.hard_fail is True
        assert "Management Discussion & Analysis" in result.details

    def test_mdna_and_variant_accepted(self) -> None:
        text = (
            "## Executive Summary\nThesis here.\n\n"
            "## Financial Performance\nRevenue grew.\n\n"
            "## Management Discussion and Analysis\nCapital allocation.\n\n"
            "## Risk Factors\nExecution risk.\n\n"
            "## Key Metrics\n\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\nHOLD for now.\n"
        )
        result = check_section_completeness(text)
        assert result.passed is True

    def test_health_rating_required_when_flag_set(self) -> None:
        text = (
            "## Executive Summary\nThesis.\n\n"
            "## Financial Performance\nRevenue.\n\n"
            "## Management Discussion & Analysis\nMDA.\n\n"
            "## Risk Factors\nRisk.\n\n"
            "## Key Metrics\nMetrics.\n\n"
            "## Closing Takeaway\nHOLD.\n"
        )
        result = check_section_completeness(text, include_health_rating=True)
        assert result.passed is False
        assert "Financial Health Rating" in result.details

    def test_health_rating_present_passes(self) -> None:
        text = (
            "## Financial Health Rating\n74/100 - Healthy.\n\n"
            "## Executive Summary\nThesis.\n\n"
            "## Financial Performance\nRevenue.\n\n"
            "## Management Discussion & Analysis\nMDA.\n\n"
            "## Risk Factors\nRisk.\n\n"
            "## Key Metrics\nMetrics.\n\n"
            "## Closing Takeaway\nHOLD.\n"
        )
        result = check_section_completeness(text, include_health_rating=True)
        assert result.passed is True

    def test_score_reflects_fraction_present(self) -> None:
        # Only 4 of 6 sections present.
        text = (
            "## Executive Summary\nThesis.\n\n"
            "## Financial Performance\nRevenue.\n\n"
            "## Risk Factors\nRisk.\n\n"
            "## Closing Takeaway\nHOLD.\n"
        )
        result = check_section_completeness(text)
        assert result.passed is False
        assert 0.5 < result.score < 1.0  # 4/6 ≈ 0.667


# ===================================================================
# Boilerplate Tests
# ===================================================================

class TestBoilerplate:
    def test_clean_prose_no_boilerplate(self) -> None:
        text = (
            "The thesis is constructive. Execution quality is stabilizing. "
            "Management maintained discipline while preserving flexibility."
        )
        result = check_boilerplate(text)
        assert result.passed is True
        assert "no boilerplate" in result.details

    def test_boilerplate_phrases_detected(self) -> None:
        text = (
            "It is worth noting that revenue grew. "
            "The company is well-positioned for growth. "
            "All things considered, the thesis holds."
        )
        result = check_boilerplate(text)
        assert result.passed is False
        assert "it is worth noting" in result.details
        assert "well-positioned" in result.details

    def test_single_boilerplate_detected(self) -> None:
        text = "Only time will tell if margins expand."
        result = check_boilerplate(text)
        assert result.passed is False

    def test_case_insensitive_detection(self) -> None:
        text = "It Should Be Noted that margins expanded."
        result = check_boilerplate(text)
        assert result.passed is False


# ===================================================================
# Cost Tracking Tests
# ===================================================================

class TestCostTracking:
    def test_within_budget_passes(self) -> None:
        tracker = PipelineCostTracker(budget_cap_usd=0.10)
        tracker.add_stage("generation", 10_000, 2_000, 0.04, 0.15)
        result = tracker.check_cost()
        assert result.passed is True

    def test_exceeds_budget_fails(self) -> None:
        tracker = PipelineCostTracker(budget_cap_usd=0.10)
        # Huge token counts to blow the budget.
        tracker.add_stage("generation", 1_000_000, 500_000, 1.25, 5.00)
        result = tracker.check_cost()
        assert result.passed is False
        assert result.hard_fail is True

    def test_cost_breakdown_per_stage(self) -> None:
        tracker = PipelineCostTracker(budget_cap_usd=0.10)
        s1 = tracker.add_stage("generation", 50_000, 5_000, 0.04, 0.15)
        s2 = tracker.add_stage("rewrite", 30_000, 3_000, 0.04, 0.15)

        report = tracker.to_dict()
        assert len(report["stages"]) == 2
        assert report["stages"][0]["stage_name"] == "generation"
        assert report["stages"][1]["stage_name"] == "rewrite"
        assert report["total_cost_usd"] == pytest.approx(
            s1.cost_usd + s2.cost_usd, abs=1e-6
        )

    def test_empty_tracker_within_budget(self) -> None:
        tracker = PipelineCostTracker(budget_cap_usd=0.10)
        assert tracker.check_budget() is True
        assert tracker.total_cost() == 0.0

    def test_add_stage_from_text(self) -> None:
        tracker = PipelineCostTracker()
        text_in = "x " * 1000  # ~2000 chars → ~500 tokens
        text_out = "y " * 500  # ~1000 chars → ~250 tokens
        stage = tracker.add_stage_from_text("test", text_in, text_out)
        assert stage.input_tokens > 0
        assert stage.output_tokens > 0
        assert stage.cost_usd > 0

    def test_to_dict_structure(self) -> None:
        tracker = PipelineCostTracker(budget_cap_usd=0.10)
        tracker.add_stage("gen", 10_000, 2_000, 0.04, 0.15)
        d = tracker.to_dict()
        assert "budget_cap_usd" in d
        assert "total_cost_usd" in d
        assert "total_input_tokens" in d
        assert "total_output_tokens" in d
        assert "within_budget" in d
        assert "stages" in d

    def test_multiple_stages_accumulate(self) -> None:
        tracker = PipelineCostTracker(budget_cap_usd=1.00)
        for i in range(5):
            tracker.add_stage(f"stage_{i}", 10_000, 2_000, 0.04, 0.15)
        assert tracker.total_input_tokens() == 50_000
        assert tracker.total_output_tokens() == 10_000
        assert len(tracker.stages) == 5


# ===================================================================
# Integration Tests
# ===================================================================

class TestEvaluateSummary:
    def test_full_pipeline_well_formed_summary_passes(self) -> None:
        summary = build_valid_summary(600)
        actual_wc = count_words(summary)
        # The builder may be off by a few words; use the actual count as target.
        report = evaluate_summary(
            summary,
            target_length=actual_wc,
            company="Apple Inc.",
            filing_type="10-Q",
            word_count_tolerance=10,
        )
        # Section completeness should pass; word count uses actual.
        section_result = next(
            r for r in report.results if r.check_name == "section_completeness"
        )
        assert section_result.passed is True

    def test_full_pipeline_catches_word_count_failure(self) -> None:
        summary = _make_words(500, "alpha")
        report = evaluate_summary(
            summary,
            target_length=600,
            company="Test",
            filing_type="10-K",
        )
        assert report.overall_pass is False
        wc_result = next(r for r in report.results if r.check_name == "word_count")
        assert wc_result.passed is False
        assert wc_result.hard_fail is True

    def test_full_pipeline_with_cost_tracker(self) -> None:
        summary = (
            "## Executive Summary\nThesis.\n\n"
            "## Financial Performance\nRevenue.\n\n"
            "## Management Discussion & Analysis\nMDA.\n\n"
            "## Risk Factors\nRisk.\n\n"
            "## Key Metrics\n\u2192 Revenue: $52B\n\n"
            "## Closing Takeaway\nHOLD.\n"
        )
        tracker = PipelineCostTracker(budget_cap_usd=0.10)
        tracker.add_stage("generation", 10_000, 2_000, 0.04, 0.15)

        report = evaluate_summary(
            summary,
            target_length=count_words(summary),
            cost_tracker=tracker,
        )
        cost_result = next(
            r for r in report.results if r.check_name == "cost_budget"
        )
        assert cost_result.passed is True
        assert report.cost_report is not None
        assert report.cost_report["within_budget"] is True

    def test_report_to_dict(self) -> None:
        summary = _make_words(100, "alpha")
        report = evaluate_summary(summary, target_length=100)
        d = report.to_dict()
        assert "results" in d
        assert "overall_pass" in d
        assert isinstance(d["results"], list)
        for r in d["results"]:
            assert "check_name" in r

    def test_source_text_triggers_quote_check(self) -> None:
        source = 'Management said "growth was strong this quarter."'
        summary = (
            "## Executive Summary\n"
            'Management said "growth was strong this quarter." '
            'The filing notes "growth was strong this quarter." '
            'Management highlighted "growth was strong this quarter."\n\n'
            "## Financial Performance\nRevenue.\n\n"
            "## Management Discussion & Analysis\nMDA.\n\n"
            "## Risk Factors\nRisk.\n\n"
            "## Key Metrics\nMetrics.\n\n"
            "## Closing Takeaway\nHOLD.\n"
        )
        report = evaluate_summary(
            summary,
            target_length=count_words(summary),
            source_text=source,
        )
        quote_result = next(
            (r for r in report.results if r.check_name == "quote_validation"),
            None,
        )
        assert quote_result is not None

    def test_no_source_text_skips_quote_check(self) -> None:
        summary = _make_words(100, "alpha")
        report = evaluate_summary(summary, target_length=100)
        quote_results = [
            r for r in report.results if r.check_name == "quote_validation"
        ]
        assert len(quote_results) == 0


class TestIndustryFixtures:
    """Smoke tests using multi-industry fixture data."""

    @pytest.mark.parametrize(
        "fixture",
        ALL_FIXTURES,
        ids=[f["ticker"] for f in ALL_FIXTURES],
    )
    def test_fixture_has_required_fields(self, fixture: dict) -> None:
        assert "ticker" in fixture
        assert "company" in fixture
        assert "sector" in fixture
        assert "filing_excerpt" in fixture
        assert "target_length" in fixture

    @pytest.mark.parametrize(
        "fixture",
        ALL_FIXTURES,
        ids=[f["ticker"] for f in ALL_FIXTURES],
    )
    def test_build_valid_summary_has_all_sections(self, fixture: dict) -> None:
        summary = build_valid_summary(fixture["target_length"])
        result = check_section_completeness(summary)
        assert result.passed is True, f"Missing sections for {fixture['ticker']}: {result.details}"

    @pytest.mark.parametrize(
        "fixture",
        ALL_FIXTURES,
        ids=[f["ticker"] for f in ALL_FIXTURES],
    )
    def test_build_valid_summary_no_boilerplate(self, fixture: dict) -> None:
        summary = build_valid_summary(fixture["target_length"])
        result = check_boilerplate(summary)
        assert result.passed is True, f"Boilerplate in {fixture['ticker']}: {result.details}"


# ===================================================================
# Edge Cases & Data-Structure Tests
# ===================================================================

class TestEdgeCases:
    def test_empty_summary_word_count_fails(self) -> None:
        result = check_word_count("", target=600)
        assert result.passed is False

    def test_empty_summary_section_completeness_fails(self) -> None:
        result = check_section_completeness("")
        assert result.passed is False

    def test_empty_summary_repetition_passes(self) -> None:
        result = check_repetition("")
        assert result.passed is True

    def test_empty_summary_flow_score_zero(self) -> None:
        result = check_flow_score("")
        assert result.score == 0.0

    def test_eval_result_to_dict(self) -> None:
        r = EvalResult(check_name="test", passed=True, score=0.9, details="ok")
        d = r.to_dict()
        assert d["check_name"] == "test"
        assert d["passed"] is True
        assert d["score"] == 0.9

    def test_stage_cost_to_dict(self) -> None:
        s = StageCost(stage_name="gen", input_tokens=100, output_tokens=50, cost_usd=0.001)
        d = s.to_dict()
        assert d["stage_name"] == "gen"
        assert d["input_tokens"] == 100

    def test_extract_section_body_returns_none_for_missing(self) -> None:
        text = "## Executive Summary\nThesis here.\n"
        assert extract_section_body(text, "Risk Factors") is None

    def test_extract_section_body_works(self) -> None:
        text = (
            "## Executive Summary\n"
            "The thesis is constructive.\n\n"
            "## Financial Performance\n"
            "Revenue grew.\n"
        )
        body = extract_section_body(text, "Executive Summary")
        assert body is not None
        assert "constructive" in body

    def test_count_words_matches_split_for_simple_text(self) -> None:
        text = "hello world foo bar baz"
        assert count_words(text) == 5
        assert count_words(text) == len(text.split())

    def test_count_words_strips_punctuation(self) -> None:
        text = '"hello," world! — foo'
        # After stripping punctuation: hello, world, foo → 3
        assert count_words(text) == 3


# ===================================================================
# Prompt Pack Integration Tests
# ===================================================================

class TestPromptPackIntegration:
    """Verify that the eval harness correctly sources constants from prompt_pack."""

    def test_density_caps_include_all_prompt_pack_prose_sections(self) -> None:
        from app.services.eval_harness import NUMERIC_DENSITY_CAPS
        from app.services.prompt_pack import NUMERIC_DENSITY_CAPS as PP_CAPS

        for section, cap in PP_CAPS.items():
            if cap >= 99:
                # Data blocks (Key Metrics) are excluded.
                assert section not in NUMERIC_DENSITY_CAPS
            else:
                assert section in NUMERIC_DENSITY_CAPS
                assert NUMERIC_DENSITY_CAPS[section] == float(cap)

    def test_standard_sections_match_prompt_pack_order(self) -> None:
        from app.services.eval_harness import STANDARD_SECTIONS
        from app.services.prompt_pack import SECTION_ORDER

        expected = [s for s in SECTION_ORDER if s != "Financial Health Rating"]
        assert STANDARD_SECTIONS == expected

    def test_boilerplate_includes_prompt_pack_banned_phrases(self) -> None:
        from app.services.eval_harness import BOILERPLATE_PHRASES

        # Key banned phrases from ANTI_BOREDOM_RULES should be present.
        assert "showcases its dominance" in BOILERPLATE_PHRASES
        assert "remains to be seen" in BOILERPLATE_PHRASES
        assert "well-positioned" in BOILERPLATE_PHRASES
        assert "leveraging synergies" in BOILERPLATE_PHRASES

    def test_attribution_phrases_include_prompt_pack_verbs(self) -> None:
        from app.services.eval_harness import ATTRIBUTION_PHRASES

        # QUOTE_BEHAVIOR_SPEC says: noted, acknowledged, emphasized,
        # highlighted, cautioned, described, characterized, indicated
        for verb in ["acknowledged", "cautioned", "described", "characterized"]:
            assert any(verb in p for p in ATTRIBUTION_PHRASES), (
                f"missing attribution verb: {verb}"
            )
