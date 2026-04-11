# Agent B — Prompt / LLM Architect Plan

## Objective
Own narrative quality and section prompt architecture so outputs read like a strong analyst memo:
`Background -> What happened -> Why -> Management framing/quotes -> Risks -> What to watch -> Closing takeaway`.

Status: `Ready`.

## Current-State Snapshot
1. `backend/app/services/prompt_pack.py`
   - Good foundation for section templates, anti-boredom rules, quote spec.
2. `backend/app/services/prompt_builder.py`
   - Prompt composition layer to align with stage orchestration.
3. `backend/app/api/filings.py`
   - Contains additional hardening/cleanup passes that must stay aligned with prompt outputs.

## Implementation Plan
### Phase B1 — Prompt Pack Contract
1. Define stable interfaces for:
   - outline pass input/output schema (claims + evidence anchors)
   - section draft prompts
   - final assembly prompt
2. Keep templates industry-agnostic and filing-agnostic.

### Phase B2 — Narrative-First Section Specs
1. Executive Summary and MD&A must be qualitative-first:
   - business drivers
   - why results changed
   - strategy and forward setup
   - selective numeric support only
2. Risk Factors must cover real business/industry/company risks, not just metric deltas.
3. Closing Takeaway must synthesize prior sections, not repeat them.

### Phase B3 — Anti-Boredom Enforcement
1. Prevent repeated sentence structures/openers.
2. Ban mechanical transitions and boilerplate filler.
3. Force causal linking language and section handoffs.
4. Keep numeric density under per-section caps.

### Phase B4 — Quote Behavior
1. Use 3-8 high-signal quotes only when present in filing evidence.
2. If quotes unavailable, enforce attribution paraphrases:
   - "Management indicates..."
   - "The filing notes..."
3. Ensure quote placements support argument flow and are not stacked.

### Phase B5 — Prompt Examples and Fallbacks
1. Add positive/negative examples per section.
2. Define fallback instructions for weak filing quality or sparse management commentary.
3. Define multilingual disclosure-style robustness guidance without hardcoding issuers.

## Acceptance Criteria
1. Each section introduces net-new information.
2. Section transitions are explicit and coherent.
3. MD&A and Executive Summary are strategy/driver-led, not number-led.
4. Risk Factors read as underwriting risks with concrete mechanisms.
5. Closing Takeaway logically follows from prior sections.

## Validation Plan
1. Unit and parser alignment checks:
   - `backend/tests/test_summary_editorial_discipline.py`
   - `backend/tests/test_section_transition_validator.py`
2. Eval harness gates (Agent C):
   - repetition
   - numeric density
   - flow score
   - quote compliance
3. Manual spot checks on multi-industry fixtures with before/after comparison.

## Handoffs
1. To Agent A:
   - prompt contract schema and stage payload requirements.
2. To Agent C:
   - enforceable thresholds and banned-pattern list for eval checks.
3. To Agent D:
   - mapping table from UI controls (`tone`, `detail`, `complexity`, `focus`) to prompt flags.

## Deliverables
1. Prompt pack updates with examples and rules.
2. Section-level do/don't constraints and quote/fallback behavior.
3. Narrative linking specification consumable by backend orchestrator and evaluator.
