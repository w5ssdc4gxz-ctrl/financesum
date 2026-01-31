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
import { companyApi, filingsApi, analysisApi, FilingSummaryPreferencesPayload } from '@/lib/api-client'
import DashboardStorage, { StoredAnalysisSnapshot, StoredSummaryPreferences } from '@/lib/dashboard-storage'
import { buildSummaryPreview, scoreToRating } from '@/lib/analysis-insights'
import { Button } from '@/components/base/buttons/button'
import { Button as StatefulButton } from '@/components/ui/stateful-button'
import { useAuth } from '@/contexts/AuthContext'
import { BrutalButton } from '@/components/ui/BrutalButton'
import SummaryWizard, { INVESTOR_PERSONAS } from '@/components/SummaryWizard'
import { Modal } from '@/components/ui/modal'
import { cn } from '@/lib/utils'
import { MultiStepLoader, type SummaryProgressPayload } from '@/components/ui/multi-step-loader'

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

type SummaryErrorModalState = {
  isOpen: boolean
  title: string
  message: string
  showExtensionTip: boolean
}

type HealthComponentScores = {
  financial_performance?: number
  profitability?: number
  leverage?: number
  liquidity?: number
  cash_flow?: number
  governance?: number
  growth?: number
}

type HealthComponentDescriptions = Partial<Record<keyof HealthComponentScores, string>>

type ChartDataPeriod = {
  revenue?: number | null
  operating_income?: number | null
  net_income?: number | null
  free_cash_flow?: number | null
  operating_margin?: number | null
  net_margin?: number | null
  gross_margin?: number | null
}

type ChartData = {
  current_period: ChartDataPeriod
  prior_period?: ChartDataPeriod | null
  period_type: 'quarterly' | 'annual'
  current_label: string
  prior_label: string
}

