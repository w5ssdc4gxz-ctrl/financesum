# Agent D — Frontend/UX Plan: Map UI Settings into Section Budgets & Prompt Flags

Status: `Ready`.
Coordination mode: `Frontend/UI mapping only; no backend/cost-policy logic changes in this track.`
Dependencies: `Agent A` (strict length contract + section-weight API), `Agent B` (prompt-flag contract), `Agent C` (eval visibility).

## Current State Analysis

### Data Flow (today)
```
SummaryWizard.tsx (UI form state)
  → page.tsx: buildPreferencePayload() → FilingSummaryPreferencesPayload
    → api-client.ts: filingsApi.summarizeFiling(filingId, payload)
      → Backend: /api/v1/filings/{id}/summary (POST)
        → filings.py: _build_user_instruction_block(preferences)
          → Uses fixed SECTION_PROPORTIONAL_WEIGHTS for budget calc
          → Tone, detail_level, output_style, complexity → flat string instructions
```

### Key Observations
1. **Focus areas → section budgets**: Currently, `focus_areas` are passed to the backend as a flat list and turned into prompt text ("cover strictly in this order and dedicate space"). But the actual `_calculate_section_word_budgets()` uses **fixed** `SECTION_PROPORTIONAL_WEIGHTS` (12/18/16/18/18/8/10) regardless of focus areas. The UI has no way to influence the proportional distribution.

2. **Tone → prompt flags**: Currently mapped to a single string: `"Tone must remain {tone}."` — no structured prompt modifiers.

3. **Detail level → target length**: No link between `detailLevel` and the slider range. A user can pick "Snapshot" but leave the slider at 3000 words.

4. **Target length → strict controller**: Already sent as `target_length` in the payload. Backend enforces ±15 words. Task #4 will tighten this to ±10.

5. **`buildPreferencePayload`**: Does NOT currently send `complexity` to the backend payload (`FilingSummaryPreferencesPayload` omits it, though the backend schema has it).

6. **EnhancedSummary.tsx**: `CompanyInsightsSection` is defined but never rendered. No "Key Takeaways" feature. Section hierarchy relies solely on markdown heading parsing.

---

## Implementation Plan

### Part 1: New shared mapping utilities — `frontend/lib/summary-mappings.ts`

Create a new utility module that centralizes all UI-to-backend mapping logic. This keeps both SummaryWizard and page.tsx clean.

```typescript
// Section weight overrides based on focus areas
export function computeSectionWeights(focusAreas: string[]): Record<string, number> { ... }

// Tone → structured prompt modifier flags
export function toneToPromptFlags(tone: SummaryTone): { ... }

// Detail level → suggested slider range
export function detailLevelToRange(level: SummaryDetailLevel): { min: number, max: number, default: number }

// Complexity → vocabulary/structure hints
export function complexityToFlags(complexity: SummaryComplexity): { ... }
```

**Focus Area → Section Weight Mapping:**
| Focus Area Selected | Section Affected | Weight Change |
|---|---|---|
| "Financial performance" | Financial Performance | 16 → 24 (+8) |
| "Risk factors" | Risk Factors | 18 → 28 (+10) |
| "Strategy & execution" | MD&A | 18 → 26 (+8) |
| "Capital allocation" | Financial Performance | 16 → 22, MD&A: 18 → 22 |
| "Liquidity & balance sheet" | Financial Health Rating: 12 → 20 (+8) |
| "Guidance & outlook" | Closing Takeaway: 10 → 18, MD&A: 18 → 22 |

When multiple focus areas are selected, weights are blended proportionally and re-normalized to sum to 100.

### Part 2: Update `FilingSummaryPreferencesPayload` and backend schema

**Frontend (`api-client.ts`):** Add new fields to `FilingSummaryPreferencesPayload`:
```typescript
export type FilingSummaryPreferencesPayload = {
  // ... existing fields ...
  complexity?: string                          // MISSING currently — add it
  section_weight_overrides?: Record<string, number>  // NEW: custom section weights
  include_key_takeaways?: boolean              // NEW: optional Key Takeaways
}
```

**Backend (`schemas.py`):** Add matching fields to `FilingSummaryPreferences`:
```python
section_weight_overrides: Optional[Dict[str, int]] = Field(default=None)
include_key_takeaways: bool = Field(default=False)
```

