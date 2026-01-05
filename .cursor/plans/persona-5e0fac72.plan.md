---
name: Fix Sentence Cutoff Issue
overview: ""
todos:
  - id: c10aaef0-b50d-471f-94be-a406f79e13dc
    content: Add persona_name param to _generate_fallback_closing_takeaway with persona-specific text
    status: pending
  - id: 0c38c0dc-c861-4a5c-8a47-68c54a71fae4
    content: Add persona_name param to _ensure_required_sections and pass through
    status: pending
  - id: 4d1b3883-4580-41d1-8a12-4c7800baeacf
    content: Pass selected_persona_name to all 3 _ensure_required_sections calls
    status: pending
---

# Fix Sentence Cutoff Issue

## Problem

The AI is cutting off sentences mid-way throughout the summary to meet word count requirements:

- "...the cyclical nature of the semiconductor industry and."
- "...represent a significant percentage of."
- "...Capital expenditures total $1."
- "...The moat, while present, may not be as wide as I."

The word count constraint (e.g., "must be between 640-660 words") is causing the AI to prioritize hitting the target over completing sentences.

## Solution

In [`backend/app/api/filings.py`](backend/app/api/filings.py), modify the length constraint instructions to:

1. **Widen the word count tolerance** - Change from +/-10 words to +/-50 words to give breathing room
2. **Prioritize completion over word count** - Explicitly state that completing sentences is MORE important than hitting exact word count
3. **Add "never cut off" language** - Make it absolutely clear that mid-sentence cutoffs are forbidden even if it means missing the word target
4. **Reorder priorities** - Put sentence completion BEFORE word count in the priority hierarchy

## Changes

### 1. Update `_build_preference_instructions` (around line 1536)

Modify the `CRITICAL LENGTH CONSTRAINT` section to:

- Widen tolerance from +/-10 to +/-50 words
- Add explicit "NEVER cut off mid-sentence" rule
- State that sentence completion trumps word count

### 2. Strengthen existing sentence completion rules in main prompt

The main prompt already has sentence completion rules but they need to be elevated to highest priority, above word count.