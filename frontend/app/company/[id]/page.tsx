'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import AnimatedList from '@/components/AnimatedList'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Navbar from '@/components/Navbar'
import HealthScoreBadge from '@/components/HealthScoreBadge'
import FinancialCharts from '@/components/FinancialCharts'
import PersonaSelector from '@/components/PersonaSelector'
import EnhancedSummary from '@/components/EnhancedSummary'
import { companyApi, filingsApi, analysisApi, API_BASE_URL, FilingSummaryPreferencesPayload } from '@/lib/api-client'
import DashboardStorage, { StoredAnalysisSnapshot, StoredSummaryPreferences } from '@/lib/dashboard-storage'
import { buildSummaryPreview, scoreToRating } from '@/lib/analysis-insights'
import { Button } from '@/components/base/buttons/button'
import { Button as StatefulButton } from '@/components/ui/stateful-button'
import { useAuth } from '@/contexts/AuthContext'
import { BrutalButton } from '@/components/ui/BrutalButton'
import SummaryWizard, { INVESTOR_PERSONAS } from '@/components/SummaryWizard'
import { cn } from '@/lib/utils'
import { MultiStepLoader } from '@/components/ui/multi-step-loader'

type SummaryMode = 'default' | 'custom'
type SummaryTone = 'objective' | 'cautiously optimistic' | 'bullish' | 'bearish'
type SummaryDetailLevel = 'snapshot' | 'balanced' | 'deep dive'
type SummaryOutputStyle = 'narrative' | 'bullets' | 'mixed'

type HealthFramework =
  | 'value_investor_default'
  | 'quality_moat_focus'
  | 'financial_resilience'
  | 'growth_sustainability'
  | 'user_defined_mix'

type HealthWeighting =
  | 'profitability_margins'
  | 'cash_flow_conversion'
  | 'balance_sheet_strength'
  | 'liquidity_near_term_risk'
  | 'execution_competitiveness'

type HealthRiskTolerance = 'very_conservative' | 'moderately_conservative' | 'balanced' | 'moderately_lenient' | 'very_lenient'

type HealthAnalysisDepth = 'headline_only' | 'key_financial_items' | 'full_footnote_review' | 'accounting_integrity' | 'forensic_deep_dive'

type HealthDisplayStyle = 'score_only' | 'score_plus_grade' | 'score_plus_traffic_light' | 'score_plus_pillars' | 'score_with_narrative'

type HealthRatingFormState = {
  enabled: boolean
  framework: HealthFramework
  weighting: HealthWeighting
  riskTolerance: HealthRiskTolerance
  analysisDepth: HealthAnalysisDepth
  displayStyle: HealthDisplayStyle
}

type SummaryPreferenceFormState = {
  mode: SummaryMode
  investorFocus: string
  focusAreas: string[]
  tone: SummaryTone
  detailLevel: SummaryDetailLevel
  outputStyle: SummaryOutputStyle
  targetLength: number
  healthRating: HealthRatingFormState
  includeHealthScore: boolean
  complexity: 'simple' | 'intermediate' | 'expert'
  selectedPersona: string | null
}

type SummaryPreferenceSnapshot = {
  mode: SummaryMode
  investorFocus?: string
  focusAreas?: string[]
  tone?: SummaryTone
  detailLevel?: SummaryDetailLevel
  outputStyle?: SummaryOutputStyle
  targetLength?: number
  healthRating?: Partial<HealthRatingFormState>
  selectedPersona?: string | null
}

type FilingSummary = {
  content: string
  metadata: SummaryPreferenceSnapshot
  healthRating?: number
}

type FilingSummaryMap = Record<string, FilingSummary>

const focusAreaOptions = [
  'Financial performance',
  'Risk factors',
  'Strategy & execution',
  'Capital allocation',
  'Liquidity & balance sheet',
  'Guidance & outlook',
]

const toneOptions: Array<{ value: SummaryTone; label: string; description: string }> = [
  { value: 'objective', label: 'Objective', description: 'Neutral research voice' },
  { value: 'cautiously optimistic', label: 'Cautiously Optimistic', description: 'Balanced view with guarded optimism' },
  { value: 'bullish', label: 'Bullish', description: 'Highlight upside catalysts' },
  { value: 'bearish', label: 'Bearish', description: 'Stress downside risks' },
]

const detailOptions: Array<{ value: SummaryDetailLevel; label: string; description: string }> = [
  { value: 'snapshot', label: 'Snapshot', description: 'High-level overview with only essentials' },
  { value: 'balanced', label: 'Balanced', description: 'Default depth across all sections' },
  { value: 'deep dive', label: 'Deep Dive', description: 'Exhaustive commentary, metric-by-metric' },
]

const outputStyleOptions: Array<{ value: SummaryOutputStyle; label: string; description: string }> = [
  { value: 'narrative', label: 'Narrative', description: 'Paragraph-first storytelling' },
  { value: 'bullets', label: 'Bullet-Heavy', description: 'Concise key takeaways' },
  { value: 'mixed', label: 'Mixed', description: 'Narrative with supporting bullets' },
]

const healthFrameworkOptions: Array<{ value: HealthFramework; label: string; description: string }> = [
  { value: 'value_investor_default', label: 'Value Investor Default', description: 'Cash flow durability, balance sheet strength, downside protection' },
  { value: 'quality_moat_focus', label: 'Quality & Moat Focus', description: 'ROIC consistency, competitive advantage, earnings stability' },
  { value: 'financial_resilience', label: 'Financial Resilience', description: 'Liquidity coverage, leverage, refinancing risk' },
  { value: 'growth_sustainability', label: 'Growth Sustainability', description: 'Margin expansion, reinvestment efficiency, growth durability' },
  { value: 'user_defined_mix', label: 'User-Defined Mix', description: 'Equal weight: profitability, risk, liquidity, growth, efficiency' },
]

const healthWeightingOptions: Array<{ value: HealthWeighting; label: string; description: string }> = [
  { value: 'profitability_margins', label: 'Profitability & Margins', description: 'Emphasize margin momentum and earnings power' },
  { value: 'cash_flow_conversion', label: 'Cash Flow & Conversion', description: 'Prioritize free cash flow quality' },
  { value: 'balance_sheet_strength', label: 'Balance Sheet Strength', description: 'Focus on leverage, debt mix, and coverage' },
  { value: 'liquidity_near_term_risk', label: 'Liquidity & Near-Term Risk', description: 'Watch near-term debt maturities and cash runway' },
  { value: 'execution_competitiveness', label: 'Execution & Competitiveness', description: 'Reward operational excellence and competitive position' },
]