### Part 3: Update `SummaryWizard.tsx`

1. **Detail level ↔ slider sync**: When user changes `detailLevel`, auto-adjust `targetLength` to the midpoint of the suggested range (only if the current value is outside the new range).
   - snapshot: 300-600 → default 450
   - balanced: 600-1200 → default 800
   - deep dive: 1200-3000 → default 1800

2. **Slider range indicator**: Show the suggested range for the current detail level as a highlighted zone on the BrutalSlider track.

3. **"Include Key Takeaways" toggle**: Add a checkbox/toggle in Step 1 that lets the user request 3-5 bullet key takeaways at the top (counting toward word budget).

4. **Output style → exposed in wizard**: Already present, but verify it's in the review step.

### Part 4: Update `buildPreferencePayload()` in `page.tsx`

```typescript
const buildPreferencePayload = (prefs: SummaryPreferenceFormState): FilingSummaryPreferencesPayload => {
  // ... existing logic ...

  // NEW: Compute section weight overrides from focus areas
  const sectionWeights = computeSectionWeights(prefs.focusAreas)

  return {
    // ... existing fields ...
    complexity: prefs.complexity,  // FIX: was missing
    section_weight_overrides: Object.keys(sectionWeights).length > 0 ? sectionWeights : undefined,
    include_key_takeaways: prefs.includeKeyTakeaways ?? false,
  }
}
```

### Part 5: Update `EnhancedSummary.tsx` — UX Improvements

1. **Better section hierarchy**:
   - Add section dividers with left-border accent colors per section
   - Use `CompanyInsightsSection` (currently dead code) — wire it into the render
   - Add visual section indicators (colored dots + section labels) for key sections

2. **Key Takeaways block**:
   - Parse "## Key Takeaways" or "## TL;DR" from summary content
   - Render as a highlighted callout box at the top (before the financial summary card)
   - Subtle blue-left-border box with 3-5 bullet points

3. **Reduce repeated metric lines**:
   - Deduplicate metric data between inline `MetricsGrid` and `FinancialSummaryCard`
   - If `FinancialSummaryCard` is rendering revenue/net income/etc., suppress those same metrics from the extracted `MetricsGrid`

4. **Generic company support**:
   - Audit all components for hard-coded tickers/sectors
   - Ensure `CompanyInsightsSection` works for any filing (already seems generic)

### Part 6: Form state extension

Add to `SummaryPreferenceFormState`:
```typescript
includeKeyTakeaways: boolean  // default: true
```

Update defaults in `page.tsx` and `SummaryWizard.tsx`.

---

## Files Modified

| File | Changes |
|---|---|
| `frontend/lib/summary-mappings.ts` | NEW — section weight calculator, tone flags, detail-level ranges |
| `frontend/lib/api-client.ts` | Add `complexity`, `section_weight_overrides`, `include_key_takeaways` to payload type |
| `frontend/components/SummaryWizard.tsx` | Detail level ↔ slider sync, key takeaways toggle, range indicator |
| `frontend/app/company/[id]/page.tsx` | Fix `buildPreferencePayload` to send complexity + section weights, add `includeKeyTakeaways` to form state |
| `frontend/components/EnhancedSummary.tsx` | Key Takeaways callout, section hierarchy improvements, dedup metrics, wire `CompanyInsightsSection` |
| `backend/app/models/schemas.py` | Add `section_weight_overrides`, `include_key_takeaways` fields |

## Blocked Dependencies

- **Task #4** (strict word-count controller): The ±10 tolerance is backend-side. Frontend just needs to send `target_length`. This plan is compatible — we send target_length as a strict target, not a max cap.
- **Task #5** (new prompt pack): The `section_weight_overrides` field lets the backend override `SECTION_PROPORTIONAL_WEIGHTS` when computing budgets. Agent B's prompt changes will consume these weights.

## Implementation Order

1. Create `summary-mappings.ts` (pure utility, no dependencies)
2. Update `api-client.ts` payload type
3. Update `schemas.py` backend schema
4. Update `SummaryWizard.tsx` (detail level sync, key takeaways toggle)
5. Update `page.tsx` (`buildPreferencePayload`, form state defaults)
6. Update `EnhancedSummary.tsx` (UX improvements)
7. Test end-to-end with multiple companies
