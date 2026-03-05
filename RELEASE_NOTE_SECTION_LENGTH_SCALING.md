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
