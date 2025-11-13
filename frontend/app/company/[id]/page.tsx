'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Navbar from '@/components/Navbar'
import HealthScoreBadge from '@/components/HealthScoreBadge'
import FinancialCharts from '@/components/FinancialCharts'
import PersonaSelector from '@/components/PersonaSelector'
import EnhancedSummary from '@/components/EnhancedSummary'
import AnimatedList, { AnimatedListItem } from '@/components/AnimatedList'
import { companyApi, filingsApi, analysisApi, API_BASE_URL, FilingSummaryPreferencesPayload } from '@/lib/api-client'
import DashboardStorage, { StoredAnalysisSnapshot, StoredSummaryPreferences } from '@/lib/dashboard-storage'
import { buildSummaryPreview, scoreToRating } from '@/lib/analysis-insights'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/base/buttons/button'
import { useAuth } from '@/contexts/AuthContext'

type SummaryMode = 'default' | 'custom'
type SummaryTone = 'objective' | 'cautiously optimistic' | 'bullish' | 'bearish'
type SummaryDetailLevel = 'snapshot' | 'balanced' | 'deep dive'
type SummaryOutputStyle = 'narrative' | 'bullets' | 'mixed'

type SummaryPreferenceFormState = {
  mode: SummaryMode
  investorFocus: string
  focusAreas: string[]
  tone: SummaryTone
  detailLevel: SummaryDetailLevel
  outputStyle: SummaryOutputStyle
  targetLength: number
}

type SummaryPreferenceSnapshot = {
  mode: SummaryMode
  investorFocus?: string
  focusAreas?: string[]
  tone?: SummaryTone
  detailLevel?: SummaryDetailLevel
  outputStyle?: SummaryOutputStyle
  targetLength?: number
}

type FilingSummaryMap = Record<string, { content: string; metadata: SummaryPreferenceSnapshot }>

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

const createDefaultSummaryPreferences = (): SummaryPreferenceFormState => ({
  mode: 'default',
  investorFocus: '',
  focusAreas: [],
  tone: 'objective',
  detailLevel: 'balanced',
  outputStyle: 'narrative',
  targetLength: 300,
})

const clampTargetLength = (value: number) => {
  if (Number.isNaN(value)) return 300
  return Math.max(10, Math.min(5000, Math.round(value)))
}

const buildPreferencePayload = (prefs: SummaryPreferenceFormState): FilingSummaryPreferencesPayload | undefined => {
  if (prefs.mode === 'default') {
    return undefined
  }
  return {
    mode: 'custom',
    investor_focus: prefs.investorFocus.trim() || undefined,
    focus_areas: prefs.focusAreas.length ? prefs.focusAreas : undefined,
    tone: prefs.tone,
    detail_level: prefs.detailLevel,
    output_style: prefs.outputStyle,
    target_length: clampTargetLength(prefs.targetLength),
  }
}

const snapshotPreferences = (prefs: SummaryPreferenceFormState): SummaryPreferenceSnapshot => {
  if (prefs.mode === 'default') {
    return { mode: 'default' }
  }
  return {
    mode: 'custom',
    investorFocus: prefs.investorFocus.trim() || undefined,
    focusAreas: prefs.focusAreas.length ? [...prefs.focusAreas] : undefined,
    tone: prefs.tone,
    detailLevel: prefs.detailLevel,
    outputStyle: prefs.outputStyle,
    targetLength: clampTargetLength(prefs.targetLength),
  }
}

const isSummaryToneValue = (value: string): value is SummaryTone => toneOptions.some(option => option.value === value)
const isSummaryDetailValue = (value: string): value is SummaryDetailLevel =>
  detailOptions.some(option => option.value === value)
const isSummaryOutputStyleValue = (value: string): value is SummaryOutputStyle =>
  outputStyleOptions.some(option => option.value === value)