const healthRiskOptions: Array<{ value: HealthRiskTolerance; label: string; description: string }> = [
  { value: 'very_conservative', label: 'Very Conservative', description: 'Penalize even small weaknesses' },
  { value: 'moderately_conservative', label: 'Moderately Conservative', description: 'Value-investor baseline' },
  { value: 'balanced', label: 'Balanced', description: 'Neutral scoring posture' },
  { value: 'moderately_lenient', label: 'Moderately Lenient', description: 'Highlight strengths unless risks are significant' },
  { value: 'very_lenient', label: 'Very Lenient', description: 'Focus on upside even with risks' },
]

const healthAnalysisDepthOptions: Array<{ value: HealthAnalysisDepth; label: string; description: string }> = [
  { value: 'headline_only', label: 'Headline Red Flags', description: 'Only obvious management-highlighted risks' },
  { value: 'key_financial_items', label: 'Key Financial Items', description: 'Margins, cash flow drivers, debt' },
  { value: 'full_footnote_review', label: 'Full Footnote Review', description: 'Leases, covenants, disclosure footnotes' },
  { value: 'accounting_integrity', label: 'Accounting Integrity', description: 'Non-GAAP, adjustments, quality of earnings' },
  { value: 'forensic_deep_dive', label: 'Forensic Deep Dive', description: 'Aggressive accounting, accrual spikes, anomalies' },
]

const healthDisplayOptions: Array<{ value: HealthDisplayStyle; label: string; description: string }> = [
  { value: 'score_only', label: '0–100 Score Only', description: 'Single score headline' },
  { value: 'score_plus_grade', label: 'Score + Letter Grade', description: 'Pair score with an A–F ranking' },
  { value: 'score_plus_traffic_light', label: 'Score + Traffic Light', description: 'Color-coded risk signal' },
  { value: 'score_plus_pillars', label: 'Score + 4 Pillars', description: 'Profitability | Risk | Liquidity | Growth' },
  { value: 'score_with_narrative', label: 'Score + Narrative', description: 'Add a short justification paragraph' },
]

const healthRatingDefaults: HealthRatingFormState = {
  enabled: false,
  framework: 'value_investor_default',
  weighting: 'profitability_margins',
  riskTolerance: 'moderately_conservative',
  analysisDepth: 'key_financial_items',
  displayStyle: 'score_plus_grade',
}

const createDefaultSummaryPreferences = (): SummaryPreferenceFormState => ({
  mode: 'default',
  investorFocus: '',
  focusAreas: ['Financial performance', 'Risk factors'],
  tone: 'objective',
  detailLevel: 'balanced',
  outputStyle: 'narrative',
  targetLength: 650,
  includeHealthScore: true,
  healthRating: { ...healthRatingDefaults },
  complexity: 'intermediate',
  selectedPersona: null,
})

const clampTargetLength = (value: number) => {
  if (Number.isNaN(value)) return 300
  return Math.max(10, Math.min(5000, Math.round(value)))
}

const buildPreferencePayload = (prefs: SummaryPreferenceFormState): FilingSummaryPreferencesPayload | undefined => {
  // Determine if health score should be enabled (either via granular setting or wizard toggle)
  const isHealthEnabled = prefs.healthRating.enabled || prefs.includeHealthScore;

  const buildHealthRating = () =>
    isHealthEnabled
      ? {
        enabled: true,
        framework: prefs.healthRating.framework,
        primary_factor_weighting: prefs.healthRating.weighting,
        risk_tolerance: prefs.healthRating.riskTolerance,
        analysis_depth: prefs.healthRating.analysisDepth,
        display_style: prefs.healthRating.displayStyle,
      }
      : undefined

  // Force health score generation via prompt injection if enabled
  const healthPromptInjection = isHealthEnabled
    ? "\n\nIMPORTANT: You MUST calculate and provide a 'Financial Health Rating' (0-100) based on the analysis. Format it exactly as 'Financial Health Rating: X/100'."
    : "";

  // Inject Persona Prompt if selected
  let personaPrompt = "";
  if (prefs.selectedPersona) {
    const persona = INVESTOR_PERSONAS.find(p => p.id === prefs.selectedPersona);
    if (persona) {
      personaPrompt = `\n\n${persona.prompt}\n\nIMPORTANT: Adopt the persona of ${persona.name} completely. Use their tone, philosophy, and focus areas. Your analysis MUST sound like it was written by ${persona.name}.`;
    }
  }

  const finalInvestorFocus = (prefs.investorFocus.trim() + healthPromptInjection + personaPrompt).trim();

  if (prefs.mode === 'custom') {
    return {
      mode: 'custom',
      investor_focus: finalInvestorFocus || undefined,
      focus_areas: prefs.focusAreas.length ? prefs.focusAreas : undefined,
      tone: prefs.tone,
      detail_level: prefs.detailLevel,
      output_style: prefs.outputStyle,
      target_length: clampTargetLength(prefs.targetLength),
      health_rating: buildHealthRating(),
    }
  }

  if (isHealthEnabled) {
    return {
      mode: 'default',
      health_rating: buildHealthRating(),
      // For default mode, we can't easily inject into investor_focus if it's not used by the backend for default mode,
      // but we can try setting it if the backend respects it.
      investor_focus: (healthPromptInjection + personaPrompt).trim(),
    }
  }

  return undefined
}

const snapshotPreferences = (prefs: SummaryPreferenceFormState): SummaryPreferenceSnapshot => {
  if (prefs.mode === 'default') {
    return {
      mode: 'default',
      healthRating: prefs.healthRating.enabled ? { ...prefs.healthRating } : undefined,
    }
  }
  return {
    mode: 'custom',
    investorFocus: prefs.investorFocus.trim() || undefined,
    focusAreas: prefs.focusAreas.length ? [...prefs.focusAreas] : undefined,
    tone: prefs.tone,
    detailLevel: prefs.detailLevel,
    outputStyle: prefs.outputStyle,
    targetLength: clampTargetLength(prefs.targetLength),
    healthRating: prefs.healthRating.enabled
      ? {
        ...prefs.healthRating,
      }
      : undefined,
  }
}

const isSummaryToneValue = (value: string): value is SummaryTone => toneOptions.some(option => option.value === value)
const isSummaryDetailValue = (value: string): value is SummaryDetailLevel =>
  detailOptions.some(option => option.value === value)
const isSummaryOutputStyleValue = (value: string): value is SummaryOutputStyle =>
  outputStyleOptions.some(option => option.value === value)

const isHealthFrameworkValue = (value: string | undefined): value is HealthFramework =>
  typeof value === 'string' && healthFrameworkOptions.some(option => option.value === value)
const isHealthWeightingValue = (value: string | undefined): value is HealthWeighting =>
  typeof value === 'string' && healthWeightingOptions.some(option => option.value === value)
const isHealthRiskValue = (value: string | undefined): value is HealthRiskTolerance =>
  typeof value === 'string' && healthRiskOptions.some(option => option.value === value)
