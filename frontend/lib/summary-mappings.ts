/**
 * summary-mappings.ts
 *
 * Centralized mapping utilities that translate UI customization settings
 * (focus areas, tone, detail level, complexity) into structured data the
 * backend pipeline can consume directly.
 *
 * These mirror the backend's canonical section-budget controller so the
 * frontend preview matches the backend allocation model.
 */

// ---------------------------------------------------------------------------
// Types (mirrored from SummaryWizard / page.tsx for self-containment)
// ---------------------------------------------------------------------------

export type SummaryTone = 'objective' | 'cautiously optimistic' | 'bullish' | 'bearish'
export type SummaryDetailLevel = 'snapshot' | 'balanced' | 'deep dive'
export type SummaryComplexity = 'simple' | 'intermediate' | 'expert'

// ---------------------------------------------------------------------------
// Section names — must match backend SECTION_PROPORTIONAL_WEIGHTS keys
// ---------------------------------------------------------------------------

const SECTIONS = {
  HEALTH: 'Financial Health Rating',
  EXEC: 'Executive Summary',
  PERF: 'Financial Performance',
  MDA: 'Management Discussion & Analysis',
  RISK: 'Risk Factors',
  METRICS: 'Key Metrics',
  CLOSING: 'Closing Takeaway',
} as const

type SectionName = (typeof SECTIONS)[keyof typeof SECTIONS]
const SECTION_ORDER: SectionName[] = [
  SECTIONS.HEALTH,
  SECTIONS.EXEC,
  SECTIONS.PERF,
  SECTIONS.MDA,
  SECTIONS.RISK,
  SECTIONS.METRICS,
  SECTIONS.CLOSING,
]

// ---------------------------------------------------------------------------
// Default weights (must stay in sync with backend)
// ---------------------------------------------------------------------------

const DEFAULT_WEIGHTS: Record<SectionName, number> = {
  [SECTIONS.HEALTH]: 20,
  [SECTIONS.EXEC]: 15,
  [SECTIONS.PERF]: 20,
  [SECTIONS.MDA]: 20,
  [SECTIONS.RISK]: 15,
  [SECTIONS.METRICS]: 8,
  [SECTIONS.CLOSING]: 10,
}

const DEFAULT_WEIGHTS_WITHOUT_HEALTH: Record<Exclude<SectionName, typeof SECTIONS.HEALTH>, number> = {
  [SECTIONS.EXEC]: 15,
  [SECTIONS.PERF]: 20,
  [SECTIONS.MDA]: 20,
  [SECTIONS.RISK]: 15,
  [SECTIONS.METRICS]: 8,
  [SECTIONS.CLOSING]: 10,
}

const KEY_METRICS_MIN_WORDS = 20
const KEY_METRICS_MAX_WORDS = 90
const KEY_METRICS_WEIGHT = 0.08
const NARRATIVE_FLOOR_MIN_WORDS = 24
const RISK_FLOOR_MIN_WORDS = 36
const NARRATIVE_FLOOR_PCT = 0.08
const RISK_FLOOR_PCT = 0.12

function applyIntegerDrift(
  budgets: Record<string, number>,
  exacts: Record<string, number>,
  sections: string[],
  targetTotal: number,
): Record<string, number> {
  let drift = targetTotal - sections.reduce((sum, section) => sum + (budgets[section] || 0), 0)
  if (drift === 0) return budgets

  const ranked = [...sections].sort(
    (a, b) => ((exacts[b] || 0) - (budgets[b] || 0)) - ((exacts[a] || 0) - (budgets[a] || 0)),
  )
  const step = drift > 0 ? 1 : -1
  let remaining = Math.abs(drift)
  let idx = 0

  while (ranked.length > 0 && remaining > 0 && idx < 10000) {
    const section = ranked[idx % ranked.length]
    const nextValue = (budgets[section] || 0) + step
    if (nextValue >= 0) {
      budgets[section] = nextValue
      remaining -= 1
    }
    idx += 1
  }

  return budgets
}

function getNarrativeSections(includeHealthRating: boolean): SectionName[] {
  return SECTION_ORDER.filter(section => (
    section !== SECTIONS.METRICS &&
    (includeHealthRating || section !== SECTIONS.HEALTH)
  ))
}

function computeProportionalFloors(
  narrativeTargetWords: number,
  sections: SectionName[],
): Record<string, number> {
  const floors: Record<string, number> = {}

  for (const section of sections) {
    floors[section] = section === SECTIONS.RISK
      ? Math.max(RISK_FLOOR_MIN_WORDS, Math.round(narrativeTargetWords * RISK_FLOOR_PCT))
      : Math.max(NARRATIVE_FLOOR_MIN_WORDS, Math.round(narrativeTargetWords * NARRATIVE_FLOOR_PCT))
  }

  return floors
}

