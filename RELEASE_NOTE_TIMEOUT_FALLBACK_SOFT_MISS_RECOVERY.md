# Release Note: Timeout Fallback Soft-Miss Recovery

## Date
- March 6, 2026
- Updated March 7, 2026

## Root Cause
- The summary timeout fallback path could still end in a hard `422` even after the draft was repaired into a near-pass state.
- The old timeout repair sequence mixed structural cleanup, section rebalancing, and strict sealing in a brittle order.
- For short explicit targets, small section-budget misses could survive until validation and trigger `SUMMARY_CONTRACT_TIMEOUT` despite the draft being materially usable.

## Backend Changes
- Added a bounded timeout repair loop that re-validates after each deterministic cleanup pass instead of relying on one fragile second-pass sequence.
- Added short-contract underflow expansion that preferentially restores `Financial Performance`, `Management Discussion & Analysis`, and `Closing Takeaway` without inflating already-problematic risk sections.
- Preserved `Risk Factors` from generic rebalance when the validator already reports a risk-schema issue, which prevents repair logic from damaging the required risk structure.
- Tightened risk-entry sentence cleanup so repaired risk bullets are normalized into complete 2-3 sentence entries more reliably.
- Improved section overweight trimming so small overages are reduced with lighter edits before falling back to blunt truncation.
- Added a post-validation short-target rescue inside the timeout repair loop so drafts that fall under-band only after repetition cleanup can be topped back up in the same bounded round instead of timing out with `SUMMARY_CONTRACT_TIMEOUT`.
- Reweighted timeout-candidate ranking so in-band drafts with only soft section-budget misses outrank under-band drafts that still contain duplicate-sentence or repeated-analysis failures.
- Expanded that post-validation rescue to also handle mixed cases where the draft is still under-band but only small section-budget overages remain, such as an overweight `Financial Health Rating` or `Closing Takeaway` alongside a narrow total-word deficit.

## Behavior After Change
- Explicit-target success responses are now re-checked immediately before return, so any cached, timeout-fallback, soft-target, or best-effort response that lands outside the backend word band now fails with `422` instead of returning a degraded `200`.
- Timeout fallback still returns degraded `200` responses for narrow, section-budget-only soft misses only when:
  1. The repaired draft is already inside the explicit total word band.
  2. Only up to four section-budget failures remain.
  3. Each remaining section miss is within 15 words of its allowed section range, not just the raw budget midpoint.
- Soft-miss responses expose the remaining issues through `summary_meta.contract_missing_requirements`, `summary_meta.timeout_fallback_contract_soft_miss`, and `contract_warnings`.

## Validation Evidence
- Full backend test suite executed successfully:
  - `cd backend && pytest`
  - Result: `957 passed`
- Frontend production build executed successfully:
  - `cd frontend && npm run build`
  - Result: build completed successfully
  - Notes: existing non-blocking ESLint warnings and client-render deopt warnings remain

## Deploy Notes
- Backend-only deploy is sufficient for this fix.
- Because the workspace contains many unrelated uncommitted backend changes, deploy from an isolated backend build context that includes only this targeted fix before publishing the image.