const isHealthAnalysisDepthValue = (value: string | undefined): value is HealthAnalysisDepth =>
  typeof value === 'string' && healthAnalysisDepthOptions.some(option => option.value === value)
const isHealthDisplayValue = (value: string | undefined): value is HealthDisplayStyle =>
  typeof value === 'string' && healthDisplayOptions.some(option => option.value === value)

const sanitizeStoredPreferences = (stored: StoredSummaryPreferences): SummaryPreferenceFormState => ({
  mode: stored.mode === 'custom' ? 'custom' : 'default',
  investorFocus: stored.investorFocus ?? '',
  focusAreas: Array.isArray(stored.focusAreas) ? stored.focusAreas : [],
  tone: isSummaryToneValue(stored.tone) ? stored.tone : 'objective',
  detailLevel: isSummaryDetailValue(stored.detailLevel) ? stored.detailLevel : 'balanced',
  outputStyle: isSummaryOutputStyleValue(stored.outputStyle) ? stored.outputStyle : 'narrative',
  targetLength: clampTargetLength(stored.targetLength),
  includeHealthScore: stored.healthRating?.enabled ?? false,
  healthRating: {
    enabled: stored.healthRating?.enabled ?? false,
    framework: isHealthFrameworkValue(stored.healthRating?.framework) ? stored.healthRating!.framework : healthRatingDefaults.framework,
    weighting: isHealthWeightingValue(stored.healthRating?.weighting)
      ? stored.healthRating!.weighting
      : healthRatingDefaults.weighting,
    riskTolerance: isHealthRiskValue(stored.healthRating?.riskTolerance)
      ? stored.healthRating!.riskTolerance
      : healthRatingDefaults.riskTolerance,
    analysisDepth: isHealthAnalysisDepthValue(stored.healthRating?.analysisDepth)
      ? stored.healthRating!.analysisDepth
      : healthRatingDefaults.analysisDepth,
    displayStyle: isHealthDisplayValue(stored.healthRating?.displayStyle)
      ? stored.healthRating!.displayStyle
      : healthRatingDefaults.displayStyle,
  },
  complexity: 'intermediate',
  selectedPersona: (stored as any).selectedPersona ?? null,
})

const extractHealthRating = (summaryText: string) => {
  if (!summaryText) {
    return { score: null as number | null }
  }
  const normalized = summaryText.replace(/\*/g, '')
  const ratingRegex = /financial health rating[^0-9]{0,80}(\d{1,3})(?:\s*\/\s*100)?(?:\s*\(([A-F][+-]?)\))?/i
  const match = normalized.match(ratingRegex)
  if (match) {
    const score = Number(match[1])
    const letter = match[2] ?? null
    if (Number.isFinite(score)) {
      return { score, letter }
    }
  }
  const fallback = normalized.match(/(\d{1,2}|100)\s*(?:\/\s*100)?\s*(?:points|score|rating)/i)
  if (fallback) {
    const score = Number(fallback[1])
    if (Number.isFinite(score)) {
      return { score, letter: null }
    }
  }
  return { score: null as number | null }
}

const emitDashboardSync = () => {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('financesum-dashboard-sync'))
  }
}

const mapPersonaSignals = (input?: Record<string, any> | null) => {
  if (!input || typeof input !== 'object') return undefined
  return Object.entries(input).map(([personaId, data]: [string, any]) => ({
    personaId,
    personaName: data?.persona_name ?? personaId,
    stance: data?.stance ?? 'Neutral',
  }))
}