function reserveKeyMetricsBudget(bodyTarget: number, narrativeFloorTotal: number): number {
  if (bodyTarget <= 0) return 0

  const preferred = Math.max(
    KEY_METRICS_MIN_WORDS,
    Math.min(KEY_METRICS_MAX_WORDS, Math.round(bodyTarget * KEY_METRICS_WEIGHT)),
  )
  const maxAllowed = Math.max(0, bodyTarget - narrativeFloorTotal)
  if (maxAllowed <= 0) return 0

  return Math.max(0, Math.min(preferred, maxAllowed))
}

// ---------------------------------------------------------------------------
// Focus Area → Section Weight Boost Map
// ---------------------------------------------------------------------------

/**
 * Each focus area maps to one or more sections that should receive extra
 * weight. The `boost` value is added to the section's default weight before
 * re-normalisation.
 */
const FOCUS_AREA_BOOSTS: Record<string, Partial<Record<SectionName, number>>> = {
  'Financial performance': {
    [SECTIONS.PERF]: 6,
  },
  'Risk factors': {
    [SECTIONS.RISK]: 6,
  },
  'Strategy & execution': {
    [SECTIONS.MDA]: 6,
  },
  'Capital allocation': {
    [SECTIONS.PERF]: 4,
    [SECTIONS.MDA]: 4,
  },
  'Liquidity & balance sheet': {
    [SECTIONS.HEALTH]: 6,
  },
  'Guidance & outlook': {
    [SECTIONS.CLOSING]: 4,
    [SECTIONS.MDA]: 4,
  },
}

function getDefaultWeights(includeHealthRating: boolean): Record<string, number> {
  return includeHealthRating
    ? { ...DEFAULT_WEIGHTS }
    : { ...DEFAULT_WEIGHTS_WITHOUT_HEALTH }
}

/**
 * Compute section weight overrides based on the user's selected focus areas.
 *
 * When no focus areas are selected (or all are selected), returns an empty
 * object — meaning the backend should use its defaults.
 *
 * When one or more focus areas are selected the corresponding sections get
 * a boost, and all weights are re-normalised so they sum to 100.
 */
export function computeSectionWeights(
  focusAreas: string[],
  includeHealthRating: boolean = true,
): Record<string, number> {
  if (!focusAreas || focusAreas.length === 0) return {}

  const weights = getDefaultWeights(includeHealthRating) as Record<string, number>

  // Accumulate boosts
  let hasBoost = false
  for (const area of focusAreas) {
    const boosts = FOCUS_AREA_BOOSTS[area]
    if (!boosts) continue
    for (const [section, boost] of Object.entries(boosts)) {
      weights[section as SectionName] = (weights[section as SectionName] || 0) + boost
      hasBoost = true
    }
  }

  if (!hasBoost) return {}

  // Re-normalise to sum = 100
  const total = Object.values(weights).reduce((s, w) => s + w, 0)
  if (total <= 0) return {}

  const normalised: Record<string, number> = {}
  for (const [section, w] of Object.entries(weights)) {
    normalised[section] = Math.round((w / total) * 100)
  }

  // Fix rounding drift so the sum is exactly 100
  const sum = Object.values(normalised).reduce((s, w) => s + w, 0)
  if (sum !== 100) {
    // Add/subtract the drift from the largest section
    const largest = Object.entries(normalised).sort((a, b) => b[1] - a[1])[0]
    if (largest) {
      normalised[largest[0]] += 100 - sum
    }
  }

  return normalised
}

export function computeSectionBudgetPreview(
  targetLength: number,
  _focusAreas: string[],
  includeHealthRating: boolean,
): Record<string, number> {
  if (!Number.isFinite(targetLength) || targetLength <= 0) return {}

  const sections = SECTION_ORDER.filter(section => includeHealthRating || section !== SECTIONS.HEALTH)
  const narrativeSections = getNarrativeSections(includeHealthRating)
  const weights = getDefaultWeights(includeHealthRating) as Record<string, number>

  const headingWords = sections.reduce((sum, section) => (
    sum + section.split(/\s+/).filter(Boolean).length
  ), 0)
  let bodyTarget = Math.max(0, Math.round(targetLength) - headingWords)
  if (bodyTarget <= 0) bodyTarget = Math.round(targetLength)

  const provisionalKeyMetricsBudget = Math.max(
    KEY_METRICS_MIN_WORDS,
    Math.min(KEY_METRICS_MAX_WORDS, Math.round(bodyTarget * KEY_METRICS_WEIGHT)),
  )
  const provisionalNarrativeTarget = Math.max(0, bodyTarget - provisionalKeyMetricsBudget)
  let floors = computeProportionalFloors(provisionalNarrativeTarget, narrativeSections)
  let narrativeFloorTotal = narrativeSections.reduce((sum, section) => sum + (floors[section] || 0), 0)
  let keyMetricsBudget = reserveKeyMetricsBudget(bodyTarget, narrativeFloorTotal)
  let narrativeBodyTarget = Math.max(0, bodyTarget - keyMetricsBudget)

  floors = computeProportionalFloors(narrativeBodyTarget, narrativeSections)
  narrativeFloorTotal = narrativeSections.reduce((sum, section) => sum + (floors[section] || 0), 0)

  if (narrativeBodyTarget < narrativeFloorTotal) {
    keyMetricsBudget = Math.max(0, bodyTarget - narrativeFloorTotal)
    narrativeBodyTarget = Math.max(0, bodyTarget - keyMetricsBudget)
    floors = computeProportionalFloors(narrativeBodyTarget, narrativeSections)
  }

  const budgets: Record<string, number> = {}
  for (const section of narrativeSections) {
    budgets[section] = floors[section] || 0
  }

  const totalWeight = narrativeSections.reduce((sum, section) => sum + (weights[section] || 0), 0) || narrativeSections.length || 1
  const remaining = Math.max(0, narrativeBodyTarget - narrativeSections.reduce((sum, section) => sum + (budgets[section] || 0), 0))
  const exactExtras: Record<string, number> = {}
  const extraBudgets: Record<string, number> = {}

  for (const section of narrativeSections) {
    exactExtras[section] = ((weights[section] || 0) * remaining) / totalWeight
    extraBudgets[section] = Math.floor(exactExtras[section])
  }

  applyIntegerDrift(extraBudgets, exactExtras, narrativeSections, remaining)

  for (const section of narrativeSections) {
    budgets[section] = (budgets[section] || 0) + (extraBudgets[section] || 0)
  }

  budgets[SECTIONS.METRICS] = keyMetricsBudget

  return sections.reduce((acc, section) => {
    acc[section] = Math.max(1, Math.round(budgets[section] || 0))
    return acc
  }, {} as Record<string, number>)
}

