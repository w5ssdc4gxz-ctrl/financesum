# Release Note: 850 Contract Rescue and Frontend Guidance

## Date
- March 11, 2026

## Issue
- Strict short-form summary requests could fail with `SUMMARY_CONTRACT_FAILED` even when the draft was close enough to recover.
- The frontend error modal treated these validator failures like generic network issues and suggested refresh/retry steps that did not address the real problem.

## Root Cause
- `backend/app/api/filings.py` only tried to rebuild `Key Metrics` from fresh metric sources during short-form underflow repair.
- If the current draft already had a valid but underweight `Key Metrics` block and no fresh metric payload was available, the repair path could return unchanged.
- The late strict-contract rescue path could repair `Key Metrics` and still stop before section rebalance / short underflow top-up, leaving otherwise recoverable `850`-word requests to hard-fail.

## Changes
- Backend (`backend/app/api/filings.py`):
  - use the existing `Key Metrics` section as a deterministic repair candidate,
  - align the section-balance validator with the shared `Key Metrics` contract window,
  - extend late short-form rescue so a successful `Key Metrics` repair can continue into rebalance, short top-up, and narrative expansion before final hard-fail,
  - re-check `Key Metrics` after final rebanding if a later pass clips it again.
- Frontend (`frontend/app/company/[id]/page.tsx`):
  - replace the generic hard-refresh guidance for `SUMMARY_CONTRACT_FAILED` with contract-aware next steps,
  - keep extension/network advice only for actual network/client-block cases,
  - show tailored action tips for timeout, insufficient metrics, and budget-cap failures.

## Regression Coverage
- Added backend regression tests in `backend/tests/test_filing_summary.py` for:
  - repairing `Key Metrics` from the existing draft when fresh metric sources are empty,
  - chaining late `850`-word rescue from `Key Metrics` repair into section rebalance and final top-up.

## Validation
- Backend: `cd backend && pytest` -> `1017 passed, 42 warnings in 165.29s`.
- Frontend: `cd frontend && npm run build` -> success (existing non-blocking lint warnings only).

## Deploy
- Backend build:
  - `gcloud builds submit --tag gcr.io/financesums/financesums-backend backend`
  - Build ID: `b1833ffb-74b9-482a-9448-7732f0b7a43a`
  - Image digest: `sha256:59780070e4be979c3eb6f7f4951c56473c874c77dc0fed09222522a419e4d4b9`
- Backend deploy:
  - Revision: `financesums-backend-00477-gtm`
  - URL: `https://financesums-backend-xbw6ttfgka-ew.a.run.app`
- Frontend deploy:
  - Direct `gcloud builds submit --tag ... frontend` failed because the Docker build did not receive the required `NEXT_PUBLIC_*` build args.
  - Used the repo-supported helper instead: `CLOUDSDK_CONFIG="$HOME/.config/gcloud" ./scripts/deploy_live.sh frontend`
  - Build ID: `529c5ab5-9fc9-4838-b887-773012111bd0`
  - Image digest: `sha256:b89d6424a1e1bea831293f8dbd65d01e15f0cc9c1e263d18095a42d3789dabda`
  - Revision: `financesums-frontend-00220-qzn`
  - URL: `https://financesums-frontend-xbw6ttfgka-ew.a.run.app`
- Live verification:
  - `gcloud run services list --region europe-west1 --platform managed` shows both services healthy.
  - Backend health: `curl -sSf <backend-url>/health` -> `{"status":"healthy","service":"financesums-backend","revision":"financesums-backend-00477-gtm"}`
  - Frontend root: `curl -I -sSf <frontend-url>/` -> `HTTP/2 200`

## Expected Behavior
- Repairable `850`-word contract failures with underweight `Key Metrics` should recover more often instead of returning `422`.
- When strict contract failure is real, the UI should suggest shorter targets / richer filings instead of asking the user to hard refresh.

## Update: Timeout Residual Mixed-Failure Rescue (2026-03-11)

### Issue
- Some explicit `850`-word timeout fallbacks still returned `SUMMARY_CONTRACT_TIMEOUT` when the remaining failures were a recoverable mix of:
  - `Risk Factors` schema drift,
  - a small `Key Metrics` underflow,
  - and residual section-balance misses.

### Code Change
- Updated `backend/app/api/filings.py` so the timeout post-validation rescue now:
  - still runs when `Key Metrics` is recoverably underweight,
  - refreshes validator flags after rebalance / top-up / expansion steps,
  - and can continue from `Key Metrics` repair into risk/balance repair instead of stopping early.
- Added a coercion path for malformed but salvageable `Key Metrics` blocks so colon-form rows like `Revenue: $10.0B` can be normalized back into numeric `DATA_GRID` rows before underflow repair.
- Allowed short-form timeout band repair to keep working when `Key Metrics` is only slightly underweight, then re-run `Key Metrics` repair after narrative expansion so a small metrics miss no longer blocks recovery of the full memo.
- Added a late editorial deterministic rescue pass for contract failures so repeated figures/themes can be cleaned after final sealing, then re-banded without another model call.
- Tightened `_enforce_whitespace_word_band()` so `Key Metrics` rows compact to `Label| Value` when split-count drift is the only thing pushing the memo over the visible band.

### Regression Coverage
- Added `backend/tests/test_filing_summary.py::test_bounded_timeout_contract_repair_post_validation_rescues_850_risk_schema_plus_key_metrics_underflow`.
- Added `backend/tests/test_filing_summary.py::test_bounded_timeout_contract_repair_rescues_850_underword_with_recoverable_key_metrics_and_closing_gap`.
- Added `backend/tests/test_filing_summary.py::test_repair_short_form_key_metrics_underflow_coerces_colon_rows_when_metric_sources_are_empty`.
- Added `backend/tests/test_filing_summary.py::test_short_form_number_theme_repetition_issue_is_repaired_before_contract_422`.
- Added `backend/tests/test_whitespace_word_band.py::test_enforce_whitespace_word_band_compacts_key_metrics_pipes_when_split_count_dominates`.
- Added `backend/tests/test_filing_summary.py::test_continuous_v2_route_rescues_850_risk_schema_plus_key_metrics_underflow`.

### Deploy Update
- Backend rebuild:
  - Build ID: `12fa93e2-c76b-4af5-a207-9991d8d710eb`
  - Image digest: `sha256:569d841470b4f309904e21bc209939eb727222faeddc8b9f0417de5f636c392d`
- Backend redeploy:
  - Revision: `financesums-backend-00478-vdn`
  - URL: `https://financesums-backend-xbw6ttfgka-ew.a.run.app`
  - Health: `{"status":"healthy","service":"financesums-backend","revision":"financesums-backend-00478-vdn"}`
- Frontend guidance update:
  - Added handling for `SUMMARY_SECTION_BALANCE_FAILED` in `frontend/app/company/[id]/page.tsx` so mixed structural failures render actionable guidance instead of raw JSON + generic retry copy.
  - Revision: `financesums-frontend-00221-zhd`
  - URL: `https://financesums-frontend-xbw6ttfgka-ew.a.run.app`

### Deploy Update 2
- Backend rebuild:
  - Build ID: `68bfe04d-7885-43cf-8b1f-589281d9644a`
  - Image digest: `sha256:cd1a64a89f7dc6f1df66c8a57a0fd5dbc1e813b0e180767869a4f1ab8eb9c205`
- Backend redeploy:
  - Revision: `financesums-backend-00480-xlz`
  - URL: `https://financesums-backend-xbw6ttfgka-ew.a.run.app`
  - Health: `{"status":"healthy","service":"financesums-backend","revision":"financesums-backend-00480-xlz"}`