const parseNumericScore = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export default function CompanyPage() {
  const params = useParams()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { user, loading } = useAuth()
  const companyId = params?.id as string
  const [feedback, setFeedback] = useState<string | null>(null)
  const analysisIdParam = searchParams?.get('analysis_id')
  const [selectedTab, setSelectedTab] = useState<'overview' | 'filings' | 'analysis' | 'personas'>(
    analysisIdParam ? 'analysis' : 'overview',
  )
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>([])
  const [currentAnalysisId, setCurrentAnalysisId] = useState<string | null>(analysisIdParam ?? null)
  const [localAnalysisSnapshot, setLocalAnalysisSnapshot] = useState<StoredAnalysisSnapshot | null>(null)
  const [loadingSummaries, setLoadingSummaries] = useState<Record<string, boolean>>({})
  const [summaryProgress, setSummaryProgress] = useState<Record<string, string>>({})
  const [filingSummaries, setFilingSummaries] = useState<Record<string, FilingSummary>>({})

  const LOADING_STEPS = [
    { text: "Initializing AI Agent..." },
    { text: "Reading Filing Content..." },
    { text: "Extracting Financial Data..." },
    { text: "Analyzing Risk Factors..." },
    { text: "Computing Health Score..." },
    { text: "Synthesizing Investor Insights..." },
    { text: "Polishing Output..." },
  ];
  const [selectedFilingForSummary, setSelectedFilingForSummary] = useState<string>('')
  const [summaryPreferences, setSummaryPreferences] = useState<SummaryPreferenceFormState>(() => createDefaultSummaryPreferences())
  const [showCustomLengthInput, setShowCustomLengthInput] = useState(false)
  const [customLengthInput, setCustomLengthInput] = useState(() => String(createDefaultSummaryPreferences().targetLength))
  const [dashboardSavedSummaries, setDashboardSavedSummaries] = useState<Record<string, boolean>>({})
  const summaryCardRef = useRef<HTMLDivElement | null>(null)
  const preferencesHydratedRef = useRef(false)
  const isSummaryGenerating = selectedFilingForSummary ? !!loadingSummaries[selectedFilingForSummary] : false
  const queryClient = useQueryClient()
  const sliderLengthValue = Math.max(50, Math.min(5000, summaryPreferences.targetLength))
  const authPending = loading || !user
  const queriesEnabled = !!companyId && !authPending
  const fallbackTicker = searchParams?.get('ticker') ?? undefined
  const isLocalAnalysisId = currentAnalysisId?.startsWith('summary-') ?? false

  useEffect(() => {
    if (!loading && !user) {
      router.replace('/signup')
    }
  }, [loading, user, router])

  useEffect(() => {
    const stored = DashboardStorage.loadSummaryPreferences()
    if (stored) {
      const sanitized = sanitizeStoredPreferences(stored)
      setSummaryPreferences(sanitized)
      setCustomLengthInput(String(sanitized.targetLength))
    }
    preferencesHydratedRef.current = true
  }, [])

  useEffect(() => {
    if (!preferencesHydratedRef.current) return
    DashboardStorage.saveSummaryPreferences(summaryPreferences)
  }, [summaryPreferences])

  useEffect(() => {
    if (analysisIdParam && analysisIdParam !== currentAnalysisId) {
      setCurrentAnalysisId(analysisIdParam)
      setSelectedTab('analysis')
    } else if (!analysisIdParam && currentAnalysisId) {
      setCurrentAnalysisId(null)
      setSelectedTab(prev => (prev === 'analysis' ? 'overview' : prev))
    }
  }, [analysisIdParam, currentAnalysisId])

  useEffect(() => {
    if (!currentAnalysisId || !isLocalAnalysisId) {
      setLocalAnalysisSnapshot(null)
      return
    }
    const history = DashboardStorage.loadAnalysisHistory()
    const snapshot = history.find(item => item.analysisId === currentAnalysisId) ?? null
    setLocalAnalysisSnapshot(snapshot)
  }, [currentAnalysisId, isLocalAnalysisId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const syncSavedSummaries = () => {
      const saved = DashboardStorage.loadAnalysisHistory().reduce<Record<string, boolean>>((acc, entry) => {
        if (entry.analysisId?.startsWith('summary-')) {
          acc[entry.analysisId.replace('summary-', '')] = true
        }
        return acc
      }, {})
      setDashboardSavedSummaries(saved)
    }
    syncSavedSummaries()
    window.addEventListener('storage', syncSavedSummaries)
    window.addEventListener('focus', syncSavedSummaries)
    return () => {
      window.removeEventListener('storage', syncSavedSummaries)
      window.removeEventListener('focus', syncSavedSummaries)
    }
  }, [])

  const resolveFilingUrl = (path?: string | null) => {
    if (!path) return '#'
    try {
      return new URL(path, API_BASE_URL).toString()
    } catch (error) {
      return path
    }
  }

  const isMountedRef = useRef(true)

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const handleGenerateSummary = async (
    filingId: string,
    preferences?: FilingSummaryPreferencesPayload,
    metadata?: SummaryPreferenceSnapshot,
  ) => {
    setLoadingSummaries(prev => ({ ...prev, [filingId]: true }))
    setSummaryProgress(prev => ({ ...prev, [filingId]: "Initializing..." }))

    let isPolling = true
    const pollProgress = async () => {
      if (!isPolling || !isMountedRef.current) return

      // Pause polling if tab is hidden to prevent network stack suspension errors
      if (document.visibilityState === 'hidden') {
        setTimeout(pollProgress, 2000)
        return
      }

      try {
        const progressRes = await filingsApi.getSummaryProgress(filingId)
        if (progressRes.data?.status && isMountedRef.current) {
          setSummaryProgress(prev => ({ ...prev, [filingId]: progressRes.data.status }))
        }
      } catch (e) {
        // Ignore polling errors, just retry
      }

      if (isPolling && isMountedRef.current) {
        setTimeout(pollProgress, 1000)
      }
    }

    // Start polling loop
    pollProgress()

    try {
      const response = await filingsApi.summarizeFiling(filingId, preferences)

      // Extract health rating if present
      let healthRating: number | undefined;
      if (response.data.health_score !== undefined) {
        healthRating = response.data.health_score;
      } else {
        const extracted = extractHealthRating(response.data.summary);
        if (extracted.score !== null) {
          healthRating = extracted.score;
        }
      }

      if (isMountedRef.current) {
        setFilingSummaries(prev => ({
          ...prev,
          [filingId]: {
            content: response.data.summary,
            metadata: metadata ?? { mode: 'default' },
            healthRating,
          },
        }))
      }
    } catch (error: any) {
      if (isMountedRef.current) {
        const message = error?.response?.data?.detail ?? 'Failed to generate summary'
        alert(message)
      }
    } finally {
      isPolling = false
      // Add a small delay to let the user see the completion state if needed
      if (isMountedRef.current) {
        setTimeout(() => {
          if (isMountedRef.current) {
            setLoadingSummaries(prev => ({ ...prev, [filingId]: false }))
            setSummaryProgress(prev => {
              const next = { ...prev }
              delete next[filingId]
              return next
            })
          }
        }, 1000)
      }
    }
  }

  const handleAddSummaryToDashboard = (filingId: string) => {
    if (!company) return
    const summary = filingSummaries[filingId]
    if (!summary) return
    const filing = filings?.find((item: any) => item.id === filingId)

    const generatedAt = new Date().toISOString()
    const { score: extractedScore } = extractHealthRating(summary.content)
    const ratingInfo = typeof extractedScore === 'number' ? scoreToRating(extractedScore) : null

    DashboardStorage.upsertAnalysisSnapshot({
      analysisId: `summary-${filingId}`,
      generatedAt,
      id: company.id,
      name: company.name,
      ticker: company.ticker,
      exchange: company.exchange,
      sector: company.sector,
      industry: company.industry,
      country: company.country,
      healthScore: extractedScore ?? null,
      scoreBand: ratingInfo?.grade ?? null,
      ratingLabel:
        ratingInfo?.label ?? (summary.metadata?.mode === 'custom' ? 'Custom brief' : 'Quick brief'),
      summaryMd: summary.content,
      summaryPreview: buildSummaryPreview(summary.content),
      filingId: filing?.id ?? filingId,
      filingType: filing?.filing_type ?? null,
      filingDate: filing?.filing_date ?? null,
      source: 'summary',
      selectedPersona: summary.metadata?.selectedPersona ?? null,
    } as any)
    setDashboardSavedSummaries(prev => ({ ...prev, [filingId]: true }))
    emitDashboardSync()
  }

  const scrollToSummaryCard = () => {
    summaryCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const requestSummaryWithPreferences = () => {
    if (!selectedFilingForSummary) return
    const payload = buildPreferencePayload(summaryPreferences)
    const metadata = snapshotPreferences(summaryPreferences)
    handleGenerateSummary(selectedFilingForSummary, payload, metadata)
  }

  const toggleFocusArea = (area: string) => {
    setSummaryPreferences(prev => {
      const exists = prev.focusAreas.includes(area)
      const updatedFocus = exists ? prev.focusAreas.filter(item => item !== area) : [...prev.focusAreas, area]
      return { ...prev, focusAreas: updatedFocus }
    })
  }

  const handleHealthToggle = () =>
    new Promise<void>((resolve) => {
      setSummaryPreferences(prev => ({
        ...prev,
        healthRating: {
          ...prev.healthRating,
          enabled: !prev.healthRating.enabled,
        },
      }))
      setTimeout(resolve, 250)
    })

  const updateHealthRatingField = <K extends keyof HealthRatingFormState>(field: K, value: HealthRatingFormState[K]) => {
    setSummaryPreferences(prev => ({
      ...prev,
      healthRating: {
        ...prev.healthRating,
        [field]: value,
      },
    }))
  }

  const renderHealthGroup = (
    title: string,
    subtitle: string,
    options: Array<{ value: string; label: string; description: string }>,
    selectedValue: string,
    onSelect: (value: string) => void,
  ) => (
    <div className="rounded-xl border border-white/10 bg-white/5 p-4">
      <p className="text-sm font-semibold text-gray-200">{title}</p>
      <p className="text-xs text-gray-400 mb-3">{subtitle}</p>
      <div className="flex flex-wrap gap-2">
        {options.map(option => {
          const active = option.value === selectedValue
          return (
            <Button
              key={option.value}
              type="button"
              onClick={() => onSelect(option.value)}
              color="ghost"
              size="sm"
              asMotion={false}
              className={`min-w-[200px] flex-1 text-left px-4 py-3 rounded-xl border ${active
                ? 'bg-primary-500/15 border-primary-500/60 text-white shadow-premium'
                : 'bg-white/5 border-white/10 text-gray-300 hover:border-primary-500/40'
                }`}
            >
              <p className="font-semibold">{option.label}</p>
              <p className="text-xs text-gray-400">{option.description}</p>
            </Button>
          )
        })}
      </div>
    </div>
  )

  const updateTargetLength = (value: number) => {
    const clamped = clampTargetLength(value)
    setSummaryPreferences(prev => ({ ...prev, targetLength: clamped }))
    setCustomLengthInput(String(clamped))
  }

  const handleCustomLengthApply = () => {
    const numericValue = Number(customLengthInput)
    if (Number.isNaN(numericValue)) {
      alert('Please enter a numeric word count.')
      return
    }
    const parsed = clampTargetLength(numericValue)
    updateTargetLength(parsed)
  }

  // Fetch company data
  const { data: company, isLoading: companyLoading, error: companyError } = useQuery({
    queryKey: ['company', companyId, fallbackTicker],
    queryFn: async () => {
      const response = await companyApi.getCompany(companyId, { ticker: fallbackTicker })
      return response.data
    },
    retry: false,
    enabled: queriesEnabled,
  })

  // Fetch filings
  const { data: filings, isLoading: filingsLoading, error: filingsError } = useQuery({
    queryKey: ['filings', companyId],
    queryFn: async () => {
      const response = await filingsApi.listCompanyFilings(companyId)
      return response.data
    },
    retry: false,
    enabled: queriesEnabled,
  })
  const selectedFilingDetails = useMemo(
    () => filings?.find((f: any) => f.id === selectedFilingForSummary),
    [filings, selectedFilingForSummary],
  )


  // Fetch analyses
  const { data: analyses, refetch: refetchAnalyses, error: analysesError } = useQuery({
    queryKey: ['analyses', companyId],
    queryFn: async () => {
      const response = await analysisApi.listCompanyAnalyses(companyId)
      return response.data
    },
    retry: false,
    enabled: queriesEnabled,
  })

  // Fetch specific analysis
  const { data: currentAnalysis } = useQuery({
    queryKey: ['analysis', currentAnalysisId],
    queryFn: async () => {
      if (!currentAnalysisId || isLocalAnalysisId) return null
      const response = await analysisApi.getAnalysis(currentAnalysisId)
      return response.data
    },
    enabled: !!currentAnalysisId && !authPending && !isLocalAnalysisId,
  })

  const persistAnalysisSnapshot = useCallback(
    (analysis: any | null, overrideId?: string | null) => {
      if (!analysis || !company?.id) return
      const identifier = overrideId ?? analysis.id ?? analysis.analysisId
      if (!identifier) return
      const personaSignals = mapPersonaSignals(analysis.investor_persona_summaries)
      const rawSummary = analysis.summary_md ?? analysis.summaryMd ?? null
      const healthScore =
        parseNumericScore(analysis.health_score) ?? parseNumericScore(analysis.healthScore)
      const generatedAt =
        analysis.analysis_date ||
        analysis.analysis_datetime ||
        analysis.created_at ||
        analysis.updated_at ||
        new Date().toISOString()

      DashboardStorage.upsertAnalysisSnapshot({
        analysisId: String(identifier),
        generatedAt,
        id: company.id,
        name: company.name,
        ticker: company.ticker,
        exchange: company.exchange,
        sector: company.sector,
        industry: company.industry,
        country: company.country,
        healthScore,
        scoreBand: analysis.score_band ?? analysis.scoreBand ?? null,
        ratingLabel: scoreToRating(healthScore).label,
        summaryMd: rawSummary,
        summaryPreview: buildSummaryPreview(rawSummary),
        personaSignals,
        filingId: analysis.primary_filing_id ?? analysis.filing_id ?? analysis.filingId ?? null,
        filingType: analysis.primary_filing_type ?? analysis.filing_type ?? analysis.filingType ?? null,
        filingDate:
          analysis.primary_filing_date ??
          analysis.filing_date ??
          analysis.filingDate ??
          null,
        source: 'analysis',
      })
      emitDashboardSync()
    },
    [company],
  )

  // Fetch filings mutation
  const fetchFilingsMutation = useMutation({
    mutationFn: () => filingsApi.fetch(companyId),
    onSuccess: (response) => {
      const message = response?.data?.message ?? 'Started fetching filings. This may take a few minutes.'
      setFeedback(null)
      alert(message)
      queryClient.invalidateQueries({ queryKey: ['filings', companyId] })
    },
    onError: (error: any) => {
      const message = error?.response?.data?.detail ?? 'Unable to fetch filings. Please try again later.'
      setFeedback(message)
    },
  })

  // Run analysis mutation
  const runAnalysisMutation = useMutation({
    mutationFn: () => analysisApi.run(companyId, undefined, selectedPersonas),
    onSuccess: async (response) => {
      setCurrentAnalysisId(response.data.analysis_id)
      await refetchAnalyses()
      const message = response?.data?.message ?? 'Analysis completed!'
      alert(message)
      queryClient.invalidateQueries({ queryKey: ['analyses', companyId] })
    },
    onError: (error: any) => {
      const message = error?.response?.data?.detail ?? 'Unable to start analysis. Please try again later.'
      setFeedback(message)
    },
  })

  const computedError = useMemo(() => {
    const sources = [companyError, filingsError, analysesError]
    for (const source of sources) {
      const detail = (source as any)?.response?.data?.detail
      if (detail) return detail
    }
    return null
  }, [companyError, filingsError, analysesError])

  const infoMessage = feedback ?? computedError

  const latestAnalysis = useMemo(() => {
    if (!analyses || analyses.length === 0) return null
    return analyses[0]
  }, [analyses])

  const analysisFromSnapshot = useMemo(() => {
    if (!localAnalysisSnapshot) return null
    return {
      id: localAnalysisSnapshot.analysisId,
      summary_md: localAnalysisSnapshot.summaryMd ?? localAnalysisSnapshot.summaryPreview ?? null,
      investor_persona_summaries: null,
    }
  }, [localAnalysisSnapshot])

  const analysisToDisplay = useMemo(() => {
    if (currentAnalysis) return currentAnalysis
    if (analysisFromSnapshot) return analysisFromSnapshot
    return latestAnalysis
  }, [currentAnalysis, analysisFromSnapshot, latestAnalysis])

  const summaryMarkdownComponents = useMemo(
    () => ({
      h1: ({ node, ...props }: any) => <p className="font-mono font-semibold text-base mt-4 mb-2" {...props} />,
      h2: ({ node, ...props }: any) => <p className="font-mono font-semibold text-base mt-4 mb-2" {...props} />,
      h3: ({ node, ...props }: any) => <p className="font-mono font-semibold text-base mt-3 mb-1" {...props} />,
      h4: ({ node, ...props }: any) => <p className="font-mono font-semibold text-base mt-3 mb-1" {...props} />,
      h5: ({ node, ...props }: any) => <p className="font-mono font-semibold text-base mt-2 mb-1" {...props} />,
      h6: ({ node, ...props }: any) => <p className="font-mono font-semibold text-base mt-2 mb-1" {...props} />,
      p: ({ node, ...props }: any) => <p className="font-mono text-base leading-relaxed mb-3 whitespace-pre-wrap" {...props} />,
      strong: ({ node, ...props }: any) => <span className="font-semibold" {...props} />,
      em: ({ node, ...props }: any) => <span className="italic" {...props} />,
      ul: ({ node, ordered, ...props }: any) => (
        <ul className="list-disc pl-5 space-y-1 font-mono text-base leading-relaxed mb-3" {...props} />
      ),
      ol: ({ node, ordered, ...props }: any) => (
        <ol className="list-decimal pl-5 space-y-1 font-mono text-base leading-relaxed mb-3" {...props} />
      ),
      li: ({ node, ...props }: any) => <li className="font-mono text-base leading-relaxed" {...props} />,
      code: ({ inline, className, children, ...props }: any) => (
        <code className="font-mono text-base bg-zinc-900/5 dark:bg-white/10 rounded px-1" {...props}>
          {children}
        </code>
      ),
      blockquote: ({ node, ...props }: any) => (
        <blockquote className="border-l-4 border-gray-300 dark:border-gray-700 pl-4 text-base leading-relaxed font-mono mb-3" {...props} />
      ),
    }),
    [],
  )

  const handleManualDashboardUpdate = useCallback(() => {
    const candidate = currentAnalysis ?? latestAnalysis ?? analysisFromSnapshot
    if (!candidate) {
      alert('Run or open an analysis before updating the dashboard.')
      return
    }
    const overrideId =
      (currentAnalysis && currentAnalysis.id) ||
      (latestAnalysis && latestAnalysis.id) ||
      currentAnalysisId ||
      (typeof (candidate as any).analysisId === 'string' ? (candidate as any).analysisId : null)
    persistAnalysisSnapshot(candidate, overrideId ?? null)
    alert('Dashboard updated with the latest analysis.')
  }, [currentAnalysis, latestAnalysis, analysisFromSnapshot, currentAnalysisId, persistAnalysisSnapshot])

  useEffect(() => {
    if (!company?.id) return
    DashboardStorage.upsertRecentCompany({
      id: company.id,
      name: company.name,
      ticker: company.ticker,
      exchange: company.exchange,
      sector: company.sector,
      industry: company.industry,
      country: company.country,
    })
  }, [company])

  useEffect(() => {
    if (!latestAnalysis) return
    persistAnalysisSnapshot(latestAnalysis)
  }, [latestAnalysis, persistAnalysisSnapshot])

  useEffect(() => {
    if (isLocalAnalysisId) return
    if (!currentAnalysis || !currentAnalysisId) return
    persistAnalysisSnapshot(currentAnalysis, currentAnalysisId)
  }, [currentAnalysis, currentAnalysisId, isLocalAnalysisId, persistAnalysisSnapshot])

  if (authPending) {
    return (
      <div className="h-screen w-full overflow-hidden bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900 flex items-center justify-center">
        <div className="h-full text-center">
          <div className="spinner mx-auto mb-4"></div>
          <p className="text-gray-300 text-xl">Checking your session...</p>
        </div>
      </div>
    )
  }

  if (companyLoading) {
    return (
      <div className="h-screen w-full overflow-hidden bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900 flex items-center justify-center">
        <div className="h-full text-center">
          <div className="spinner mx-auto mb-4"></div>
          <p className="text-gray-300 text-xl">Loading company data...</p>
        </div>
      </div>
    )
  }

  if (!company) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900">
        <Navbar />
        <div className="flex items-center justify-center h-96">
          <div className="card-premium bg-gradient-to-br from-red-900/30 to-red-800/30 border-red-500/40 text-center max-w-md">
            <svg className="w-16 h-16 text-red-400 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <h2 className="text-2xl font-bold text-red-300 mb-2">Error Loading Company</h2>
            <p className="text-red-200">
              {computedError || 'Company not found'}
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-full overflow-hidden bg-gray-50 dark:bg-black font-sans text-black dark:text-white selection:bg-yellow-300 selection:text-black flex flex-col">
      <Navbar />

      <div className="flex-1 overflow-y-auto">
        <main className="container mx-auto px-4 py-8 max-w-7xl space-y-8">
          {/* Header Section */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 md:p-8 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]"
          >
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-6">
              <div className="flex items-center gap-6">
                <div className="w-20 h-20 bg-white dark:bg-black border-2 border-black dark:border-white flex items-center justify-center shrink-0 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
                  {company?.ticker ? (
                    <img
                      src={`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/companies/logo/${company.ticker}`}
                      alt={`${company.name} logo`}
                      className="w-12 h-12 object-contain"
                      onError={(e) => {
                        e.currentTarget.style.display = 'none'
                        e.currentTarget.nextElementSibling?.classList.remove('hidden')
                      }}
                    />
                  ) : null}
                  <span className={`text-2xl font-black ${company?.ticker ? 'hidden' : ''}`}>
                    {company?.ticker?.slice(0, 2) ?? 'CO'}
                  </span>
                </div>
                <div>
                  <h1 className="text-3xl md:text-4xl font-black uppercase tracking-tight mb-2">
                    {company?.name ?? 'Loading...'}
                  </h1>
                  <div className="flex flex-wrap gap-3">
                    {company?.ticker && (
                      <span className="px-3 py-1 bg-blue-600 text-white text-xs font-bold uppercase border border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)]">
                        {company.ticker}
                      </span>
                    )}
                    {company?.exchange && (
                      <span className="px-3 py-1 bg-white dark:bg-black text-black dark:text-white text-xs font-bold uppercase border border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)]">
                        {company.exchange}
                      </span>
                    )}
                    {company?.sector && (
                      <span className="px-3 py-1 bg-yellow-400 text-black text-xs font-bold uppercase border border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)]">
                        {company.sector}
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {latestAnalysis?.health_score != null && (
                <div className="self-end md:self-center">
                  <HealthScoreBadge
                    score={latestAnalysis.health_score}
                    band={scoreToRating(latestAnalysis.health_score).label}
                  />
                </div>
              )}
            </div>
          </motion.div>

          {/* Navigation Tabs */}
          <div className="flex flex-wrap gap-4 border-b-4 border-black dark:border-white pb-1">
            {['overview', 'filings'].map((tab) => (
              <button
                key={tab}
                onClick={() => setSelectedTab(tab as any)}
                className={`
                    px-6 py-3 font-bold uppercase tracking-wider text-sm transition-all duration-200 border-2 border-black dark:border-white
                    ${selectedTab === tab
                    ? 'bg-black text-white dark:bg-white dark:text-black shadow-[4px_4px_0px_0px_rgba(128,128,128,1)] translate-y-[-2px] translate-x-[-2px]'
                    : 'bg-white dark:bg-black text-black dark:text-white hover:bg-gray-100 dark:hover:bg-zinc-900 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)]'}
                  `}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Info Message */}
          {infoMessage && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              className="bg-red-100 dark:bg-red-900/30 border-2 border-red-600 text-red-700 dark:text-red-300 px-6 py-4 font-bold shadow-[4px_4px_0px_0px_rgba(220,38,38,1)]"
            >
              {infoMessage}
            </motion.div>
          )}

          {/* Tab Content */}
          <div className="min-h-[400px]">
            {selectedTab === 'overview' && (
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                className="space-y-8"
              >
                {/* Quick Actions */}
                <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
                  <h2 className="text-xl font-black uppercase mb-6 flex items-center gap-3">
                    <span className="w-4 h-4 bg-black dark:bg-white"></span>
                    Quick Actions
                  </h2>
                  <div className="flex flex-wrap gap-4">
                    <BrutalButton
                      onClick={() => fetchFilingsMutation.mutate()}
                      disabled={fetchFilingsMutation.isPending}
                      variant="unapologetic"
                    >
                      {fetchFilingsMutation.isPending ? 'Fetching...' : 'Fetch Latest Filings'}
                    </BrutalButton>


                  </div>
                </div>

                {/* Health Analysis Section */}
                {latestAnalysis?.health_score != null && (
                  <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
                    <h2 className="text-xl font-black uppercase mb-6 flex items-center gap-3">
                      <span className="w-4 h-4 bg-red-500"></span>
                      Health Analysis
                    </h2>
                    <div className="flex flex-col md:flex-row gap-8 items-center">
                      <div className="shrink-0">
                        <HealthScoreBadge
                          score={latestAnalysis.health_score}
                          band={scoreToRating(latestAnalysis.health_score).label}
                        />
                      </div>
                      <div className="flex-1">
                        <h3 className="font-bold uppercase text-lg mb-2">
                          {scoreToRating(latestAnalysis.health_score).label} Health
                        </h3>
                        <p className="font-mono text-sm text-gray-600 dark:text-gray-300">
                          {latestAnalysis.summary || "No health summary available yet. Run a full analysis to generate detailed health insights."}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* AI Summary Section */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                  {/* Summary Generation Card */}
                  <div className="lg:col-span-1 bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] h-fit sticky top-4">
                    <h3 className="text-lg font-black uppercase mb-6 flex items-center gap-2">
                      <span className="w-3 h-3 bg-blue-600"></span>
                      Generate Brief
                    </h3>

                    <div className="space-y-6">
                      <SummaryWizard
                        filings={filings}
                        selectedFilingId={selectedFilingForSummary}
                        onFilingChange={setSelectedFilingForSummary}
                        preferences={summaryPreferences}
                        onPreferencesChange={setSummaryPreferences}
                        onGenerate={requestSummaryWithPreferences}
                        isGenerating={isSummaryGenerating}
                      />
                    </div>
                  </div>

                  {/* Generated Summaries List */}
                  <div className="lg:col-span-2 space-y-6">
                    {Object.entries(filingSummaries).length === 0 && Object.keys(dashboardSavedSummaries).length === 0 ? (
                      <div className="bg-gray-50 dark:bg-zinc-900/50 border-2 border-dashed border-gray-300 dark:border-gray-700 p-12 text-center">
                        <div className="w-16 h-16 bg-gray-200 dark:bg-zinc-800 mx-auto mb-4 flex items-center justify-center border-2 border-gray-400 dark:border-gray-600">
                          <span className="text-2xl">📄</span>
                        </div>
                        <h3 className="text-lg font-bold uppercase text-gray-500 dark:text-gray-400">No summaries yet</h3>
                        <p className="text-sm text-gray-400 mt-2">Select a filing to generate an AI-powered brief.</p>
                      </div>
                    ) : (
                      <div className="space-y-6">
                        {Object.entries(filingSummaries).map(([fid, summary]) => (
                          <motion.div
                            key={fid}
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)]"
                          >
                            <div className="flex justify-between items-start mb-4 border-b-2 border-gray-100 dark:border-gray-800 pb-4">
                              <div>
                                <div className="flex items-center gap-3">
                                  <h4 className="font-black uppercase text-lg">AI Brief</h4>
                                  {summary.healthRating && (
                                    <span className={cn(
                                      "px-2 py-0.5 text-xs font-bold uppercase border border-black dark:border-white",
                                      summary.healthRating >= 70 ? "bg-green-400 text-black" :
                                        summary.healthRating >= 40 ? "bg-yellow-400 text-black" :
                                          "bg-red-400 text-black"
                                    )}>
                                      Health: {summary.healthRating}/100
                                    </span>
                                  )}
                                </div>
                                <p className="text-xs font-mono text-gray-500 mt-1">
                                  Filing ID: {fid.slice(0, 8)}...
                                </p>
                              </div>
                              <div className="flex gap-2">
                                <BrutalButton
                                  onClick={() => handleAddSummaryToDashboard(fid)}
                                  disabled={dashboardSavedSummaries[fid]}
                                  className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-green-400 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)] disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                  {dashboardSavedSummaries[fid] ? 'Saved' : 'Save to Dashboard'}
                                </BrutalButton>
                                <BrutalButton
                                  onClick={() => {
                                    const newSummaries = { ...filingSummaries }
                                    delete newSummaries[fid]
                                    setFilingSummaries(newSummaries)
                                  }}
                                  className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-red-400 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)]"
                                >
                                  Dismiss
                                </BrutalButton>
                              </div>
                            </div>
                            <div className="prose dark:prose-invert max-w-none font-mono text-sm max-h-[600px] overflow-y-auto pr-2 custom-scrollbar">
                              <EnhancedSummary
                                content={summary.content}
                                persona={summary.metadata?.selectedPersona ? INVESTOR_PERSONAS.find(p => p.id === summary.metadata.selectedPersona) : null}
                              />
                            </div>
                          </motion.div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </motion.div>
            )}

            {selectedTab === 'filings' && (
              <motion.div
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]"
              >
                <h2 className="text-xl font-black uppercase mb-6 flex items-center gap-3">
                  <span className="w-4 h-4 bg-purple-600"></span>
                  Recent Filings
                </h2>

                {filingsLoading ? (
                  <div className="text-center py-12 font-mono animate-pulse">Loading filings data...</div>
                ) : !filings?.length ? (
                  <div className="text-center py-12 border-2 border-dashed border-gray-300 dark:border-gray-700">
                    No filings found.
                  </div>
                ) : (
                  <AnimatedList
                    items={filings}
                    renderItem={(filing: any, index: number, isSelected: boolean) => (
                      <div className="flex flex-col md:flex-row justify-between gap-4 w-full">
                        <div>
                          <div className="flex items-center gap-3 mb-2">
                            <span className="font-black text-lg uppercase">{filing.filing_type}</span>
                            <span className={`px-2 py-0.5 text-[10px] font-bold uppercase border border-black dark:border-white ${filing.status === 'Completed' ? 'bg-green-400 text-black' : 'bg-yellow-400 text-black'
                              }`}>
                              {filing.status || 'Unknown'}
                            </span>
                            {loadingSummaries[filing.id] && (
                              <MultiStepLoader
                                loadingStates={LOADING_STEPS}
                                loading={loadingSummaries[filing.id]}
                                duration={2000}
                                loop={false}
                                currentStep={Math.max(0, LOADING_STEPS.findIndex(s => s.text === (summaryProgress[filing.id] || "Initializing AI Agent...")))}
                              />
                            )}
                          </div>
                          <p className="font-mono text-xs text-gray-600 dark:text-gray-400">
                            {new Date(filing.filing_date).toLocaleDateString()}
                            {filing.period && filing.period !== 'N/A' && (
                              <> • {filing.period}</>
                            )}
                          </p>
                        </div>
                        <div className="flex items-center gap-3">
                          <a
                            href={resolveFilingUrl(filing.filing_url)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-4 py-2 text-xs font-bold uppercase border-2 border-black dark:border-white hover:bg-black hover:text-white dark:hover:bg-white dark:hover:text-black transition-colors"
                            onClick={(e) => e.stopPropagation()}
                          >
                            View Source
                          </a>
                          <BrutalButton
                            variant="brutal-stacked"
                            onClick={(e) => {
                              e.stopPropagation()
                              setSelectedTab('overview')
                              setSelectedFilingForSummary(filing.id)
                              window.scrollTo({ top: 0, behavior: 'smooth' })
                            }}
                          >
                            Analyze
                          </BrutalButton>
                        </div>
                      </div>
                    )}
                    itemClassName="bg-white dark:bg-black border-2 border-black dark:border-white p-4 hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] transition-all duration-200"
                  />
                )}
              </motion.div>
            )}

            {selectedTab === 'analysis' && (
              <motion.div
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                className="space-y-8"
              >
                <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
                  <div className="flex justify-between items-center mb-8">
                    <h2 className="text-xl font-black uppercase flex items-center gap-3">
                      <span className="w-4 h-4 bg-emerald-600"></span>
                      Financial Analysis
                    </h2>
                    {analysisToDisplay && (
                      <BrutalButton
                        onClick={handleManualDashboardUpdate}
                        variant="outline-rounded"
                        className="text-xs"
                      >
                        Update Dashboard
                      </BrutalButton>
                    )}
                  </div>

                  {!analysisToDisplay ? (
                    <div className="text-center py-12 border-2 border-dashed border-gray-300 dark:border-gray-700">
                      <p className="font-bold uppercase text-gray-500">No analysis available</p>
                      <p className="text-sm text-gray-400 mt-2">Run an analysis from the Overview tab to see insights.</p>
                    </div>
                  ) : (
                    <div className="space-y-8">
                      {/* Financial Charts */}
                      {analysisToDisplay.financial_ratios && (
                        <FinancialCharts ratios={analysisToDisplay.financial_ratios} />
                      )}

                      {/* Analysis Content */}
                      <div className="border-t-2 border-gray-100 dark:border-gray-800 pt-8">
                        <ReactMarkdown
                          className="max-w-none text-base leading-relaxed font-mono space-y-2"
                          components={summaryMarkdownComponents as any}
                        >
                          {analysisToDisplay.summary_md || analysisToDisplay.content || ''}
                        </ReactMarkdown>
                      </div>
                    </div>
                  )}
                </div>
              </motion.div>
            )}

            {selectedTab === 'personas' && (
              <motion.div
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                className="space-y-8"
              >
                <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
                  <h2 className="text-xl font-black uppercase mb-6 flex items-center gap-3">
                    <span className="w-4 h-4 bg-amber-500"></span>
                    Investor Personas
                  </h2>

                  <div className="mb-8">
                    <PersonaSelector
                      selectedPersonas={selectedPersonas}
                      onSelectionChange={setSelectedPersonas}
                    />
                  </div>

                  {analysisToDisplay?.investor_persona_summaries ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                      {Object.entries(analysisToDisplay.investor_persona_summaries).map(([persona, data]: [string, any]) => (
                        <div
                          key={persona}
                          className="bg-white dark:bg-black border-2 border-black dark:border-white p-6 hover:shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] transition-all duration-200"
                        >
                          <div className="flex justify-between items-start mb-4">
                            <h3 className="font-black uppercase text-lg">{data.persona_name || persona}</h3>
                            <span className={`px-3 py-1 text-xs font-bold uppercase border border-black dark:border-white ${data.stance === 'Bullish' ? 'bg-green-400 text-black' :
                              data.stance === 'Bearish' ? 'bg-red-400 text-black' :
                                'bg-gray-200 text-black'
                              }`}>
                              {data.stance}
                            </span>
                          </div>
                          <p className="font-mono text-sm mb-4">{data.summary}</p>
                          <div className="space-y-2">
                            {data.key_points?.map((point: string, i: number) => (
                              <div key={i} className="flex items-start gap-2 text-xs font-bold">
                                <span className="text-blue-600">→</span>
                                <span>{point}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-12 border-2 border-dashed border-gray-300 dark:border-gray-700">
                      <p className="font-bold uppercase text-gray-500">No persona analysis yet</p>
                      <p className="text-sm text-gray-400 mt-2">Select personas and run analysis to see their perspectives.</p>
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