type FilingSummary = {
  content: string
  metadata: SummaryPreferenceSnapshot
  generatedAt: string
  companyCountry?: string | null
  healthRating?: number
  healthComponents?: HealthComponentScores
  healthComponentWeights?: Partial<Record<keyof HealthComponentScores, number>>  // Dynamic weights from user settings
  healthComponentDescriptions?: HealthComponentDescriptions  // Dynamic descriptions based on weighting
  healthComponentMetrics?: Partial<Record<keyof HealthComponentScores, string>>  // Actual metric values for display
  chartData?: ChartData | null  // Chart data for visualizations
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
  { value: 'score_plus_grade', label: 'Score + Rating Label', description: 'Pair score with a Very Healthy/Healthy/Watch/At Risk label' },
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

const DEFAULT_TARGET_LENGTH_WORDS = 650

const createDefaultSummaryPreferences = (): SummaryPreferenceFormState => ({
  mode: 'custom',
  investorFocus: '',
  focusAreas: ['Financial performance', 'Risk factors'],
  tone: 'objective',
  detailLevel: 'balanced',
  outputStyle: 'narrative',
  targetLength: DEFAULT_TARGET_LENGTH_WORDS,
  includeHealthScore: true,
  healthRating: { ...healthRatingDefaults },
  complexity: 'intermediate',
  selectedPersona: null,
})

const clampTargetLength = (value: number | null | undefined) => {
  const numericValue = typeof value === 'number' && Number.isFinite(value) ? value : DEFAULT_TARGET_LENGTH_WORDS
  return Math.max(1, Math.min(3000, Math.round(numericValue)))
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

  // Inject Persona Prompt if selected
  let personaPrompt = "";
  if (prefs.selectedPersona) {
    const persona = INVESTOR_PERSONAS.find(p => p.id === prefs.selectedPersona);
    if (persona) {
      personaPrompt = `\n\n${persona.prompt}\n\nIMPORTANT: Adopt the persona of ${persona.name} completely. Use their tone, philosophy, and focus areas. Your analysis MUST sound like it was written by ${persona.name}.`;
    }
  }

  const finalInvestorFocus = (prefs.investorFocus.trim() + personaPrompt).trim();

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

const snapshotPreferences = (prefs: SummaryPreferenceFormState): SummaryPreferenceSnapshot => {
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
  mode: 'custom',
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

const getFilingColor = (type?: string) => {
  const t = type?.toUpperCase() || ''
  if (t.includes('10-K')) return 'bg-emerald-500'
  if (t.includes('10-Q')) return 'bg-blue-500'
  if (t.includes('8-K')) return 'bg-rose-500'
  return 'bg-zinc-500'
}

export default function CompanyPage() {
  const params = useParams()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { user, loading } = useAuth()
  const userId = user?.id ?? null
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
  const [activeSummaryProgressFilingId, setActiveSummaryProgressFilingId] = useState<string | null>(null)
  const [summaryProgress, setSummaryProgress] = useState<Record<string, SummaryProgressPayload>>({})
  const [filingSummaries, setFilingSummaries] = useState<Record<string, FilingSummary>>({})
  const [summaryErrorModal, setSummaryErrorModal] = useState<SummaryErrorModalState>({
    isOpen: false,
    title: '',
    message: '',
    showExtensionTip: false,
  })

  const [selectedFilingForSummary, setSelectedFilingForSummary] = useState<string>('')
  const [summaryPreferences, setSummaryPreferences] = useState<SummaryPreferenceFormState>(() => createDefaultSummaryPreferences())
  const [showCustomLengthInput, setShowCustomLengthInput] = useState(false)
  const [customLengthInput, setCustomLengthInput] = useState(() => String(createDefaultSummaryPreferences().targetLength))
  const [dashboardSavedSummaries, setDashboardSavedSummaries] = useState<Record<string, boolean>>({})
  const [showSavedPopup, setShowSavedPopup] = useState(false)
  const [copiedSummaries, setCopiedSummaries] = useState<Record<string, boolean>>({})
  const [exportingSummaries, setExportingSummaries] = useState<Record<string, 'pdf' | 'docx'>>({})
  const [copiedAnalysis, setCopiedAnalysis] = useState(false)
  const [exportingAnalysis, setExportingAnalysis] = useState<null | 'pdf' | 'docx'>(null)
  const summaryCardRef = useRef<HTMLDivElement | null>(null)
  const preferencesHydratedRef = useRef(false)
  const isSummaryGenerating = selectedFilingForSummary ? !!loadingSummaries[selectedFilingForSummary] : false
  const queryClient = useQueryClient()
  const sliderLengthValue = Math.max(1, Math.min(3000, summaryPreferences.targetLength))
  const authPending = loading || !user
  const queriesEnabled = !!companyId && !authPending
  const fallbackTicker = searchParams?.get('ticker') ?? undefined
  const isLocalAnalysisId = currentAnalysisId?.startsWith('summary-') ?? false

  const openSummaryError = useCallback((error: any, fallbackMessage: string) => {
    const responseMessage = error?.response?.data?.detail
    const message = String(responseMessage || error?.message || fallbackMessage || 'Failed to generate summary')
    const status = error?.response?.status
    const code = String(error?.code || '')
    const raw = `${String(error?.message || '')} ${String(error?.toString?.() || '')}`.toLowerCase()

    const looksLikeNetworkOrClientBlock =
      !status &&
      (code.toLowerCase().includes('network') ||
        raw.includes('network error') ||
        raw.includes('failed to fetch') ||
        raw.includes('err_blocked_by_client') ||
        raw.includes('net::err_'))

    setSummaryErrorModal({
      isOpen: true,
      title: 'Couldn’t generate summary',
      message,
      showExtensionTip: looksLikeNetworkOrClientBlock,
    })
  }, [])

  useEffect(() => {
    if (!loading && !user) {
      router.replace('/signup')
    }
  }, [loading, user, router])

  useEffect(() => {
    if (!userId) return
    const stored = DashboardStorage.loadSummaryPreferences(userId)
    if (stored) {
      const sanitized = sanitizeStoredPreferences(stored)
      setSummaryPreferences(sanitized)
      setCustomLengthInput(String(sanitized.targetLength))
    }
    preferencesHydratedRef.current = true
  }, [userId])

  useEffect(() => {
    if (!preferencesHydratedRef.current) return
    if (!userId) return
    DashboardStorage.saveSummaryPreferences(summaryPreferences, userId)
  }, [summaryPreferences, userId])

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
    if (!currentAnalysisId || !isLocalAnalysisId || !userId) {
      setLocalAnalysisSnapshot(null)
      return
    }
    const history = DashboardStorage.loadAnalysisHistory(userId)
    const snapshot = history.find(item => item.analysisId === currentAnalysisId) ?? null
    setLocalAnalysisSnapshot(snapshot)
  }, [currentAnalysisId, isLocalAnalysisId, userId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!userId) {
      setDashboardSavedSummaries({})
      return
    }
    const syncSavedSummaries = () => {
      const saved = DashboardStorage.loadAnalysisHistory(userId).reduce<Record<string, boolean>>((acc, entry) => {
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
  }, [userId])

  const resolveFilingUrl = (path?: string | null) => {
    if (!path) return '#'

    const trimmed = path.trim()
    if (!trimmed) return '#'

    if (/^https?:\/\//i.test(trimmed)) {
      return trimmed
    }

    const proxyBase = (process.env.NEXT_PUBLIC_API_PROXY_BASE ?? '/api/backend').trim()
    const normalizedProxyBase = (proxyBase || '/api/backend').replace(/\/+$/, '')
    const normalizedPath = trimmed.startsWith('/') ? trimmed : `/${trimmed}`

    return `${normalizedProxyBase}${normalizedPath}`
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
    setActiveSummaryProgressFilingId(filingId)
    setLoadingSummaries(prev => ({ ...prev, [filingId]: true }))
    setSummaryProgress(prev => ({
      ...prev,
      [filingId]: { status: 'Initializing...', percent: 0, percent_exact: 0, eta_seconds: null },
    }))

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
        if (isMountedRef.current && progressRes.data) {
          const status = String(progressRes.data.status ?? 'Initializing...')
          const percent = typeof progressRes.data.percent === 'number' ? progressRes.data.percent : 0
          const percent_exact =
            typeof progressRes.data.percent_exact === 'number' ? progressRes.data.percent_exact : percent
          const eta_seconds = typeof progressRes.data.eta_seconds === 'number' ? progressRes.data.eta_seconds : null
          setSummaryProgress(prev => ({
            ...prev,
            [filingId]: { status, percent, percent_exact, eta_seconds },
          }))
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
      if (isMountedRef.current) {
        setSummaryProgress(prev => ({
          ...prev,
          [filingId]: { status: 'Complete', percent: 100, percent_exact: 100, eta_seconds: 0 },
        }))
      }

      // Extract health rating if present
      let healthRating: number | undefined;
      let healthComponents: HealthComponentScores | undefined;

      if (response.data.health_score !== undefined) {
        healthRating = response.data.health_score;
      } else {
        const extracted = extractHealthRating(response.data.summary);
        if (extracted.score !== null) {
          healthRating = extracted.score;
        }
      }

      if (response.data.health_components) {
        healthComponents = response.data.health_components;
      }

      // Capture dynamic weights, descriptions, and metrics from user settings
      const healthComponentWeights = response.data.health_component_weights;
      const healthComponentDescriptions = response.data.health_component_descriptions;
      const healthComponentMetrics = response.data.health_component_metrics;
      const companyCountry = response.data.company_country ?? null
      const chartData = response.data.chart_data ?? null

      if (isMountedRef.current) {
        const generatedAt = new Date().toISOString()
        setFilingSummaries(prev => ({
          ...prev,
          [filingId]: {
            content: response.data.summary,
            metadata: metadata ?? { mode: 'custom' },
            generatedAt,
            companyCountry,
            healthRating,
            healthComponents,
            healthComponentWeights,
            healthComponentDescriptions,
            healthComponentMetrics,
            chartData,
          },
        }))
      }
    } catch (error: any) {
      if (isMountedRef.current) {
        openSummaryError(error, 'Failed to generate summary')
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
            setActiveSummaryProgressFilingId(prev => (prev === filingId ? null : prev))
          }
        }, 1000)
      }
    }
  }

  const handleAddSummaryToDashboard = (filingId: string) => {
    if (!company || !userId) return
    const summary = filingSummaries[filingId]
    if (!summary) return
    const filing = filings?.find((item: any) => item.id === filingId)

    const generatedAt = new Date().toISOString()
    // Prioritize API-provided healthRating over text extraction
    const healthScore = summary.healthRating ?? extractHealthRating(summary.content).score
    const ratingInfo = typeof healthScore === 'number' ? scoreToRating(healthScore) : null

    DashboardStorage.upsertAnalysisSnapshot({
      analysisId: `summary-${filingId}`,
      generatedAt,
      id: company.id,
      name: company.name,
      ticker: company.ticker,
      exchange: company.exchange,
      sector: company.sector,
      industry: company.industry,
      country: summary.companyCountry ?? company.country,
      healthScore: healthScore ?? null,
      scoreBand: ratingInfo?.grade ?? null,
      ratingLabel:
        ratingInfo?.label ?? 'Custom brief',
      summaryMd: summary.content,
      summaryPreview: buildSummaryPreview(summary.content),
      filingId: filing?.id ?? filingId,
      filingType: filing?.filing_type ?? null,
      filingDate: filing?.filing_date ?? null,
      source: 'summary',
      selectedPersona: summary.metadata?.selectedPersona ?? null,
    } as any, userId)
    setDashboardSavedSummaries(prev => ({ ...prev, [filingId]: true }))
    emitDashboardSync()
    setShowSavedPopup(true)
  }

  const clearCopiedFlag = (filingId: string) => {
    setCopiedSummaries(prev => {
      const next = { ...prev }
      delete next[filingId]
      return next
    })
  }

  const clearCopiedAnalysisFlag = () => {
    setCopiedAnalysis(false)
  }

  const handleCopySummary = async (filingId: string) => {
    const summary = filingSummaries[filingId]
    if (!summary?.content) return

    try {
      await navigator.clipboard.writeText(summary.content)
    } catch (error) {
      const textarea = document.createElement('textarea')
      textarea.value = summary.content
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      textarea.style.top = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }

    setCopiedSummaries(prev => ({ ...prev, [filingId]: true }))
    window.setTimeout(() => clearCopiedFlag(filingId), 1500)
  }

  const handleCopyAnalysis = async () => {
    const content = (analysisToDisplay as any)?.summary_md || (analysisToDisplay as any)?.summaryMd || (analysisToDisplay as any)?.content || ''
    if (!content) return

    try {
      await navigator.clipboard.writeText(content)
    } catch (error) {
      const textarea = document.createElement('textarea')
      textarea.value = content
      textarea.style.position = 'fixed'
      textarea.style.left = '-9999px'
      textarea.style.top = '0'
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }

    setCopiedAnalysis(true)
    window.setTimeout(clearCopiedAnalysisFlag, 1500)
  }

  const sanitizeDownloadFilename = (value: string) =>
    value
      .trim()
      .replace(/[\s/\\]+/g, '_')
      .replace(/[^a-zA-Z0-9._-]+/g, '')
      .replace(/_+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 140) || 'summary'

  const handleExportSummary = async (filingId: string, format: 'pdf' | 'docx') => {
    const summary = filingSummaries[filingId]
    if (!summary?.content) return

    const filing = filings?.find((item: any) => item.id === filingId)
    const companyLabel = company?.ticker || company?.name || 'Company'
    const filingType = filing?.filing_type || 'Filing'
    const filingDate = filing?.filing_date || ''

    const baseName = sanitizeDownloadFilename(
      [companyLabel, filingType, filingDate, 'brief'].filter(Boolean).join('_')
    )
    const filename = `${baseName}.${format === 'pdf' ? 'pdf' : 'docx'}`

    setExportingSummaries(prev => ({ ...prev, [filingId]: format }))
    try {
      const response = await filingsApi.exportSummary(filingId, {
        format,
        title: `${companyLabel} ${filingType} Brief`,
        summary: summary.content,
        filing_type: filingType,
        filing_date: filingDate || undefined,
        generated_at: summary.generatedAt || undefined,
      })

      const blob = response.data as Blob
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
    } catch (error: any) {
      const message = error?.response?.data?.detail ?? 'Failed to export summary'
      alert(message)
    } finally {
      setExportingSummaries(prev => {
        const next = { ...prev }
        delete next[filingId]
        return next
      })
    }
  }

  const handleExportAnalysis = async (format: 'pdf' | 'docx') => {
    const content = (analysisToDisplay as any)?.summary_md || (analysisToDisplay as any)?.summaryMd || (analysisToDisplay as any)?.content || ''
    if (!content) return

    const analysisIdentifier = String((analysisToDisplay as any)?.id ?? currentAnalysisId ?? '')
    if (!analysisIdentifier) return

    const companyLabel = company?.ticker || company?.name || 'Company'

    // If the analysis is actually a locally cached filing summary (dashboard snapshot),
    // reuse the filings export endpoint so the metadata stays consistent.
    const isSummarySnapshot = analysisIdentifier.startsWith('summary-') || isLocalAnalysisId

    const inferredGeneratedAt =
      (analysisToDisplay as any)?.analysis_date ||
      (analysisToDisplay as any)?.analysis_datetime ||
      (analysisToDisplay as any)?.created_at ||
      (analysisToDisplay as any)?.updated_at ||
      localAnalysisSnapshot?.generatedAt ||
      new Date().toISOString()

    const filingType =
      (analysisToDisplay as any)?.primary_filing_type ||
      (analysisToDisplay as any)?.filing_type ||
      localAnalysisSnapshot?.filingType ||
      undefined
    const filingDate =
      (analysisToDisplay as any)?.primary_filing_date ||
      (analysisToDisplay as any)?.filing_date ||
      localAnalysisSnapshot?.filingDate ||
      undefined

    const baseName = sanitizeDownloadFilename(
      isSummarySnapshot
        ? [companyLabel, filingType, filingDate, 'brief'].filter(Boolean).join('_')
        : [companyLabel, 'analysis', inferredGeneratedAt].filter(Boolean).join('_')
    )
    const filename = `${baseName}.${format === 'pdf' ? 'pdf' : 'docx'}`

    setExportingAnalysis(format)

    try {
      const response = isSummarySnapshot
        ? await filingsApi.exportSummary(localAnalysisSnapshot?.filingId || analysisIdentifier.replace('summary-', ''), {
          format,
          title: `${companyLabel} ${filingType ?? 'Filing'} Brief`,
          summary: content,
          filing_type: filingType,
          filing_date: filingDate,
          generated_at: inferredGeneratedAt,
        })
        : await analysisApi.exportAnalysis(analysisIdentifier, {
          format,
          title: `${companyLabel} Financial Analysis`,
          summary: content,
          ticker: company?.ticker,
          company_name: company?.name,
          analysis_date: (analysisToDisplay as any)?.analysis_date || (analysisToDisplay as any)?.analysis_datetime,
          generated_at: inferredGeneratedAt,
          filing_type: filingType,
          filing_date: filingDate,
        })

      const blob = response.data as Blob
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
    } catch (error: any) {
      const message = error?.response?.data?.detail ?? 'Failed to export analysis'
      alert(message)
    } finally {
      setExportingAnalysis(null)
    }
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
      if (!analysis || !company?.id || !userId) return
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
      }, userId)
      emitDashboardSync()
    },
    [company, userId],
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
    mutationFn: () => analysisApi.run(companyId, undefined, {
      includePersonas: selectedPersonas,
      targetLength: clampTargetLength(summaryPreferences.targetLength),
      complexity: summaryPreferences.complexity,
    }),
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

  // Get the most recent health display data - prefer from filing summaries (has user preferences) over base analysis
  const currentHealthDisplay = useMemo(() => {
    const hasHealthData = (summary: FilingSummary | undefined) =>
      !!summary && (summary.healthComponents || summary.healthRating != null)

    // 1) Prefer the currently selected filing (if it already has a generated summary)
    const selectedSummary = selectedFilingForSummary
      ? filingSummaries[selectedFilingForSummary]
      : undefined
    if (hasHealthData(selectedSummary)) {
      return {
        score: selectedSummary!.healthRating ?? latestAnalysis?.health_score,
        components: selectedSummary!.healthComponents ?? latestAnalysis?.health_components,
        weights: selectedSummary!.healthComponentWeights,
        descriptions: selectedSummary!.healthComponentDescriptions,
        metrics: selectedSummary!.healthComponentMetrics,
      }
    }

    // 2) Otherwise, use the most recently generated summary (by timestamp)
    let newest: FilingSummary | null = null
    for (const [, summary] of Object.entries(filingSummaries)) {
      if (!hasHealthData(summary)) continue
      if (!newest || summary.generatedAt > newest.generatedAt) newest = summary
    }
    if (newest) {
      return {
        score: newest.healthRating ?? latestAnalysis?.health_score,
        components: newest.healthComponents ?? latestAnalysis?.health_components,
        weights: newest.healthComponentWeights,
        descriptions: newest.healthComponentDescriptions,
        metrics: newest.healthComponentMetrics,
      }
    }

    // Fall back to latestAnalysis data
    return {
      score: latestAnalysis?.health_score,
      components: latestAnalysis?.health_components,
      weights: undefined,
      descriptions: undefined,
      metrics: undefined,
    }
  }, [filingSummaries, latestAnalysis, selectedFilingForSummary])

  const summaryMarkdownComponents = useMemo(
    () => ({
      h1: ({ node, ...props }: any) => <h2 className="text-xl font-black uppercase mt-8 mb-4 flex items-center gap-2" {...props}><span className="w-3 h-3 bg-black dark:bg-white" /></h2>,
      h2: ({ node, ...props }: any) => <h2 className="text-xl font-black uppercase mt-8 mb-4 flex items-center gap-2" {...props}><span className="w-3 h-3 bg-black dark:bg-white" /></h2>,
      h3: ({ node, ...props }: any) => <h3 className="text-lg font-bold uppercase mt-6 mb-3 flex items-center gap-2 text-zinc-700 dark:text-zinc-300" {...props}><span className="text-blue-500">#</span></h3>,
      p: ({ node, ...props }: any) => <p className="text-base leading-relaxed mb-4 text-zinc-800 dark:text-zinc-200" {...props} />,
      strong: ({ node, ...props }: any) => <strong className="font-black bg-yellow-200/80 dark:bg-yellow-900/50 px-1 rounded" {...props} />,
      em: ({ node, ...props }: any) => <span className="italic opacity-80" {...props} />,
      ul: ({ node, ordered, ...props }: any) => (
        <ul className="list-none space-y-3 mb-6 mt-2" {...props} />
      ),
      ol: ({ node, ordered, ...props }: any) => (
        <ol className="list-decimal pl-6 space-y-3 mb-6 mt-2 text-zinc-800 dark:text-zinc-200" {...props} />
      ),
      li: ({ node, ...props }: any) => (
        <li className="flex items-start gap-2 text-zinc-800 dark:text-zinc-200" {...props}>
          <span className="text-blue-500 font-bold mt-0.5">→</span>
          <span className="flex-1">{props.children}</span>
        </li>
      ),
      code: ({ inline, className, children, ...props }: any) => (
        <code className="font-mono text-sm bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded px-1.5 py-0.5" {...props}>
          {children}
        </code>
      ),
      blockquote: ({ node, ...props }: any) => (
        <blockquote className="border-l-4 border-black dark:border-white pl-4 italic my-6 bg-zinc-50 dark:bg-zinc-900/50 p-4 rounded-r-lg text-zinc-700 dark:text-zinc-300" {...props} />
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
    if (!company?.id || !userId) return
    DashboardStorage.upsertRecentCompany({
      id: company.id,
      name: company.name,
      ticker: company.ticker,
      exchange: company.exchange,
      sector: company.sector,
      industry: company.industry,
      country: company.country,
    }, userId)
  }, [company, userId])

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
      <MultiStepLoader
        loading={!!activeSummaryProgressFilingId}
        progress={activeSummaryProgressFilingId ? summaryProgress[activeSummaryProgressFilingId] : null}
        title="Generating AI Brief"
      />
      <Modal
        isOpen={summaryErrorModal.isOpen}
        onClose={() => setSummaryErrorModal(prev => ({ ...prev, isOpen: false }))}
        className="max-w-xl"
      >
        <div className="pr-10">
          <h2 className="text-2xl font-black uppercase tracking-tight">{summaryErrorModal.title || 'Couldn’t generate summary'}</h2>
          <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap">
            {summaryErrorModal.message}
          </p>

          <div className="mt-5 space-y-2 text-sm">
            <p className="font-bold">Try this:</p>
            <ul className="list-disc pl-5 space-y-1 text-zinc-800 dark:text-zinc-200">
              <li>Retry once (temporary network/API hiccups happen).</li>
              <li>Hard refresh the page, then try again.</li>
              {summaryErrorModal.showExtensionTip ? (
                <li>
                  If you have browser extensions that inject into pages or block requests (e.g. Savier, ad blockers),
                  temporarily disable them or try Incognito, then retry.
                </li>
              ) : null}
            </ul>
          </div>

          <div className="mt-6 flex gap-3">
            <Button onClick={() => setSummaryErrorModal(prev => ({ ...prev, isOpen: false }))}>Close</Button>
          </div>
        </div>
      </Modal>

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
                      src={`/api/backend/api/v1/companies/logo/${company.ticker}`}
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

              {currentHealthDisplay.score != null && (
                <div className="self-end md:self-center">
                  <HealthScoreBadge
                    score={currentHealthDisplay.score}
                    band={scoreToRating(currentHealthDisplay.score).label}
                    componentScores={currentHealthDisplay.components}
                    componentWeights={currentHealthDisplay.weights}
                    componentDescriptions={currentHealthDisplay.descriptions}
                    componentMetrics={currentHealthDisplay.metrics}
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

                {/* AI Summary Section */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                  {/* Summary Generation Card */}
                  <div className="lg:col-span-1 bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] h-fit sticky top-4">
                    <h3 className="text-lg font-black uppercase mb-6 flex items-center gap-2">
                      <span className="w-3 h-3 bg-blue-600"></span>
                      Custom
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
                  <div className="lg:col-span-2 relative">
                    {Object.entries(filingSummaries).length === 0 && Object.keys(dashboardSavedSummaries).length === 0 ? (
                      <div className="bg-gray-50 dark:bg-zinc-900/50 border-2 border-dashed border-gray-300 dark:border-gray-700 p-12 text-center">
                        <div className="w-16 h-16 bg-gray-200 dark:bg-zinc-800 mx-auto mb-4 flex items-center justify-center border-2 border-gray-400 dark:border-gray-600">
                          <span className="text-2xl">📄</span>
                        </div>
                        <h3 className="text-lg font-bold uppercase text-gray-500 dark:text-gray-400">No summaries yet</h3>
                        <p className="text-sm text-gray-400 mt-2">Select a filing to generate an AI-powered brief.</p>
                      </div>
                    ) : (
                      <div className="space-y-8">
                          {Object.entries(filingSummaries).map(([fid, summary], idx) => {
                            const filing = filings?.find((item: any) => item.id === fid)
                            const filingColor = getFilingColor(filing?.filing_type)
                            
                            return (
                              <motion.div
                                key={fid}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true, margin: "-50px" }}
                                transition={{ duration: 0.4, delay: idx * 0.08 }}
                                className="group"
                              >
                                <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-0 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] overflow-hidden transition-all duration-300 group-hover:shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] dark:group-hover:shadow-[8px_8px_0px_0px_rgba(255,255,255,1)] group-hover:-translate-y-1">
                                  {/* Card Header with Type Color */}
                                  <div className={cn("h-2 w-full", filingColor)} />
                                  
                                  <div className="p-6">
                                    <div className="flex flex-col md:flex-row justify-between items-start mb-6 gap-4 border-b-2 border-gray-100 dark:border-gray-800 pb-6">
                                      <div>
                                        <div className="flex items-center gap-3 flex-wrap">
                                          <div className={cn("px-2 py-0.5 text-[10px] font-black uppercase text-white", filingColor)}>
                                            {filing?.filing_type || 'Unknown'}
                                          </div>
                                          <h4 className="font-black uppercase text-xl">AI Financial Brief</h4>
                                          {summary.healthRating && (
                                            <HealthScoreBadge
                                              score={summary.healthRating}
                                              band={scoreToRating(summary.healthRating).label}
                                              size="sm"
                                              componentScores={summary.healthComponents}
                                              componentWeights={summary.healthComponentWeights}
                                              componentDescriptions={summary.healthComponentDescriptions}
                                              componentMetrics={summary.healthComponentMetrics}
                                            />
                                          )}
                                        </div>
                                        <div className="flex items-center gap-2 mt-2">
                                          <p className="text-xs font-mono text-gray-500">
                                            Filing ID: <span className="text-black dark:text-white font-bold">{fid.slice(0, 12)}</span>
                                          </p>
                                          <span className="text-gray-300">•</span>
                                          <p className="text-xs font-mono text-gray-500">
                                            {new Date(summary.generatedAt).toLocaleDateString()}
                                          </p>
                                        </div>
                                      </div>
                                      
                                      <div className="flex flex-wrap gap-2 justify-end">
                                        <BrutalButton
                                          onClick={() => handleAddSummaryToDashboard(fid)}
                                          disabled={dashboardSavedSummaries[fid]}
                                          className="px-3 py-1.5 text-[10px] bg-green-400 text-black border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                                        >
                                          {dashboardSavedSummaries[fid] ? '✓ Saved' : 'Save to Dashboard'}
                                        </BrutalButton>
                                        <BrutalButton
                                          onClick={() => handleCopySummary(fid)}
                                          className="px-3 py-1.5 text-[10px] bg-yellow-300 text-black border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                                        >
                                          {copiedSummaries[fid] ? 'Copied!' : 'Copy'}
                                        </BrutalButton>
                                        <BrutalButton
                                          onClick={() => handleExportSummary(fid, 'pdf')}
                                          disabled={!!exportingSummaries[fid]}
                                          className="px-3 py-1.5 text-[10px] bg-blue-300 text-black border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                                        >
                                          {exportingSummaries[fid] === 'pdf' ? '...' : 'PDF'}
                                        </BrutalButton>
                                        <BrutalButton
                                          onClick={() => {
                                            const newSummaries = { ...filingSummaries }
                                            delete newSummaries[fid]
                                            setFilingSummaries(newSummaries)
                                          }}
                                          className="px-3 py-1.5 text-[10px] bg-rose-400 text-black border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
                                        >
                                          Dismiss
                                        </BrutalButton>
                                      </div>
                                    </div>

                                    <div className="prose dark:prose-invert max-w-none">
                                      <EnhancedSummary
                                        content={summary.content}
                                        persona={summary.metadata?.selectedPersona ? INVESTOR_PERSONAS.find(p => p.id === summary.metadata.selectedPersona) : null}
                                        chartData={summary.chartData}
                                        filingId={fid}
                                        healthData={{
                                          score: summary.healthRating,
                                          components: summary.healthComponents,
                                          weights: summary.healthComponentWeights,
                                          descriptions: summary.healthComponentDescriptions,
                                          metrics: summary.healthComponentMetrics,
                                        }}
                                      />
                                    </div>
                                  </div>
                                </div>
                              </motion.div>
                            )
                          })}
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
                  <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-8 gap-4">
                    <h2 className="text-xl font-black uppercase flex items-center gap-3">
                      <span className="w-4 h-4 bg-emerald-600"></span>
                      Financial Analysis
                    </h2>
                    {analysisToDisplay && (
                      <div className="flex flex-wrap gap-2 justify-end">
                        <BrutalButton
                          onClick={handleManualDashboardUpdate}
                          variant="outline-rounded"
                          className="text-xs"
                        >
                          Update Dashboard
                        </BrutalButton>
                        <BrutalButton
                          onClick={handleCopyAnalysis}
                          disabled={!(analysisToDisplay.summary_md || analysisToDisplay.content)}
                          className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-yellow-300 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)] disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {copiedAnalysis ? 'Copied' : 'Copy'}
                        </BrutalButton>
                        <BrutalButton
                          onClick={() => handleExportAnalysis('pdf')}
                          disabled={!(analysisToDisplay.summary_md || analysisToDisplay.content) || !!exportingAnalysis}
                          className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-blue-300 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)] disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {exportingAnalysis === 'pdf' ? 'Exporting...' : 'Export PDF'}
                        </BrutalButton>
                        <BrutalButton
                          onClick={() => handleExportAnalysis('docx')}
                          disabled={!(analysisToDisplay.summary_md || analysisToDisplay.content) || !!exportingAnalysis}
                          className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-blue-300 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)] disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {exportingAnalysis === 'docx' ? 'Exporting...' : 'Export Word'}
                        </BrutalButton>
                      </div>
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

      {/* Saved to Dashboard Success Popup */}
      <Modal isOpen={showSavedPopup} onClose={() => setShowSavedPopup(false)} className="max-w-md">
        <div className="text-center py-4">
          <div className="w-16 h-16 bg-green-400 mx-auto mb-6 flex items-center justify-center border-2 border-black dark:border-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
            <svg className="w-8 h-8 text-black" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h3 className="text-xl font-black uppercase mb-2">Saved to Dashboard!</h3>
          <p className="text-gray-600 dark:text-gray-400 font-mono text-sm mb-8">
            Your summary has been saved and is ready to view.
          </p>
          <div className="flex flex-col gap-3">
            <BrutalButton
              onClick={() => router.push('/dashboard')}
              className="w-full px-6 py-3 text-sm font-bold uppercase border-2 border-black dark:border-white bg-blue-500 text-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-2px] hover:shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] transition-all"
            >
              Go to Dashboard
            </BrutalButton>
            <button
              onClick={() => setShowSavedPopup(false)}
              className="w-full px-6 py-3 text-sm font-bold uppercase text-gray-600 dark:text-gray-400 hover:text-black dark:hover:text-white transition-colors"
            >
              Stay Here
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
