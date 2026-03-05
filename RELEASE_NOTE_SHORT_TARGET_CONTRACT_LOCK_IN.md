# Release Note: Short-Target Contract Lock-In (500-1200 Words)

## Date
- March 5, 2026

## Root Cause
- The frontend summary proxy retried `422` failures by progressively relaxing payload constraints.
- For explicit `target_length` requests, fallback retries could remove `target_length` entirely.
- That allowed a later retry to succeed with a materially shorter summary than requested.

## Policy Change
- Explicit target requests are now treated as hard-contract retries.
- Retry attempts for explicit targets preserve the original `target_length` on every retry payload.
- Retry order for explicit targets:
  1. Same payload with `health_rating.enabled=false` (when enabled)
  2. `mode: "default"` + original `target_length`
  3. `mode: "default"` + original `target_length` + `health_rating.enabled=false`
- Non-explicit target requests keep best-effort fallback behavior.

## Expected Behavior After Change
- Explicit target requests no longer silently downgrade to unbounded shorter summaries.
- If all bounded retries fail, the client receives a `422` failure response instead of a downgraded success.
- Non-explicit requests still benefit from broader fallback retries.

## Observability Headers
- Retry success/failure responses now include retry-policy metadata:
  - `x-financesum-summary-retry-policy`
  - `x-financesum-target-length-locked` (explicit-target flows)
  - `x-financesum-target-length` (explicit-target flows)
- Existing retry headers are preserved:
  - `x-financesum-summary-fallback-retry`
  - `x-financesum-summary-fallback-attempted`
  - `x-financesum-initial-failure-code`

## Validation Evidence
- Full backend test suite executed successfully:
  - `cd backend && pytest`
  - Result: `938 passed`
- Frontend production build executed successfully:
  - `cd frontend && npm run build`
  - Result: build completed (existing non-blocking lint warnings only)

## Deploy Notes (Frontend Only)
- Build and deploy frontend Cloud Run service:
  - `gcloud builds submit --tag gcr.io/financesums/financesums-frontend frontend`
  - `gcloud run deploy financesums-frontend --image gcr.io/financesums/financesums-frontend --platform managed --region europe-west1 --allow-unauthenticated`

## Rollback Notes
- If regression is detected, roll back the frontend Cloud Run service to the previous revision:
  - `gcloud run revisions list --service financesums-frontend --region europe-west1`
  - Route traffic back to the prior known-good revision.
