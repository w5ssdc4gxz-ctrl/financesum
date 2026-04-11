# Lead Coordination Plan — Filing Summary Production Upgrade

## Mission
Upgrade the public-company filing summary system to production readiness with four parallel teammate tracks while keeping the lead in coordination mode.

Primary priorities (in order):
1. Strict word-count accuracy for any target length: `target ±10 words`, every run.
2. Narrative quality and flow: analyst-memo style, non-repetitive, causal, coherent closing.
3. Generalization across issuers/industries/filing quality.
4. Cost control: `<= $0.10` per summary.

## Team Spawn (Live)
1. Agent A — Backend / Pipeline Engineer
   - Plan file: `PLAN-agent-a-backend.md`
   - Status: `Ready, waiting for approval gate`
2. Agent B — Prompt / LLM Architect
   - Plan file: `PLAN-agent-b-prompt-llm.md`
   - Status: `Ready`
3. Agent C — Evaluation / QA / Cost Analyst
   - Plan file: `PLAN-agent-c-eval-cost.md`
   - Status: `Ready, waiting for approval gate`
4. Agent D — Frontend / UX
   - Plan file: `PLAN-agent-d-frontend.md`
   - Status: `Ready`

## Lead Rules (Enforced)
1. Coordination-only by default: lead does not implement product code unless a teammate is blocked.
2. Backend/cost-critical work requires explicit plan approval before implementation.
3. Teammates produce file-level implementation plans, tests, and rollback notes before code.
4. Shared contract changes are documented first, then consumed by dependent agents.

## Approval Gates
1. Gate G1 — Architecture Approval (required before Agent A implementation)
   - Scope: model migration contract, stage orchestration, caching, strict length loop, cost budgets.
   - Must be approved: env-var strategy, OpenAI-only path, fallback policy, timeout/retry policy.
2. Gate G2 — Cost Policy Approval (required before Agent C enforcement wiring)
   - Scope: per-stage token/cost budgets, over-budget behavior, alert thresholds.
   - Must be approved: budget assumptions and fail/warn policy.
3. Gate G3 — Integration Approval (required before merge)
   - Scope: end-to-end multi-company results, word-band pass rate, quality metrics, cost report.

## Shared Task Board
1. Locate current Gemini usage and summarization flow.
   - Owner: Agent A
   - Status: In progress
2. Implement GPT-5.2 migration end-to-end and remove Gemini dependencies.
   - Owner: Agent A
   - Status: Blocked on Gate G1
3. Add web-research dossier step with caching and source capture.
   - Owner: Agent A
   - Status: Planned
4. Implement strict word-count controller (`±10`) for all targets.
   - Owner: Agent A
   - Status: Planned
5. Build narrative-first prompt pack with qualitative MD&A and real risk factors.
   - Owner: Agent B
   - Status: Planned
6. Build QA harness with hard word-band fail, repetition/numeric-density/flow checks.
   - Owner: Agent C
   - Status: Planned
7. Enforce cost budget and produce per-stage cost report.
   - Owner: Agent C
   - Status: Blocked on Gate G2
8. Map UI settings to section budgets and prompt flags.
   - Owner: Agent D
   - Status: Planned
9. Run multi-company regression and document before/after quality.
   - Owners: Agent C + Agent D (support), Agent A (pipeline)
   - Status: Planned

## Handoff Contracts
1. Agent A -> Agent B
   - Exposes stage context contract for outline/draft/final passes.
2. Agent B -> Agent A
   - Provides prompt pack interfaces and section-level constraints.
3. Agent A -> Agent C
   - Emits telemetry per stage: token input/output, estimated cost, retries, cache-hit flags.
4. Agent C -> Agent A
   - Returns pass/fail thresholds and budget alarm hooks.
5. Agent D -> Agent A/B
   - Sends normalized UI controls (`focus`, `tone`, `detail`, `complexity`, `target_length`, `include_key_takeaways`) and section-weight overrides.

## Done Criteria
1. Word count: 100% pass on test matrix for targets including `600, 900, 1200, 2599, 3000` with `±10`.
2. Quality: repetition and numeric-density thresholds pass on multi-industry set.
3. Quotes: 3-8 high-signal quotes when available; otherwise attribution-based paraphrase.
4. Cost: median and p95 under `$0.10` with alerting for budget drift.
5. Documentation: runbook + regression report + rollback notes.
