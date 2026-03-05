# Release Note: Section-Length Scaling Lock (FP + MD&A)

## Issue
- Even when total summary length matched the request, `Financial Performance` and `Management Discussion & Analysis` could stay under-expanded.
- This was most visible on explicit short/mid targets and sometimes persisted on higher targets.

## Root Cause
- Deterministic top-up logic exhausted too early for these two sections:
  - fallback sentence variety was too limited,
  - guardrails could reject additional sentences,
  - target-aware minimums were only strongly enforced in limited paths.

## Changes
- Updated backend section repair logic in `backend/app/api/filings.py`:
  - Always compute target-aware minimums when `target_length` is provided.
  - Keep stronger focus floors for short quality-sensitive targets while preserving scalable floors for longer targets.
  - Expanded deterministic top-up behavior for `Financial Performance` and `Management Discussion & Analysis` with multi-variant, metric-aware continuation sentences.
  - Added overflow-resistant continuation generation so these sections do not stall after a small number of attempts.
  - Increased bounded top-up rounds dynamically by section deficit for explicit target flows.
  - Applied the same scaling logic to strict contract structural repairs.

## Expected Behavior
- When users request a longer summary, `Financial Performance` and `MD&A` now scale upward with the requested target instead of staying near short-form depth.
- For explicit targets, these sections are much less likely to remain underweight while the output still returns `200`.
- If contractual bounds cannot be satisfied after bounded repairs, existing `422` behavior remains intact.

## Validation
- Local backend tests: `pytest` (full suite) -> `942 passed`.
- Local frontend build: `npm run build` (success; warnings only).
- Added regression test:
  - `backend/tests/test_enforce_section_budget_distribution.py::test_ensure_required_sections_scales_fp_and_mdna_with_target_length`

## Deploy
- Built backend image:
  - `gcloud builds submit --tag gcr.io/financesums/financesums-backend backend`
  - Build ID: `fc4563be-1a96-4b97-b10d-7db365da4d26`
  - Image digest: `sha256:a6184d02e51918dd6d1c55d7e4a63d7d57e22088f86d2db02da9492d10707958`
- Deployed Cloud Run backend:
  - Revision: `financesums-backend-00444-m44`
  - URL: `https://financesums-backend-1093972319438.europe-west1.run.app`
- Health check:
  - `/health` -> `{"status":"healthy","service":"financesums-backend","revision":"financesums-backend-00444-m44"}`

## Rollback
- To rollback backend, redeploy a prior revision or shift traffic to an older ready revision:
  - `gcloud run revisions list --service financesums-backend --region europe-west1`
  - `gcloud run services update-traffic financesums-backend --region europe-west1 --to-revisions <OLD_REVISION>=100`

## Update: Continuous-V2 Short-Target Stabilization (2026-03-05)

### Root Cause
- Continuous-v2 outputs could still fail at the final validator because section-balance and risk-schema constraints were checked after route-level rewrites without a last deterministic repair pass.
- In short/mid requests, this surfaced as underweighted `Financial Performance` / `MD&A`, malformed Risk Factors structure, or late section drift that caused `422`.

### Policy and Code Changes
- Added a deterministic continuous-v2 fallback repair path in `backend/app/api/filings.py`:
  - normalize headings/order,
  - re-run `_ensure_required_sections(...)`,
  - apply `_apply_contract_structural_repairs(...)`,
  - force `_rebalance_section_budgets_deterministically(...)` using failures from the validator,
  - re-seal with `_apply_strict_contract_seal(...)`,
  - revalidate before returning `422`.
- Hardened risk normalization for explicit target flows:
  - expanded generic risk-name rewriting coverage,
  - enforced mechanism/transmission/early-warning phrasing per risk entry,
  - preserved distinct risk entries while still de-duplicating exact duplicates,
  - added bounded long-form risk top-up to avoid underweight fall-through.
- Updated short-target behavior tests to include `500` in FP/MD&A scaling coverage.

### Expected Behavior
- Explicit target requests in continuous-v2 should no longer silently fail from avoidable section drift at the final checkpoint.
- `Financial Performance` and `MD&A` remain target-scaled on short targets.
- Risk Factors are more likely to satisfy schema and section-balance requirements without returning `422`.

### Validation
- Backend full suite: `cd backend && pytest` -> `944 passed`.
- Frontend build: `cd frontend && npm run build` -> success (warnings only).
- Added/updated regression coverage:
  - `backend/tests/test_enforce_section_budget_distribution.py::test_ensure_required_sections_scales_fp_and_mdna_with_target_length` (now includes `500`),
  - `backend/tests/test_enforce_section_budget_distribution.py::test_ensure_required_sections_normalizes_risk_schema_for_short_targets`,
  - `backend/tests/test_filing_summary.py::test_summary_trims_when_model_refuses` (accepts in-band success or explicit contract `422`).

## Update: Short-Target Timeout Contract Gate (2026-03-05)

### Root Cause
- Explicit short-target requests (`500-1200`) could hit the runtime cap and return a degraded `200` draft that failed target/section contract checks.
- That timeout fallback path bypassed strict short-target contract enforcement.

### Policy and Code Changes
- Updated timeout fallback behavior in `backend/app/api/filings.py`:
  - for explicit short-target requests, run deterministic post-timeout repair + validation before returning any fallback success,
  - if repaired output still fails contract, do not return degraded success,
  - return explicit `422` with `failure_code=SUMMARY_CONTRACT_TIMEOUT` instead.
- Existing best-effort timeout behavior remains for non-explicit or non-short-target flows.

### Expected Behavior
- Short explicit targets no longer silently downgrade to underweight `200` outputs when timeout occurs.
- Success responses on this path are contract-checked; otherwise the client receives actionable `422`.

### Validation
- `pytest backend/tests/test_filing_summary.py -k "timeout"` -> `4 passed`.
- `pytest backend/tests/test_enforce_section_budget_distribution.py backend/tests/test_filing_summary.py -q` -> `124 passed`.
- Added regression:
  - `backend/tests/test_filing_summary.py::test_short_target_timeout_returns_422_when_contract_not_met`.

## Update: Short-Target Last-Mile Structural Rescue (2026-03-05)

### Root Cause
- After timeout/contract recovery, some short targets still failed structurally on narrow, fixable issues (for example: missing explicit Closing recommendation and underweight Executive Summary).

### Policy and Code Changes
- Added an extra deterministic structural rescue pass before short-form hard-fail in `backend/app/api/filings.py`:
  - enforce closing recommendation via `_repair_closing_recommendation_in_summary(...)`,
  - rebalance section budgets using parsed structural failure flags,
  - re-seal final word band and whitespace band,
  - re-evaluate contract once more before emitting `422`.
- Added runtime floor for explicit short targets:
  - `SUMMARY_SHORT_TARGET_TIMEOUT_SECONDS` (default `300`) to reduce premature timeout failures.

### Validation
- `pytest backend/tests/test_filing_summary.py -k "timeout"` -> `4 passed`.
- `pytest backend/tests/test_enforce_section_budget_distribution.py backend/tests/test_filing_summary.py -q` -> `124 passed`.