// ---------------------------------------------------------------------------
// Detail Level → Suggested Target Length Range
// ---------------------------------------------------------------------------

export type DetailLevelRange = {
  min: number
  max: number
  default: number
}

const DETAIL_RANGES: Record<SummaryDetailLevel, DetailLevelRange> = {
  snapshot: { min: 300, max: 700, default: 500 },
  balanced: { min: 700, max: 1400, default: 1000 },
  'deep dive': { min: 1400, max: 3000, default: 1800 },
}

/**
 * Return the suggested word-count range for a given detail level.
 */
export function detailLevelToRange(level: SummaryDetailLevel): DetailLevelRange {
  return DETAIL_RANGES[level] ?? DETAIL_RANGES.balanced
}

/**
 * Given a detail level and the current slider value, return a suggested new
 * value. If the current value already falls within the range, it is returned
 * unchanged. Otherwise the range default is returned.
 */
export function suggestTargetLength(
  level: SummaryDetailLevel,
  currentValue: number,
): number {
  const range = detailLevelToRange(level)
  if (currentValue >= range.min && currentValue <= range.max) {
    return currentValue
  }
  return range.default
}

// ---------------------------------------------------------------------------
// Tone → Prompt Flags (informational — consumed by frontend review & backend)
// ---------------------------------------------------------------------------

export type ToneFlags = {
  key: SummaryTone
  label: string
  modifiers: string[]
}

const TONE_FLAGS: Record<SummaryTone, ToneFlags> = {
  objective: {
    key: 'objective',
    label: 'Objective',
    modifiers: [
      'Use neutral, evidence-driven language',
      'Present balanced pros and cons for every major point',
      'Avoid superlatives or directional bias',
    ],
  },
  'cautiously optimistic': {
    key: 'cautiously optimistic',
    label: 'Cautiously Optimistic',
    modifiers: [
      'Acknowledge positives and growth drivers',
      'Note material risks and headwinds alongside upside',
      'Frame outlook as guardedly constructive',
    ],
  },
  bullish: {
    key: 'bullish',
    label: 'Bullish',
    modifiers: [
      'Emphasise growth drivers and upside catalysts',
      'Lead with strengths; mention risks briefly',
      'Highlight competitive advantages and market opportunity',
    ],
  },
  bearish: {
    key: 'bearish',
    label: 'Bearish',
    modifiers: [
      'Emphasise risks, downside scenarios, and red flags',
      'Lead with weaknesses; mention strengths briefly',
      'Highlight competitive threats and margin pressure',
    ],
  },
}

export function toneToPromptFlags(tone: SummaryTone): ToneFlags {
  return TONE_FLAGS[tone] ?? TONE_FLAGS.objective
}

// ---------------------------------------------------------------------------
// Complexity → Vocabulary Flags
// ---------------------------------------------------------------------------

export type ComplexityFlags = {
  key: SummaryComplexity
  label: string
  description: string
}

const COMPLEXITY_FLAGS: Record<SummaryComplexity, ComplexityFlags> = {
  simple: {
    key: 'simple',
    label: 'Simple',
    description: 'Plain English, no jargon, accessible to any reader',
  },
  intermediate: {
    key: 'intermediate',
    label: 'Intermediate',
    description: 'Standard financial analysis language',
  },
  expert: {
    key: 'expert',
    label: 'Expert',
    description: 'Sophisticated terminology for professional investors',
  },
}

export function complexityToFlags(complexity: SummaryComplexity): ComplexityFlags {
  return COMPLEXITY_FLAGS[complexity] ?? COMPLEXITY_FLAGS.intermediate
}