const sanitizeStoredPreferences = (stored: StoredSummaryPreferences): SummaryPreferenceFormState => ({
  mode: stored.mode === 'custom' ? 'custom' : 'default',
  investorFocus: stored.investorFocus ?? '',
  focusAreas: Array.isArray(stored.focusAreas) ? stored.focusAreas : [],
  tone: isSummaryToneValue(stored.tone) ? stored.tone : 'objective',
  detailLevel: isSummaryDetailValue(stored.detailLevel) ? stored.detailLevel : 'balanced',
  outputStyle: isSummaryOutputStyleValue(stored.outputStyle) ? stored.outputStyle : 'narrative',
  targetLength: clampTargetLength(stored.targetLength),
})

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
  const [filingSummaries, setFilingSummaries] = useState<FilingSummaryMap>({})
  const [loadingSummaries, setLoadingSummaries] = useState<Record<string, boolean>>({})
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
    const saved = DashboardStorage.loadAnalysisHistory().reduce<Record<string, boolean>>((acc, entry) => {
      if (entry.analysisId?.startsWith('summary-')) {
        acc[entry.analysisId.replace('summary-', '')] = true
      }
      return acc
    }, {})
    setDashboardSavedSummaries(saved)
  }, [])

  const resolveFilingUrl = (path?: string | null) => {
    if (!path) return '#'
    try {
      return new URL(path, API_BASE_URL).toString()
    } catch (error) {
      return path
    }
  }

  const handleGenerateSummary = async (
    filingId: string,
    preferences?: FilingSummaryPreferencesPayload,
    metadata?: SummaryPreferenceSnapshot,
  ) => {
    setLoadingSummaries(prev => ({ ...prev, [filingId]: true }))
    try {
      const response = await filingsApi.summarizeFiling(filingId, preferences)
      setFilingSummaries(prev => ({
        ...prev,
        [filingId]: {
          content: response.data.summary,
          metadata: metadata ?? { mode: 'default' },
        },
      }))
    } catch (error: any) {
      const message = error?.response?.data?.detail ?? 'Failed to generate summary'
      alert(message)
    } finally {
      setLoadingSummaries(prev => ({ ...prev, [filingId]: false }))
    }
  }

  const handleAddSummaryToDashboard = (filingId: string) => {
    if (!company) return
    const summary = filingSummaries[filingId]
    if (!summary) return

    const generatedAt = new Date().toISOString()
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
      healthScore: null,
      scoreBand: null,
      ratingLabel: summary.metadata?.mode === 'custom' ? 'Custom brief' : 'Quick brief',
      summaryMd: summary.content,
      summaryPreview: buildSummaryPreview(summary.content),
    })
    setDashboardSavedSummaries(prev => ({ ...prev, [filingId]: true }))
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
  const filingListItems = useMemo<AnimatedListItem[]>(() => {
    if (!filings) return []
    return filings.map((filing: any) => {
      const formattedDate = filing.filing_date
        ? new Date(filing.filing_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
        : 'Date unavailable'

      const period = filing.period || filing.period_end || filing.fiscal_period
      const description = period ? `${formattedDate} • ${period}` : formattedDate
      const status = filing.status || filing.filing_status

      return {
        id: filing.id,
        title: filing.filing_type || 'Filing',
        description,
        meta: status,
      }
    })
  }, [filings])

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
    if (!company?.id || !latestAnalysis?.id) return
    const personaSignals = latestAnalysis.investor_persona_summaries
      ? Object.entries(latestAnalysis.investor_persona_summaries).map(([personaId, data]: [string, any]) => ({
          personaId,
          personaName: data?.persona_name ?? personaId,
          stance: data?.stance ?? 'Neutral',
        }))
      : undefined
    const generatedAt =
      latestAnalysis.analysis_date ||
      latestAnalysis.created_at ||
      (latestAnalysis.updated_at ? latestAnalysis.updated_at : new Date().toISOString())

    DashboardStorage.upsertAnalysisSnapshot({
      analysisId: latestAnalysis.id,
      generatedAt,
      id: company.id,
      name: company.name,
      ticker: company.ticker,
      exchange: company.exchange,
      sector: company.sector,
      industry: company.industry,
      country: company.country,
      healthScore: latestAnalysis.health_score,
      scoreBand: latestAnalysis.score_band,
      ratingLabel: scoreToRating(latestAnalysis.health_score).label,
      summaryMd: latestAnalysis.summary_md,
      summaryPreview: buildSummaryPreview(latestAnalysis.summary_md),
      personaSignals,
    })
  }, [company, latestAnalysis])

  if (authPending) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900 flex items-center justify-center">
        <div className="text-center">
          <div className="spinner mx-auto mb-4"></div>
          <p className="text-gray-300 text-xl">Checking your session...</p>
        </div>
      </div>
    )
  }

  if (companyLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900">
        <Navbar />
        <div className="flex items-center justify-center h-96">
          <div className="text-center">
            <div className="spinner mx-auto mb-4"></div>
            <p className="text-gray-300 text-xl">Loading company data...</p>
          </div>
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
    <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900">
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 mb-8 animate-fade-in">
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <h1 className="text-4xl font-bold text-white mb-3">{company.name}</h1>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="px-4 py-2 rounded-lg bg-gradient-to-r from-primary-500 to-accent-500 text-white font-bold text-lg">
                  {company.ticker}
                </span>
                <span className="px-3 py-1 rounded-lg bg-white/10 text-gray-300 text-sm">
                  {company.exchange}
                </span>
                {company.sector && (
                  <span className="px-3 py-1 rounded-lg bg-white/10 text-gray-300 text-sm">
                    {company.sector}
                  </span>
                )}
              </div>
            </div>
            {latestAnalysis && latestAnalysis.health_score && (
              <div className="ml-4">
                <HealthScoreBadge 
                  score={latestAnalysis.health_score} 
                  band={latestAnalysis.score_band || 'Unknown'}
                />
              </div>
            )}
          </div>
        </div>

        {infoMessage && (
          <div className="mb-6 bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border-2 border-yellow-500/30 text-yellow-300 px-6 py-4 rounded-2xl backdrop-blur-sm animate-slide-down">
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
              </svg>
              <p className="font-medium">{infoMessage}</p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="glass rounded-2xl border border-white/10 shadow-premium-lg mb-6 overflow-hidden animate-slide-up">
          <div className="border-b border-white/10">
            <nav className="flex -mb-px overflow-x-auto">
              {[
                { id: 'overview', label: 'Overview' },
                { id: 'filings', label: 'Filings' },
                { id: 'analysis', label: 'Analysis' },
                { id: 'personas', label: 'Investor Personas' },
              ].map(tab => (
                <Button
                  key={tab.id}
                  onClick={() => setSelectedTab(tab.id as any)}
                  color="ghost"
                  size="sm"
                  asMotion={false}
                  className={`px-6 py-4 text-sm font-semibold whitespace-nowrap transition-all rounded-none ${
                    selectedTab === tab.id
                      ? 'border-b-2 border-primary-500 text-white bg-white/5'
                      : 'text-gray-400 hover:text-white hover:bg-white/5'
                  }`}
                >
                  {tab.label}
                </Button>
              ))}
            </nav>
          </div>

          <div className="p-8">
            {/* Overview Tab */}
            {selectedTab === 'overview' && (
              <div className="space-y-8">
                <div>
                  <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                    <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    Quick Actions
                  </h2>
                  <div className="flex flex-wrap gap-4 mb-6">
                    <Button
                      onClick={() => fetchFilingsMutation.mutate()}
                      color="primary"
                      size="md"
                      isLoading={fetchFilingsMutation.isPending}
                      leftIcon={
                        !fetchFilingsMutation.isPending ? (
                          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M9 19l3 3m0 0l3-3m-3 3V10" />
                          </svg>
                        ) : undefined
                      }
                    >
                      {fetchFilingsMutation.isPending ? 'Fetching Filings...' : 'Fetch Latest Filings'}
                    </Button>
                    <Button
                      onClick={() => runAnalysisMutation.mutate()}
                      disabled={!filings || filings.length === 0}
                      color="success"
                      size="md"
                      isLoading={runAnalysisMutation.isPending}
                      leftIcon={
                        !runAnalysisMutation.isPending ? (
                          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                          </svg>
                        ) : undefined
                      }
                    >
                      {runAnalysisMutation.isPending ? 'Running Analysis...' : 'Run Analysis'}
                    </Button>
                  </div>
                  
                  {filings && filings.length > 0 && (
                    <div
                      ref={summaryCardRef}
                      id="summary-preferences"
                      className="card-premium bg-gradient-to-br from-dark-700 to-dark-800 border-primary-500/20 space-y-6"
                    >
                      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                          </svg>
                          <div>
                            <p className="text-lg font-semibold text-white">Personalized AI Summary</p>
                            <p className="text-sm text-gray-400">
                              {selectedFilingDetails
                                ? `${selectedFilingDetails.filing_type} • ${new Date(selectedFilingDetails.filing_date).toLocaleDateString()}`
                                : 'Choose a filing to get started.'}
                            </p>
                          </div>
                        </div>
                        {selectedFilingDetails?.status && (
                          <span className="inline-flex items-center px-3 py-1 rounded-full bg-primary-500/20 text-primary-200 text-xs font-semibold uppercase tracking-wide">
                            {selectedFilingDetails.status}
                          </span>
                        )}
                      </div>

                      <div className="space-y-6">
                        <div>
                          <div className="flex items-center justify-between mb-2">
                            <label className="text-sm font-semibold text-gray-300">Select filing</label>
                            <span className="text-xs text-gray-400">
                              {selectedFilingForSummary ? 'Press Enter to confirm' : 'Use ↑ ↓ + Enter'}
                            </span>
                          </div>
                          <AnimatedList
                            items={filingListItems}
                            selectedId={selectedFilingForSummary}
                            onItemSelect={(item) => setSelectedFilingForSummary(item.id)}
                            showGradients
                            enableArrowNavigation
                            displayScrollbar={false}
                          />
                        </div>

                        <div>
                          <p className="text-sm font-semibold text-gray-300 mb-3">How should we summarize it?</p>
                          <div className="grid sm:grid-cols-2 gap-3">
                            {['default', 'custom'].map((mode) => {
                              const isSelected = summaryPreferences.mode === mode
                              return (
                                <Button
                                  key={mode}
                                  type="button"
                                  onClick={() => setSummaryPreferences(prev => ({ ...prev, mode: mode as SummaryMode }))}
                                  color="ghost"
                                  size="md"
                                  asMotion={false}
                                  className={`text-left p-4 rounded-xl border transition-all ${
                                    isSelected
                                      ? 'border-primary-500/60 bg-primary-500/15 text-white shadow-premium'
                                      : 'border-white/10 bg-white/5 text-gray-300 hover:border-primary-500/40'
                                  }`}
                                >
                                  <p className="font-semibold mb-1 flex items-center gap-2">
                                    <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-white/20 text-xs">
                                      {isSelected ? '✓' : mode === 'default' ? '0' : '?'}
                                    </span>
                                    {mode === 'default' ? 'Default summary' : 'Custom brief'}
                                  </p>
                                  <p className="text-sm text-gray-300">
                                    {mode === 'default'
                                      ? 'Use the Financesum house style with balanced coverage.'
                                      : 'Answer a few prompts so the AI focuses on what you care about.'}
                                  </p>
                                </Button>
                              )
                            })}
                          </div>
                        </div>

                        {summaryPreferences.mode === 'custom' ? (
                          <>
                            <div>
                              <label className="text-sm font-semibold text-gray-300 mb-2 block">
                                What are you specifically looking for?
                              </label>
                              <textarea
                                value={summaryPreferences.investorFocus}
                                onChange={(e) => setSummaryPreferences(prev => ({ ...prev, investorFocus: e.target.value }))}
                                rows={3}
                                placeholder="e.g. Compare margin trends and call out liquidity risks."
                                className="w-full px-4 py-3 bg-dark-900 border-2 border-white/10 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 focus:ring-4 focus:ring-primary-100/20 transition-all"
                              />
                            </div>

                            <div>
                              <p className="text-sm font-semibold text-gray-300 mb-3">Focus areas</p>
                              <div className="flex flex-wrap gap-2">
                                {focusAreaOptions.map(area => {
                                  const active = summaryPreferences.focusAreas.includes(area)
                                  return (
                                    <Button
                                      key={area}
                                      type="button"
                                      onClick={() => toggleFocusArea(area)}
                                      color="ghost"
                                      size="sm"
                                      asMotion={false}
                                      className={`px-4 py-2 rounded-full text-sm border transition-all ${
                                        active
                                          ? 'border-primary-500 text-primary-200 bg-primary-500/15'
                                          : 'border-white/10 text-gray-300 bg-white/5 hover:border-primary-500/40 hover:text-white'
                                      }`}
                                    >
                                      {area}
                                    </Button>
                                  )
                                })}
                              </div>
                            </div>

                            <div className="grid gap-4 lg:grid-cols-3">
                              <div className="bg-white/5 rounded-xl p-4 border border-white/10">
                                <p className="text-sm font-semibold text-gray-300 mb-3">Tone</p>
                                <div className="space-y-2">
                                  {toneOptions.map(option => {
                                    const active = summaryPreferences.tone === option.value
                                    return (
                                      <Button
                                        key={option.value}
                                        type="button"
                                        onClick={() => setSummaryPreferences(prev => ({ ...prev, tone: option.value }))}
                                        color="ghost"
                                        size="sm"
                                        asMotion={false}
                                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition ${
                                          active ? 'bg-primary-500/20 text-white border border-primary-500/60' : 'bg-dark-900 border border-white/10 text-gray-300 hover:border-primary-500/40'
                                        }`}
                                      >
                                        <p className="font-semibold">{option.label}</p>
                                        <p className="text-xs text-gray-400">{option.description}</p>
                                      </Button>
                                    )
                                  })}
                                </div>
                              </div>
                              <div className="bg-white/5 rounded-xl p-4 border border-white/10">
                                <p className="text-sm font-semibold text-gray-300 mb-3">Detail level</p>
                                <div className="space-y-2">
                                  {detailOptions.map(option => {
                                    const active = summaryPreferences.detailLevel === option.value
                                    return (
                                      <Button
                                        key={option.value}
                                        type="button"
                                        onClick={() => setSummaryPreferences(prev => ({ ...prev, detailLevel: option.value }))}
                                        color="ghost"
                                        size="sm"
                                        asMotion={false}
                                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition ${
                                          active ? 'bg-primary-500/20 text-white border border-primary-500/60' : 'bg-dark-900 border border-white/10 text-gray-300 hover:border-primary-500/40'
                                        }`}
                                      >
                                        <p className="font-semibold">{option.label}</p>
                                        <p className="text-xs text-gray-400">{option.description}</p>
                                      </Button>
                                    )
                                  })}
                                </div>
                              </div>
                              <div className="bg-white/5 rounded-xl p-4 border border-white/10">
                                <p className="text-sm font-semibold text-gray-300 mb-3">Output style</p>
                                <div className="space-y-2">
                                  {outputStyleOptions.map(option => {
                                    const active = summaryPreferences.outputStyle === option.value
                                    return (
                                      <Button
                                        key={option.value}
                                        type="button"
                                        onClick={() => setSummaryPreferences(prev => ({ ...prev, outputStyle: option.value }))}
                                        color="ghost"
                                        size="sm"
                                        asMotion={false}
                                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition ${
                                          active ? 'bg-primary-500/20 text-white border border-primary-500/60' : 'bg-dark-900 border border-white/10 text-gray-300 hover:border-primary-500/40'
                                        }`}
                                      >
                                        <p className="font-semibold">{option.label}</p>
                                        <p className="text-xs text-gray-400">{option.description}</p>
                                      </Button>
                                    )
                                  })}
                                </div>
                              </div>
                            </div>

                            <div>
                              <div className="flex items-center justify-between mb-2">
                                <p className="text-sm font-semibold text-gray-300">Target length</p>
                                <div className="flex items-center gap-3">
                                  <span className="text-sm text-primary-200 font-semibold">
                                    {summaryPreferences.targetLength} words
                                  </span>
                                  <Button
                                    type="button"
                                    onClick={() => setShowCustomLengthInput(prev => !prev)}
                                    color="ghost"
                                    size="sm"
                                    asMotion={false}
                                    className="px-3 py-1 rounded-lg border border-white/10 text-xs text-gray-300 hover:border-primary-500/40 hover:text-white transition"
                                  >
                                    {showCustomLengthInput ? 'Hide custom' : 'Custom'}
                                  </Button>
                                </div>
                              </div>
                              <input
                                type="range"
                                min={50}
                                max={5000}
                                step={50}
                                value={sliderLengthValue}
                                onChange={(e) => updateTargetLength(Number(e.target.value))}
                                className="w-full accent-primary-500"
                              />
                              <p className="text-xs text-gray-400 mt-1">
                                Drag to tell the AI how much detail you want (50–5000 words). Use custom input for finer control (down to 10 words).
                              </p>
                              {showCustomLengthInput && (
                                <div className="mt-3 flex flex-col sm:flex-row gap-2">
                                  <input
                                    type="number"
                                    min={10}
                                    max={5000}
                                    value={customLengthInput}
                                    onChange={(e) => setCustomLengthInput(e.target.value)}
                                    className="flex-1 px-4 py-2 rounded-lg bg-dark-900 border-2 border-white/10 text-white focus:outline-none focus:border-primary-500 focus:ring-4 focus:ring-primary-100/20"
                                    placeholder="Enter custom word count"
                                  />
                                  <Button
                                    type="button"
                                    onClick={handleCustomLengthApply}
                                    color="primary"
                                    size="sm"
                                  >
                                    Apply
                                  </Button>
                                </div>
                              )}
                            </div>
                          </>
                        ) : (
                          <div className="bg-white/5 border border-white/10 rounded-xl p-4 text-sm text-gray-300">
                            <p className="font-semibold text-white mb-2">Default house summary</p>
                            <p>
                              We will use the standard Financesum memo: executive summary, financial performance, MD&A,
                              risks, capital allocation, and key metrics — optimized for a balanced ~300 word read. Adjust the target length above if you need more or less detail.
                            </p>
                          </div>
                        )}

                        <div className="flex flex-wrap gap-3">
                          <Button
                            onClick={requestSummaryWithPreferences}
                            disabled={!selectedFilingForSummary}
                            isLoading={isSummaryGenerating}
                            color="primary"
                            size="md"
                            className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700"
                            leftIcon={
                              !isSummaryGenerating ? (
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z" />
                                </svg>
                              ) : undefined
                            }
                          >
                            {isSummaryGenerating ? 'Generating...' : (summaryPreferences.mode === 'custom' ? 'Generate custom summary' : 'Generate default summary')}
                          </Button>
                          <Button
                            type="button"
                            onClick={() => {
                              const defaults = createDefaultSummaryPreferences()
                              setSummaryPreferences(defaults)
                              setShowCustomLengthInput(false)
                              setCustomLengthInput(String(defaults.targetLength))
                            }}
                            color="ghost"
                            size="md"
                            className="px-5 py-3 rounded-lg border border-white/10 text-sm text-gray-300 hover:border-primary-500/40 hover:text-white transition"
                          >
                            Reset questionnaire
                          </Button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {(() => {
                  if (Object.keys(filingSummaries).length === 0) return null
                  
                  const summaryEntries = Object.entries(filingSummaries)
                  return summaryEntries.map(([filingId, summaryData]) => {
                    const filing = filings?.find((f: any) => f.id === filingId)
                    if (!filing || !summaryData?.content) return null
                    const meta = summaryData.metadata

                    return (
                      <div key={filingId} className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/30 animate-scale-in">
                        <div className="flex justify-between items-start mb-6 gap-4 flex-wrap">
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center">
                              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                              </svg>
                            </div>
                            <div>
                              <h3 className="text-xl font-bold text-white">
                                {meta?.mode === 'custom' ? 'Custom Summary' : 'AI Summary'}: {filing.filing_type}
                              </h3>
                              <p className="text-sm text-gray-400">
                                {new Date(filing.filing_date).toLocaleDateString()}
                              </p>
                              <div className="flex flex-wrap gap-2 mt-2">
                                <span className="px-3 py-1 rounded-full text-xs font-semibold bg-white/5 text-gray-300 border border-white/10">
                                  {meta?.mode === 'custom' ? 'Tailored request' : 'Default settings'}
                                </span>
                                {meta?.targetLength && (
                                  <span className="px-3 py-1 rounded-full text-xs font-semibold bg-primary-500/10 text-primary-200 border border-primary-500/40">
                                    ~{meta.targetLength} words
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <Button
                              onClick={() => handleAddSummaryToDashboard(filingId)}
                              color={dashboardSavedSummaries[filingId] ? 'success' : 'secondary'}
                              size="sm"
                              disabled={!company || dashboardSavedSummaries[filingId]}
                              className="whitespace-nowrap"
                              leftIcon={
                                dashboardSavedSummaries[filingId] ? (
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                  </svg>
                                ) : (
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                  </svg>
                                )
                              }
                            >
                              {dashboardSavedSummaries[filingId] ? 'Added to dashboard' : 'Add to dashboard'}
                            </Button>
                            <Button
                              onClick={() => {
                                const newSummaries = { ...filingSummaries }
                                delete newSummaries[filingId]
                                setFilingSummaries(newSummaries)
                              }}
                              color="ghost"
                              size="sm"
                              className="text-gray-400 hover:text-red-400 transition-colors p-2"
                            >
                              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                              </svg>
                            </Button>
                          </div>
                        </div>
                        {meta?.investorFocus && (
                          <div className="mb-4 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-sm text-gray-200">
                            <p className="font-semibold text-white mb-1">Investor request</p>
                            <p>{meta.investorFocus}</p>
                          </div>
                        )}
                        {meta?.focusAreas && meta.focusAreas.length > 0 && (
                          <div className="mb-4 flex flex-wrap gap-2">
                            {meta.focusAreas.map(area => (
                              <span key={area} className="px-3 py-1 rounded-full bg-primary-500/10 text-primary-100 text-xs border border-primary-500/20">
                                {area}
                              </span>
                            ))}
                          </div>
                        )}
                        <EnhancedSummary content={summaryData.content} />
                      </div>
                    )
                  })
                })()}

                {latestAnalysis && latestAnalysis.ratios && (
                  <FinancialCharts ratios={latestAnalysis.ratios} />
                )}
              </div>
            )}

            {/* Filings Tab */}
            {selectedTab === 'filings' && (
              <div>
                <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                  <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  SEC Filings
                </h2>
                {filingsLoading ? (
                  <div className="text-center py-12">
                    <div className="spinner mx-auto mb-4"></div>
                    <p className="text-gray-300">Loading filings...</p>
                  </div>
                ) : filings && filings.length > 0 ? (
                  <div className="space-y-4">
                    {filings.map((filing: any) => {
                      const inlineSummary = filingSummaries[filing.id]
                      return (
                        <div key={filing.id} className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/40">
                        <div className="flex justify-between items-start gap-4">
                          <div className="flex-1">
                            <h3 className="text-lg font-bold text-white mb-2">{filing.filing_type}</h3>
                            <div className="flex flex-wrap gap-3 text-sm">
                              <span className="text-gray-400">
                                📅 {new Date(filing.filing_date).toLocaleDateString()}
                              </span>
                              <span className="px-2 py-1 rounded bg-primary-500/20 text-primary-300 text-xs font-semibold">
                                {filing.status}
                              </span>
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <Button
                              onClick={() => {
                                setSelectedFilingForSummary(filing.id)
                                scrollToSummaryCard()
                              }}
                              isLoading={loadingSummaries[filing.id]}
                              color="primary"
                              size="sm"
                              className="px-4 py-2 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700 text-sm"
                            >
                              {loadingSummaries[filing.id] ? 'Generating...' : 'AI Summary Options'}
                            </Button>
                            {filing.url && (
                              <a
                                href={resolveFilingUrl(filing.url)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="px-4 py-2 text-primary-400 hover:text-primary-300 text-sm border-2 border-primary-500/30 hover:border-primary-500/60 rounded-lg font-semibold transition-all flex items-center gap-2"
                              >
                                View Filing
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                                </svg>
                              </a>
                            )}
                          </div>
                        </div>
                        {inlineSummary && (
                          <div className="mt-6 pt-6 border-t border-white/10">
                            <div className="flex items-center gap-2 mb-3">
                              <svg className="w-4 h-4 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                              </svg>
                              <h4 className="font-bold text-white text-sm">
                                {inlineSummary.metadata.mode === 'custom' ? 'Custom AI Summary' : 'AI-Generated Summary'}
                              </h4>
                              {inlineSummary.metadata.targetLength && (
                                <span className="px-2 py-0.5 rounded-full bg-primary-500/10 text-primary-200 text-xs border border-primary-500/20">
                                  ~{inlineSummary.metadata.targetLength} words
                                </span>
                              )}
                            </div>
                            {inlineSummary.metadata.investorFocus && (
                              <p className="text-xs text-gray-400 mb-2">
                                Focus: {inlineSummary.metadata.investorFocus}
                              </p>
                            )}
                            <div className="prose-premium text-base">
                              <ReactMarkdown>{inlineSummary.content}</ReactMarkdown>
                            </div>
                          </div>
                        )}
                      </div>
                    )})}
                  </div>
                ) : (
                  <div className="text-center py-12 card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20">
                    <svg className="w-16 h-16 text-gray-500 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <p className="text-gray-400 text-lg">
                      No filings found. Click "Fetch Latest Filings" to retrieve them.
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* Analysis Tab */}
            {selectedTab === 'analysis' && (
              <div>
                <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                  <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                  </svg>
                  Financial Analysis
                </h2>
                {analysisToDisplay && analysisToDisplay.summary_md ? (
                  <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/30">
                    <EnhancedSummary content={analysisToDisplay.summary_md} />
                  </div>
                ) : (
                  <div className="text-center py-12 card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20">
                    <svg className="w-16 h-16 text-gray-500 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                    </svg>
                    <p className="text-gray-400 text-lg">
                      No analysis available yet. Run an analysis to see results.
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* Personas Tab */}
            {selectedTab === 'personas' && (
              <div className="space-y-6">
                <PersonaSelector
                  selectedPersonas={selectedPersonas}
                  onSelectionChange={setSelectedPersonas}
                />
                
                <div className="card-premium bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border-yellow-500/30">
                  <div className="flex items-start gap-3">
                    <svg className="w-6 h-6 text-yellow-400 flex-shrink-0 mt-1" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                    </svg>
                    <p className="text-sm text-yellow-200">
                      <strong className="font-bold">Disclaimer:</strong> All investor persona outputs are simulations based on publicly 
                      available writings and investment philosophies. They do not represent actual advice from 
                      these investors.
                    </p>
                  </div>
                </div>

                {latestAnalysis && latestAnalysis.investor_persona_summaries ? (
                  <div className="space-y-6">
                    {Object.entries(latestAnalysis.investor_persona_summaries).map(([personaId, data]: [string, any]) => (
                      <div key={personaId} className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/30 group">
                        <div className="flex justify-between items-start mb-6">
                          <div className="flex items-center gap-4">
                            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center text-2xl">
                              👤
                            </div>
                            <h3 className="text-2xl font-bold text-white">{data.persona_name}</h3>
                          </div>
                          <span className={`px-4 py-2 rounded-full text-sm font-bold shadow-premium ${
                            data.stance === 'Buy' ? 'bg-gradient-to-r from-green-600 to-emerald-600 text-white' :
                            data.stance === 'Sell' ? 'bg-gradient-to-r from-red-600 to-rose-600 text-white' :
                            'bg-gradient-to-r from-yellow-600 to-orange-600 text-white'
                          }`}>
                            {data.stance}
                          </span>
                        </div>
                        <div className="prose-premium mb-6">
                          <ReactMarkdown>{data.summary}</ReactMarkdown>
                        </div>
                        {data.key_points && data.key_points.length > 0 && (
                          <div className="pt-6 border-t border-white/10">
                            <h4 className="font-bold text-white mb-4 flex items-center gap-2">
                              <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                              </svg>
                              Key Points:
                            </h4>
                            <ul className="space-y-2">
                              {data.key_points.map((point: string, idx: number) => (
                                <li key={idx} className="flex items-start gap-3 text-gray-300">
                                  <svg className="w-5 h-5 text-primary-400 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                                  </svg>
                                  <span>{point}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-12 card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20">
                    <svg className="w-16 h-16 text-gray-500 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                    </svg>
                    <p className="text-gray-400 text-lg">
                      No investor persona analysis available yet.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
