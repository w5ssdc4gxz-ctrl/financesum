# Agent C — QA Harness Implementation Plan (Task #6)

## Overview
Create `backend/app/services/eval_harness.py` and `backend/tests/test_eval_harness.py` providing an automated quality evaluation harness for filing summaries.

## Files to Create
1. `backend/app/services/eval_harness.py` — Main evaluation harness module
2. `backend/tests/test_eval_harness.py` — Comprehensive test suite
3. `backend/tests/fixtures/industry_samples.py` — Multi-industry test fixture data

## Implementation Steps

### Step 1: Create `eval_harness.py` with Quality Check Functions

**1a. Word Count Check (HARD FAIL)**
- Use existing `_count_words()` pattern from filings.py (whitespace split + punctuation strip)
- `check_word_count(summary: str, target: int, tolerance: int = 10) -> EvalResult`
- Returns PASS/FAIL with actual vs expected counts
- Test targets: 600, 900, 1200, 2599, 3000

**1b. Repetition Detection**
- `check_repetition(summary: str) -> EvalResult`
- Split text into sentences using `re.split(r"(?<=[.!?])\s+", ...)`
- Use `difflib.SequenceMatcher` to compare all pairs; flag ratio > 0.85
- Return score 0-1 (0 = no repetition, 1 = all duplicate)
- Also check for repeated sentence openings (same first 5 words)

**1c. Numeric Density per Section**
- `check_numeric_density(summary: str) -> EvalResult`
- Extract each section body using regex matching the `_extract_markdown_section_body` pattern
- Count numbers via `re.findall(r"\d+", section_body)` per 100 words
- Thresholds: MD&A ≤ 3 numbers/100 words, Executive Summary ≤ 3 numbers/100 words
- Flag violations with section name and actual density

**1d. Flow Score (Narrative Connectors)**
- `check_flow_score(summary: str) -> EvalResult`
- Connector list: "however", "this suggests", "looking ahead", "importantly", "notably", "consequently", "meanwhile", "furthermore", "nevertheless", "that said", "in contrast", "as a result", "going forward", "more broadly", "the practical read-through"
- Score = unique_connectors / len(connector_list) (variety measure)
- Also track total_connectors / total_sentences (usage frequency)

**1e. Quote Validation**
- `check_quotes(summary: str, source_text: str) -> EvalResult`
- If source has quoted text: check summary has 3-8 direct quotes
- Verify quotes are grounded in source text (fuzzy match with ratio > 0.80)
- If no source quotes: check for attribution phrases ("management noted", "the filing states", etc.)

**1f. Section Completeness**
- `check_section_completeness(summary: str, include_health_rating: bool = False) -> EvalResult`
- Expected sections: Executive Summary, Financial Performance, Management Discussion & Analysis, Risk Factors, Key Metrics, Closing Takeaway
- Optional: Financial Health Rating
- Check each is present via `## SectionName` heading regex

**1g. Boilerplate Detection**
- `check_boilerplate(summary: str) -> EvalResult`
- Flag generic filler phrases: "it is worth noting", "it should be noted", "in conclusion", "all things considered", "at the end of the day", "moving forward", "remains to be seen", "only time will tell", "the company continues to"
- Return count and list of detected boilerplate phrases

### Step 2: Cost Reporting Module

**2a. Pipeline Cost Tracker**
- `CostReport` dataclass with fields: stage_name, input_tokens, output_tokens, cost_usd
- `PipelineCostTracker` class:
  - `add_stage(name, input_tokens, output_tokens, rate_per_m_input, rate_per_m_output)`
  - `total_cost() -> float`
  - `check_budget(cap: float = 0.10) -> bool`
  - `to_dict() -> dict` for Supabase logging
- Token estimation: reuse the `len(text) / 4` pattern from gemini_usage.py

**2b. Supabase Logging**
- `log_eval_to_supabase(eval_results: dict, cost_report: dict) -> None`
- Best-effort logging (never raises)
- Includes company, filing_type, target_length, all check results, cost breakdown

### Step 3: Top-Level Evaluation Orchestrator

- `EvalResult` dataclass: check_name, passed, score, details, hard_fail
- `EvalReport` dataclass: company, filing_type, target_length, results (list of EvalResult), cost_report, overall_pass
- `evaluate_summary(summary, target_length, source_text, company, filing_type, include_health_rating) -> EvalReport`
  - Runs all checks, aggregates results
  - overall_pass = True only if all hard-fail checks pass AND no cost budget exceeded

### Step 4: Multi-Industry Test Fixtures

Create `backend/tests/fixtures/industry_samples.py` with sample data:
- **Tech (AAPL)**: ~200 word filing excerpt + expected 600-word summary skeleton
- **Healthcare (JNJ)**: excerpt with pharmaceutical terminology
- **Financial (JPM)**: banking metrics, capital ratios
- **Energy (XOM)**: upstream/downstream, commodity exposure
- **Consumer (PG)**: brand portfolio, organic growth
- **Industrial (CAT)**: backlog, equipment orders

Each fixture: `{"ticker": ..., "company": ..., "sector": ..., "filing_excerpt": ..., "sample_summary": ..., "target_length": ...}`

### Step 5: Create `test_eval_harness.py`

Tests grouped by check:

**Word Count Tests:**
- `test_word_count_exact_match_passes` — target 600, actual 600
- `test_word_count_within_tolerance_passes` — target 600, actual 608 (±10)
- `test_word_count_over_tolerance_fails` — target 600, actual 615
- `test_word_count_under_tolerance_fails` — target 900, actual 885
- `test_word_count_targets_600_900_1200_2599_3000` — parametrized across all targets

**Repetition Tests:**
- `test_no_repetition_scores_zero`
- `test_high_repetition_flagged` — duplicate sentences injected
- `test_near_duplicate_detection_above_085_threshold`

**Numeric Density Tests:**
- `test_low_density_passes`
- `test_mdna_high_density_flagged`
- `test_exec_summary_high_density_flagged`

**Flow Score Tests:**
- `test_no_connectors_scores_zero`
- `test_varied_connectors_high_score`
- `test_repeated_single_connector_low_variety`

**Quote Validation Tests:**
- `test_quotes_present_and_grounded_passes`
- `test_missing_quotes_fails`
- `test_too_many_quotes_flagged`
- `test_no_source_quotes_attribution_check`

**Section Completeness Tests:**
- `test_all_sections_present_passes`
- `test_missing_section_fails`

**Boilerplate Tests:**
- `test_clean_prose_no_boilerplate`
- `test_boilerplate_phrases_detected`

**Cost Tests:**
- `test_cost_within_budget_passes`
- `test_cost_exceeds_budget_fails`
- `test_cost_breakdown_per_stage`

**Integration Tests:**
- `test_full_evaluation_pipeline_passes` — well-formed summary from each industry fixture
- `test_full_evaluation_pipeline_catches_multiple_issues` — summary with known defects

## Design Decisions
- Reuse `_count_words` pattern from filings.py for consistency (don't import private function — reimplement)
- Use `re` for section extraction to match existing codebase pattern
- `difflib.SequenceMatcher` for repetition (stdlib, no new dependencies)
- All functions are pure/testable — no side effects except Supabase logging
- EvalResult uses dataclasses (matches codebase style with SummaryModelPlan, etc.)
- tolerance parameter defaults to 10 (task requirement) not 15 (current codebase value)
