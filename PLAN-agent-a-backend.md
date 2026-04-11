# Agent A — Backend / Pipeline Engineer Plan

## Objective
Own the production migration and orchestration for filing summaries:
1. GPT-5.2 end-to-end (remove Gemini dependencies).
2. Stage-based pipeline with research dossier + evidence-first drafting.
3. Deterministic word-count controller with guaranteed `target ±10`.
4. Token/cost guardrails to keep each summary at `<= $0.10`.

Status: `Ready, blocked on Lead Gate G1 approval for backend/cost-critical execution`.

## Current-State Snapshot (Relevant Surfaces)
1. `backend/app/api/filings.py`
   - Main summarization flow, word-band enforcement, rewrite loops, model planning.
   - Still imports Gemini-named interfaces (`get_gemini_client`, `gemini_usage`, `gemini_exceptions`).
2. `backend/app/services/openai_client.py`
   - GPT-5.2 client exists, but still includes Gemini fallback logic.
3. `backend/app/config.py`
   - OpenAI key + legacy Gemini envs both present.
4. `backend/app/services/web_research.py`
   - Company dossier step and Supabase cache already exists.
5. `supabase/functions/get-gemini-key/index.ts`, `supabase/config.toml`
   - Legacy Gemini-named function still configured.

## Implementation Plan (Post-Approval)
### Phase A1 — OpenAI-Only Runtime Path
1. Remove Gemini fallback execution from core summary path.
2. Replace Gemini-named imports in summary pipeline with neutral/AI naming.
3. Keep compatibility shims only where needed for staged migration, not in primary path.
4. Align envs/docs toward `OPENAI_*` as source of truth.

Target files:
1. `backend/app/api/filings.py`
2. `backend/app/services/openai_client.py`
3. `backend/app/config.py`
4. `backend/app/services/ai_usage.py` and admin usage API
5. `supabase/config.toml` and legacy function cleanup path

### Phase A2 — Stage-Oriented Orchestration
1. Stage 0: input normalize + target length clamp + preference normalization.
2. Stage 1: web research dossier fetch/cache with source metadata.
3. Stage 2: filing parse/chunk and evidence extraction store:
   - financial metrics
   - narrative passages
   - quote candidates
4. Stage 3: outline plan (claims + evidence anchors).
5. Stage 4: section drafting by budget.
6. Stage 5: final assembly and coherence pass.

### Phase A3 — Strict Length Controller (`±10`)
1. Compute section budgets deterministically from target + weights.
2. Post-generation hard count (same counter used by evaluator).
3. If short: targeted section expansion prompts only where needed.
4. If long: targeted compression prompts only where needed.
5. Final deterministic surgery pass to land inside band without breaking section order/flow.

### Phase A4 — Cost Guardrails
1. Per-stage token budget caps and max retries.
2. Retrieval-only relevant chunks (no full-doc stuffing).
3. Cache-first reads for dossier/evidence where possible.
4. Preflight cost estimate before expensive rewrite loops.
5. Fail-safe behavior when projected cost exceeds budget.

### Phase A5 — Runbook + Operability
1. Add runbook for env vars, budget tuning, and incident fallback.
2. Add troubleshooting matrix for timeout/rate-limit/over-budget outcomes.

## Acceptance Criteria
1. Any user target length in `[1, 3000]` lands within `±10` words.
2. Pipeline runs for heterogeneous issuers/filings without issuer-specific assumptions.
3. No primary summary path dependency on Gemini APIs.
4. Cost policy demonstrates `<= $0.10` for baseline test matrix.

## Test/Validation Plan
1. Existing strict-length and surgery tests:
   - `backend/tests/test_word_band_enforcement.py`
   - `backend/tests/test_whitespace_word_band.py`
   - `backend/tests/test_final_word_band_guard.py`
2. Cost/retry tests:
   - `backend/tests/test_summary_retry_cost_policy.py`
3. Summary integration tests:
   - `backend/tests/test_filing_summary.py`
4. New multi-company regression:
   - `backend/tests/test_multi_company.py` (expand as needed)

## Approval Checkpoints
1. A-G1 (required): approve OpenAI-only migration + env strategy + fallback policy.
2. A-G2 (required): approve per-stage token/cost budgets and over-budget behavior.
3. A-G3 (required): approve canary regression report before merge.

## Deliverables
1. PR-ready backend changes.
2. Updated env/config and deprecation notes for Gemini naming.
3. Operational runbook with budget controls and rollback plan.
