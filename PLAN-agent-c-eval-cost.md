# Agent C — Evaluation / QA / Cost Analyst Plan

## Objective
Own measurable quality and budget enforcement so regressions are caught automatically:
1. Hard fail if summary misses `target ±10` words.
2. Detect repetition, numeric overuse, weak narrative flow, quote misuse.
3. Report and enforce cost budget `<= $0.10` per summary.

Status: `Ready, blocked on Lead Gate G2 approval for cost-policy enforcement`.

## Current-State Snapshot
1. `backend/app/services/eval_harness.py`
   - Includes word-count, repetition, numeric density, flow, quote, section checks.
2. `backend/tests/test_eval_harness.py`
   - Broad unit coverage present; should be extended for final thresholds and regression suites.
3. `backend/app/services/ai_usage.py`
   - Tracks token/cost usage with dual OpenAI/Gemini env support.
4. `backend/tests/test_summary_retry_cost_policy.py`
   - Existing cost/retry policy tests still reference Gemini-prefixed config names.

## Implementation Plan
### Phase C1 — Hard-Gate Quality Checks
1. Word count check: hard fail outside `±10`.
2. Repetition check: near-duplicate sentence detection.
3. Numeric-density check per section with stricter caps on Executive Summary/MD&A/Risk/Closing.
4. Flow-score check: transition + coherence heuristics.
5. Quote check:
   - verify direct quotes against evidence when available
   - require attribution paraphrases when direct quotes unavailable

### Phase C2 — Cost Reporting + Alerts
1. Per-stage token/cost accounting:
   - research
   - outline
   - section drafts
   - rewrite/length control
   - final surgery
2. Hard budget alert policy for `$0.10` cap.
3. Summaries over budget are flagged with cause breakdown.

### Phase C3 — Multi-Industry Regression Set
1. Build/curate compact fixture set across sectors:
   - tech, healthcare, financials, energy, consumer, industrials
2. Include varied disclosure styles and filing quality.
3. Add target-length matrix including edge lengths (`2599`, `3000`).

### Phase C4 — CI/Report Artifacts
1. Generate machine-readable eval report JSON.
2. Publish concise human-readable summary table:
   - pass/fail by check
   - cost by stage
   - top failure causes

## Acceptance Criteria
1. Zero tolerance escapes on word-band check.
2. Repetition/numeric-density/flow thresholds catch regressions without high false positives.
3. Cost report produced for every run and highlights budget drift early.
4. Multi-industry suite demonstrates stable behavior across issuers.

## Validation Targets
1. `backend/tests/test_eval_harness.py`
2. `backend/tests/test_summary_retry_cost_policy.py`
3. `backend/tests/test_multi_company.py`
4. Spot-check integration with:
   - `backend/tests/test_filing_summary.py`
   - `backend/tests/test_summary_editorial_discipline.py`

## Approval Checkpoints
1. C-G1 (required): approve cost thresholds, alert policy, and fail/warn behavior.
2. C-G2 (required): approve regression dataset scope and gating criteria for merge.

## Deliverables
1. Eval scripts + thresholds.
2. Cost report schema and summary output.
3. Multi-company regression results with pass/fail matrix.
