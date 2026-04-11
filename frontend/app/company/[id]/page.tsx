'use client'

/* eslint-disable @next/next/no-img-element -- Dynamic company logos use raw img fallbacks. */

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
  sectionInstructions: Record<string, string>
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
  sectionInstructions?: Record<string, string>
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
  tips: string[]
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
  gross_profit?: number | null
  operating_cash_flow?: number | null
  capex?: number | null
  total_debt?: number | null
  cash_and_equivalents?: number | null
  total_assets?: number | null
  eps_diluted?: number | null
  roe?: number | null
  roa?: number | null
  debt_to_equity?: number | null
  current_ratio?: number | null
  quick_ratio?: number | null
  inventory_turnover?: number | null
  days_sales_outstanding?: number | null
  fcf_margin?: number | null
  [key: string]: number | null | undefined
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
  degraded?: boolean
  degradedReason?: string | null
  warnings?: string[]
  contractWarnings?: string[]
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

const SUMMARY_EXPORT_IGNORE_SELECTOR = '[data-export-ignore="true"]'

const SUMMARY_CHART_METRICS: Array<{ key: keyof ChartDataPeriod; label: string; kind: 'currency' | 'percent' }> = [
  { key: 'revenue', label: 'Revenue', kind: 'currency' },
  { key: 'operating_income', label: 'Operating Income', kind: 'currency' },
  { key: 'net_income', label: 'Net Income', kind: 'currency' },
  { key: 'free_cash_flow', label: 'Free Cash Flow', kind: 'currency' },
  { key: 'gross_margin', label: 'Gross Margin', kind: 'percent' },
  { key: 'operating_margin', label: 'Operating Margin', kind: 'percent' },
  { key: 'net_margin', label: 'Net Margin', kind: 'percent' },
]

const formatCompactCurrency = (value: number) => {
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `$${(value / 1_000).toFixed(1)}K`
  return `$${value.toFixed(0)}`
}

const formatPercentMetric = (value: number) => {
  const normalized = Math.abs(value) <= 1.2 ? value * 100 : value
  return `${normalized.toFixed(1)}%`
}

const toFiniteNumber = (value: unknown): number | null => {
  if (typeof value !== 'number') return null
  if (!Number.isFinite(value)) return null
  return value
}

const normalizePercentRatioValue = (value: number | null | undefined): number | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return Math.abs(value) > 1.2 ? value / 100 : value
}

const hasNumericValues = (value: Record<string, unknown> | null | undefined) =>
  !!value && Object.values(value).some((entry) => typeof entry === 'number' && Number.isFinite(entry))

type LegacyStoredAnalysisSnapshot = StoredAnalysisSnapshot & {
  filing_id?: string | null
  filing_type?: string | null
  filing_date?: string | null
  chart_data?: ChartData | null
  financial_ratios?: Record<string, number | null> | null
  summary_md?: string | null
  summary?: string | null
  selected_persona?: string | null
}

const getDerivedSummaryFilingId = (analysisId: string | null | undefined) => {
  if (!analysisId || !analysisId.startsWith('summary-')) return null
  const derived = analysisId.slice('summary-'.length).trim()
  return derived || null
}

const normalizeStoredAnalysisSnapshot = (
  snapshot: StoredAnalysisSnapshot | null,
  analysisId: string | null | undefined,
): StoredAnalysisSnapshot | null => {
  if (!snapshot) return null
  const legacy = snapshot as LegacyStoredAnalysisSnapshot
  const inferredSource = (analysisId && analysisId.startsWith('summary-')) ? 'summary' : 'analysis'
  return {
    ...snapshot,
    summaryMd: snapshot.summaryMd ?? legacy.summary_md ?? legacy.summary ?? null,
    filingId: snapshot.filingId ?? legacy.filing_id ?? getDerivedSummaryFilingId(analysisId),
    filingType: snapshot.filingType ?? legacy.filing_type ?? null,
    filingDate: snapshot.filingDate ?? legacy.filing_date ?? null,
    selectedPersona: snapshot.selectedPersona ?? legacy.selected_persona ?? null,
    chartData: snapshot.chartData ?? legacy.chart_data ?? null,
    ratios: snapshot.ratios ?? legacy.financial_ratios ?? null,
    source: snapshot.source ?? inferredSource,
  }
}

const snapshotWasNormalized = (
  snapshot: StoredAnalysisSnapshot,
  normalized: StoredAnalysisSnapshot,
) =>
  snapshot.summaryMd !== normalized.summaryMd ||
  snapshot.filingId !== normalized.filingId ||
  snapshot.filingType !== normalized.filingType ||
  snapshot.filingDate !== normalized.filingDate ||
  snapshot.selectedPersona !== normalized.selectedPersona ||
  snapshot.chartData !== normalized.chartData ||
  snapshot.ratios !== normalized.ratios ||
  snapshot.source !== normalized.source

const buildRatiosFromChartData = (
  chartData: ChartData | null | undefined,
): Record<string, number | null> | null => {
  const current = chartData?.current_period
  if (!current) return null

  const resolved: Record<string, number | null> = {
    gross_margin: normalizePercentRatioValue(toFiniteNumber(current.gross_margin)),
    operating_margin: normalizePercentRatioValue(toFiniteNumber(current.operating_margin)),
    net_margin: normalizePercentRatioValue(toFiniteNumber(current.net_margin)),
    roe: normalizePercentRatioValue(toFiniteNumber(current.roe)),
    roa: normalizePercentRatioValue(toFiniteNumber(current.roa)),
    current_ratio: toFiniteNumber(current.current_ratio),
    quick_ratio: toFiniteNumber(current.quick_ratio),
    debt_to_equity: toFiniteNumber(current.debt_to_equity),
    inventory_turnover: toFiniteNumber(current.inventory_turnover),
    dso: toFiniteNumber(current.days_sales_outstanding),
  }

  return hasNumericValues(resolved) ? resolved : null
}

const buildFallbackChartDataFromCalculatedMetrics = (
  calculatedMetrics: Record<string, unknown> | null | undefined,
  filingType?: string | null,
): ChartData | null => {
  if (!calculatedMetrics) return null

  const currentPeriod: ChartData['current_period'] = {
    revenue: toFiniteNumber(calculatedMetrics.revenue),
    operating_income: toFiniteNumber(calculatedMetrics.operating_income),
    net_income: toFiniteNumber(calculatedMetrics.net_income),
    free_cash_flow: toFiniteNumber(calculatedMetrics.free_cash_flow),
    gross_margin: toFiniteNumber(calculatedMetrics.gross_margin),
    operating_margin: toFiniteNumber(calculatedMetrics.operating_margin),
    net_margin: toFiniteNumber(calculatedMetrics.net_margin),
    gross_profit: toFiniteNumber(calculatedMetrics.gross_profit),
    operating_cash_flow: toFiniteNumber(calculatedMetrics.operating_cash_flow),
    capex: toFiniteNumber(calculatedMetrics.capital_expenditures),
    total_debt: toFiniteNumber(calculatedMetrics.total_debt),
    cash_and_equivalents: toFiniteNumber(calculatedMetrics.cash),
    total_assets: toFiniteNumber(calculatedMetrics.total_assets),
    eps_diluted: toFiniteNumber(calculatedMetrics.diluted_eps),
    roe: toFiniteNumber(calculatedMetrics.roe),
    roa: toFiniteNumber(calculatedMetrics.roa),
    debt_to_equity: toFiniteNumber(calculatedMetrics.debt_to_equity),
    current_ratio: toFiniteNumber(calculatedMetrics.current_ratio),
    quick_ratio: toFiniteNumber(calculatedMetrics.quick_ratio),
    inventory_turnover: toFiniteNumber(calculatedMetrics.inventory_turnover),
    days_sales_outstanding: toFiniteNumber(calculatedMetrics.days_sales_outstanding ?? calculatedMetrics.dso),
    fcf_margin: toFiniteNumber(calculatedMetrics.fcf_margin),
  }

  if (!hasNumericValues(currentPeriod as Record<string, unknown>)) return null

  const isQuarterly = String(filingType || '').toUpperCase().includes('10-Q')
  return {
    current_period: currentPeriod,
    prior_period: null,
    period_type: isQuarterly ? 'quarterly' : 'annual',
    current_label: isQuarterly ? 'Current Quarter' : 'Current Year',
    prior_label: isQuarterly ? 'Prior Quarter' : 'Prior Year',
  }
}

const buildRatiosFromCalculatedMetrics = (
  calculatedMetrics: Record<string, unknown> | null | undefined,
  chartData: ChartData | null | undefined,
): Record<string, number | null> | null => {
  const fromChartData = buildRatiosFromChartData(chartData) ?? {}
  const merged: Record<string, number | null> = {
    ...fromChartData,
    net_debt_to_ebitda: toFiniteNumber(calculatedMetrics?.net_debt_to_ebitda),
    interest_coverage: toFiniteNumber(calculatedMetrics?.interest_coverage),
  }
  return hasNumericValues(merged) ? merged : null
}

const buildSummaryChartAppendix = (summary: FilingSummary) => {
  const chartData = summary.chartData
  if (!chartData?.current_period) return ''

  const currentLabel = chartData.current_label || 'Current'
  const priorLabel = chartData.prior_label || 'Prior'
  const lines = SUMMARY_CHART_METRICS.flatMap(({ key, label, kind }) => {
    const current = chartData.current_period[key]
    if (typeof current !== 'number' || !Number.isFinite(current)) return []

    const prior = chartData.prior_period?.[key]
    const formatter = kind === 'currency' ? formatCompactCurrency : formatPercentMetric
    const currentText = formatter(current)
    const priorText =
      typeof prior === 'number' && Number.isFinite(prior)
        ? `${formatter(prior)} (${priorLabel})`
        : 'N/A'

    return [`- ${label}: ${currentText} (${currentLabel}) | ${priorText}`]
  })

  if (lines.length === 0) return ''
  return `Chart Snapshot\n${lines.join('\n')}`
}

const writeTextToClipboard = async (value: string) => {
  try {
    await navigator.clipboard.writeText(value)
    return
  } catch (_err) {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    textarea.style.top = '0'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
}

const downloadBlob = (blob: Blob, filename: string) => {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

const stripCrossOriginImagesForExport = (doc: Document, origin: string) => {
  doc.querySelectorAll('img').forEach((img) => {
    const src = (img.getAttribute('src') || '').trim()
    if (!src) return
    if (src.startsWith('data:') || src.startsWith('blob:')) return
    try {
      const parsed = new URL(src, origin)
      if (parsed.origin !== origin) {
        img.remove()
      }
    } catch (_err) {
      img.remove()
    }
  })
}

const captureSummaryElement = async (element: HTMLElement, scale = 2) => {
  const { default: html2canvas } = await import('html2canvas')
  const currentOrigin = window.location.origin
  const boundedScale = Math.max(1, Math.min(2, scale))
  const area = Math.max(1, element.scrollWidth * element.scrollHeight)
  const maxPixels = 24_000_000
  const safeScale = Math.min(boundedScale, Math.sqrt(maxPixels / area))
  return html2canvas(element, {
    backgroundColor: '#ffffff',
    scale: Math.max(1, safeScale),
    useCORS: true,
    logging: false,
    onclone: (doc) => {
      doc.querySelectorAll(SUMMARY_EXPORT_IGNORE_SELECTOR).forEach((node) => node.remove())
      stripCrossOriginImagesForExport(doc, currentOrigin)
    },
  })
}

const exportSummaryElementToPdf = async (element: HTMLElement, filename: string) => {
  const scales = [2, 1.5, 1.25, 1]
  let canvas: HTMLCanvasElement | null = null
  let lastCaptureError: unknown = null
  for (const scale of scales) {
    try {
      canvas = await captureSummaryElement(element, scale)
      break
    } catch (error) {
      lastCaptureError = error
    }
  }
  if (!canvas) {
    throw (lastCaptureError ?? new Error('Unable to capture summary for PDF export'))
  }

  const { jsPDF } = await import('jspdf')
  const pdf = new jsPDF({
    orientation: 'portrait',
    unit: 'pt',
    format: 'letter',
    compress: true,
  })

  const margin = 36
  const pageWidth = pdf.internal.pageSize.getWidth()
  const pageHeight = pdf.internal.pageSize.getHeight()
  const usableWidth = pageWidth - margin * 2
  const usableHeight = pageHeight - margin * 2
  const pageCanvasHeight = Math.max(1, Math.floor((usableHeight * canvas.width) / usableWidth))

  let sourceY = 0
  let pageIndex = 0
  while (sourceY < canvas.height) {
    const sliceHeight = Math.min(pageCanvasHeight, canvas.height - sourceY)
    const pageCanvas = document.createElement('canvas')
    pageCanvas.width = canvas.width
    pageCanvas.height = sliceHeight

    const context = pageCanvas.getContext('2d')
    if (!context) {
      throw new Error('Failed to create canvas context for PDF export')
    }

    context.drawImage(canvas, 0, sourceY, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight)
    const pageImage = pageCanvas.toDataURL('image/png')
    const imageHeight = (sliceHeight * usableWidth) / canvas.width

    if (pageIndex > 0) {
      pdf.addPage()
    }

    pdf.addImage(pageImage, 'PNG', margin, margin, usableWidth, imageHeight, undefined, 'FAST')
    sourceY += sliceHeight
    pageIndex += 1
  }

  const blob = pdf.output('blob')
  downloadBlob(blob, filename)
}

const DEFAULT_TARGET_LENGTH_WORDS = 1000

const createDefaultSummaryPreferences = (): SummaryPreferenceFormState => ({
  mode: 'custom',
  sectionInstructions: {},
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
  return Math.max(300, Math.min(3000, Math.round(numericValue)))
}

const normalizeSectionInstructions = (instructions: Record<string, string>) => {
  const normalized = Object.fromEntries(
    Object.entries(instructions)
      .map(([key, value]) => [key, typeof value === 'string' ? value.trim() : ''])
      .filter(([, value]) => Boolean(value))
  ) as Record<string, string>

  return Object.keys(normalized).length > 0 ? normalized : undefined
}

const buildPreferencePayload = (prefs: SummaryPreferenceFormState): FilingSummaryPreferencesPayload | undefined => {
  // Determine if health score should be enabled (either via granular setting or wizard toggle)
  const isHealthEnabled = prefs.healthRating.enabled || prefs.includeHealthScore;
  const sectionInstructions = normalizeSectionInstructions(prefs.sectionInstructions)

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

  return {
    mode: 'custom',
    section_instructions: sectionInstructions,
    investor_focus: sectionInstructions
      ? Object.values(sectionInstructions).join('\n\n')
      : undefined,
    persona_id: prefs.selectedPersona || undefined,
    focus_areas: prefs.focusAreas.length ? prefs.focusAreas : undefined,
    tone: prefs.tone,
    detail_level: prefs.detailLevel,
    output_style: prefs.outputStyle,
    target_length: clampTargetLength(prefs.targetLength),
    complexity: prefs.complexity,
    health_rating: buildHealthRating(),
  }
}

const snapshotPreferences = (prefs: SummaryPreferenceFormState): SummaryPreferenceSnapshot => {
  const sectionInstructions = normalizeSectionInstructions(prefs.sectionInstructions)
  return {
    mode: 'custom',
    sectionInstructions,
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
  sectionInstructions: normalizeSectionInstructions(
    (
      (stored as Record<string, unknown>).sectionInstructions &&
      typeof (stored as Record<string, unknown>).sectionInstructions === 'object'
        ? (stored as Record<string, unknown>).sectionInstructions
        : {}
    ) as Record<string, string>
  ) ?? {},
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

const normalizeValidatedHealthScore = (value: unknown): number | null => {
  const parsed = parseNumericScore(value)
  if (parsed == null) return null
  if (parsed < 0 || parsed > 100) return null
  return parsed
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
  const [analysisFallbackChartData, setAnalysisFallbackChartData] = useState<ChartData | null>(null)
  const [analysisFallbackRatios, setAnalysisFallbackRatios] = useState<Record<string, number | null> | null>(null)
  const [loadingSummaries, setLoadingSummaries] = useState<Record<string, boolean>>({})
  const [activeSummaryProgressFilingId, setActiveSummaryProgressFilingId] = useState<string | null>(null)
  const [summaryProgress, setSummaryProgress] = useState<Record<string, SummaryProgressPayload>>({})
  const [filingSummaries, setFilingSummaries] = useState<Record<string, FilingSummary>>({})
  const [summaryErrorModal, setSummaryErrorModal] = useState<SummaryErrorModalState>({
    isOpen: false,
    title: '',
    message: '',
    tips: [],
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
  const summaryExportRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const inFlightSummaryRequestsRef = useRef<Set<string>>(new Set())
  const analysisExportRef = useRef<HTMLDivElement | null>(null)
  const preferencesHydratedRef = useRef(false)
  const isSummaryGenerating = selectedFilingForSummary ? !!loadingSummaries[selectedFilingForSummary] : false
  const queryClient = useQueryClient()
  const sliderLengthValue = Math.max(1, Math.min(3000, summaryPreferences.targetLength))
  const authPending = loading || !user
  const queriesEnabled = !!companyId && !authPending
  const fallbackTicker = searchParams?.get('ticker') ?? undefined
  const isLocalAnalysisId = currentAnalysisId?.startsWith('summary-') ?? false

  const setSummaryExportRef = useCallback((filingId: string, node: HTMLDivElement | null) => {
    if (node) {
      summaryExportRefs.current[filingId] = node
      return
    }
    delete summaryExportRefs.current[filingId]
  }, [])

  const openSummaryError = useCallback((error: any, fallbackMessage: string) => {
    const responseDetail = error?.response?.data?.detail
    const failureCode = responseDetail?.failure_code || error?.response?.data?.failure_code
    const targetLength = responseDetail?.target_length
    const actualWordCount = Number(responseDetail?.actual_word_count)
    const missingRequirements = (Array.isArray(responseDetail?.missing_requirements)
      ? responseDetail.missing_requirements
      : []).filter((item: unknown) => !/^_\w+:/.test(String(item || '')))
    const diagnosticMissingRequirements = Array.isArray(responseDetail?.diagnostic_missing_requirements)
      ? responseDetail.diagnostic_missing_requirements
      : []
    const sectionFailures = Array.isArray(responseDetail?.section_failures)
      ? responseDetail.section_failures
      : []
    const targetBand =
      responseDetail?.target_band && typeof responseDetail.target_band === 'object'
        ? responseDetail.target_band
        : null
    const contractScope = String(responseDetail?.contract_scope || '').trim()
    const hasKeyMetricsIssue = missingRequirements.some((item: unknown) => String(item || '').toLowerCase().includes('key metrics'))
    const hasQuoteIssue = missingRequirements.some((item: unknown) => String(item || '').toLowerCase().includes('quote'))
    const hasWordBandIssue = missingRequirements.some((item: unknown) => String(item || '').toLowerCase().includes('word-count band violation'))

    let message = ''
    let tips: string[] = []
    if (failureCode === 'SUMMARY_ONE_SHOT_CONTRACT_FAILED') {
      const isMicro = contractScope === 'micro_exact'
      const bandLine =
        targetBand &&
        Number.isFinite(Number(targetBand.lower)) &&
        Number.isFinite(Number(targetBand.upper))
          ? `Required final band: ${Math.round(Number(targetBand.lower))}-${Math.round(Number(targetBand.upper))} words.`
          : ''
      const actualLine = Number.isFinite(actualWordCount)
        ? `Actual summary length: ${Math.round(actualWordCount)} words.`
        : ''
      const bandTolerance =
        targetBand &&
        Number.isFinite(Number(targetBand.lower)) &&
        Number.isFinite(Number(targetBand.upper)) &&
        Number.isFinite(Number(targetLength))
          ? Math.max(
              Math.abs(Math.round(Number(targetLength)) - Math.round(Number(targetBand.lower))),
              Math.abs(Math.round(Number(targetBand.upper)) - Math.round(Number(targetLength))),
            )
          : 20
      const fatalBlock = missingRequirements.length
        ? `\n\nFatal requirements not met:\n- ${missingRequirements.slice(0, 6).join('\n- ')}`
        : ''
      const diagnosticBlock = diagnosticMissingRequirements.length
        ? `\n\nDiagnostics:\n- ${diagnosticMissingRequirements.slice(0, 6).join('\n- ')}`
        : ''
      const baseLine = isMicro
        ? (targetLength
            ? `Unable to satisfy the exact ${targetLength}-word one-shot summary contract.`
            : 'Unable to satisfy the exact one-shot summary contract.')
        : (targetLength
            ? `Unable to satisfy the one-shot summary contract within ${bandTolerance} words of the ${targetLength}-word target without retries.`
            : 'Unable to satisfy the one-shot summary contract without retries.')
      message = [baseLine, actualLine, bandLine].filter(Boolean).join(' ') + fatalBlock + diagnosticBlock
      tips = [
        'Retry once in case the generation pass had a temporary model hiccup.',
        'If it keeps failing, lower the target length slightly and try again.',
      ]
    } else if (failureCode === 'SUMMARY_CONTRACT_FAILED') {
      const backendDetail = String(responseDetail?.detail || '').trim()
      const contractLine = backendDetail.startsWith('Unable to satisfy')
        ? backendDetail
        : targetLength
          ? `Unable to satisfy ${targetLength}-word narrative+quote contract from current filing evidence.`
          : 'Unable to satisfy strict long-form narrative+quote contract from current filing evidence.'
      const requirementsBlock = missingRequirements.length
        ? `\n\nRequirements not met:\n- ${missingRequirements.slice(0, 6).join('\n- ')}`
        : ''
      message = `${contractLine}${requirementsBlock}`
      tips = [
        Number.isFinite(Number(targetLength)) && Number(targetLength) > 0
          ? `Try a shorter target length than ${Math.round(Number(targetLength))} words so the contract has less evidence to satisfy.`
          : 'Try a shorter target length so the contract has less evidence to satisfy.',
        hasQuoteIssue
          ? 'If quotes are missing, try a filing with richer management commentary, usually a 10-K or annual filing.'
          : 'Try a filing with richer management commentary if this one is sparse or heavily numeric.',
        hasKeyMetricsIssue
          ? 'If Key Metrics is underweight, use a filing with fuller financial statements or reduce the requested length.'
          : hasWordBandIssue
            ? 'If only the word band is failing, trim the target slightly and retry instead of refreshing the page.'
            : 'A refresh usually will not fix this one because it is a strict evidence/length contract failure.',
      ]
    } else if (failureCode === 'SUMMARY_SECTION_BALANCE_FAILED') {
      const sectionBlock = sectionFailures.length
        ? `\n\nFinal validation still failed:\n- ${sectionFailures
            .slice(0, 6)
            .map((item: any) => String(item?.message || item || '').trim())
            .filter(Boolean)
            .join('\n- ')}`
        : ''
      message = [
        targetLength
          ? `The ${targetLength}-word Continuous V2 summary got close, but final section-balance validation still failed.`
          : 'The Continuous V2 summary got close, but final section-balance validation still failed.',
        sectionBlock,
      ].join('')
      tips = [
        'Retry once in case a neighboring repair path lands cleanly on the next pass.',
        'If Risk Factors and Key Metrics keep failing together, lower the target length slightly and retry.',
        'If it still repeats, try a filing with richer management commentary and fuller statement detail.',
      ]
    } else if (failureCode === 'SUMMARY_CONTRACT_TIMEOUT') {
      const timeoutLine = targetLength
        ? `The explicit ${targetLength}-word summary target could not be satisfied before the timeout fallback finished.`
        : 'The explicit summary target could not be satisfied before the timeout fallback finished.'
      const requirementsBlock = missingRequirements.length
        ? `\n\nStill unresolved:\n- ${missingRequirements.slice(0, 4).join('\n- ')}`
        : ''
      message = `${timeoutLine}${requirementsBlock}`
      tips = [
        'Retry once in case the timeout was transient.',
        'If it happens again, lower the target length and retry.',
      ]
    } else if (failureCode === 'INSUFFICIENT_NUMERIC_KEY_METRICS') {
      const numericRowCount = Number(responseDetail?.numeric_row_count)
      const issueText = String(responseDetail?.issue || '').trim()
      const countLine = Number.isFinite(numericRowCount)
        ? `Detected numeric rows: ${Math.round(numericRowCount)}.`
        : ''
      message = [
        'The filing does not contain enough numeric evidence to build a strict Key Metrics section for this summary target.',
        countLine,
        issueText,
        'Try a shorter target length or a filing with richer financial statement data.',
      ].filter(Boolean).join(' ')
      tips = [
        'Choose a shorter target length for this filing.',
        'Or switch to a filing with fuller statement data, often a 10-K or annual report.',
      ]
    } else if (failureCode === 'SUMMARY_BUDGET_EXCEEDED') {
      const cap = Number(responseDetail?.budget_cap_usd)
      const estimatedMin = Number(responseDetail?.estimated_min_cost_usd)
      const projected = Number(responseDetail?.projected_cost_usd)
      const actualCost = Number(responseDetail?.actual_cost_usd)
      const hasActualCost = Number.isFinite(actualCost)
      const hasProjectedCost = Number.isFinite(projected)
      const hasEstimatedMin = Number.isFinite(estimatedMin)
      const stage = String(responseDetail?.stage || '').toLowerCase()
      const guidance = String(responseDetail?.guidance || '').trim()
      const suggestedTargetLength = Number(responseDetail?.suggested_target_length)
      const attemptedAdjustments = Array.isArray(responseDetail?.budget_adjustments_attempted)
        ? responseDetail.budget_adjustments_attempted
            .map((item: unknown) => String(item || '').trim())
            .filter(Boolean)
        : []
      const capLabel = Number.isFinite(cap) ? `$${cap.toFixed(2)}` : '$0.10'
      const stageLine = stage.includes('rewrite')
        ? 'The request exceeded budget during the rewrite pass.'
        : stage.includes('generation')
          ? 'The request exceeded budget during summary generation.'
          : stage.includes('preflight')
            ? 'The request is over budget before generation starts (preflight estimate).'
            : 'The request exceeded the strict summary budget cap.'
      const estimateSource = hasActualCost
        ? `Actual cost reached $${actualCost.toFixed(3)}.`
        : hasProjectedCost
          ? `Projected cost is $${projected.toFixed(3)}.`
          : hasEstimatedMin
            ? `Estimated minimum cost is $${estimatedMin.toFixed(3)}.`
            : ''
      const runtimeClarifier =
        Number.isFinite(cap) &&
        hasEstimatedMin &&
        estimatedMin < cap &&
        (hasActualCost || hasProjectedCost)
          ? 'Minimum-path estimate was below cap, but runtime generation/rewrite cost exceeded the cap.'
          : ''
      const adjustmentLabels = attemptedAdjustments
        .map((code: string) => {
          if (code === 'context_trimmed' || code === 'context_trimmed_token_budget' || code === 'context_trimmed_preflight_cost') {
            return 'trimmed filing context'
          }
          if (code === 'context_trimmed_data_window') {
            return 'trimmed non-core filing data window'
          }
          if (code === 'context_trimmed_after_research_skipped') {
            return 'trimmed filing context again after skipping research'
          }
          if (code === 'context_trimmed_after_non_core_drops') {
            return 'trimmed filing context again after non-core reductions'
          }
          if (code === 'research_compressed') {
            return 'compressed Agent-1 research'
          }
          if (code === 'research_skipped') {
            return 'skipped Agent-1 research'
          }
          if (code === 'spotlight_context_dropped') {
            return 'dropped spotlight context'
          }
          if (code === 'filing_snippets_reduced') {
            return 'reduced filing quote snippets'
          }
          if (code === 'filing_snippets_dropped') {
            return 'dropped filing quote snippets'
          }
          if (code === 'risk_factors_excerpt_reduced') {
            return 'reduced risk-factors appendix'
          }
          if (code === 'risk_factors_excerpt_dropped') {
            return 'dropped risk-factors appendix'
          }
          return ''
        })
        .filter(Boolean)
      const attemptedLine = adjustmentLabels.length
        ? `Backend already attempted: ${Array.from(new Set(adjustmentLabels)).join(', ')}.`
        : ''
      const suggestedLine =
        Number.isFinite(suggestedTargetLength) && suggestedTargetLength > 0
          ? `Suggested target length: ~${Math.round(suggestedTargetLength)} words.`
          : ''
      const guidanceLine = guidance || 'Try a shorter target length or reduce context size, then retry.'
      message = `${stageLine} Strict cap: ${capLabel}.${estimateSource ? ` ${estimateSource}` : ''}\n\n${runtimeClarifier}${runtimeClarifier && (attemptedLine || suggestedLine || guidanceLine) ? '\n' : ''}${attemptedLine}${attemptedLine && (suggestedLine || guidanceLine) ? '\n' : ''}${suggestedLine}${suggestedLine && guidanceLine ? '\n' : ''}${guidanceLine}`
      tips = [
        'Reduce the requested length or simplify the request, then retry.',
        'If available, use the suggested target length shown in the error.',
      ]
    } else {
      if (typeof responseDetail === 'string') {
        message = responseDetail
      } else if (responseDetail && typeof responseDetail === 'object') {
        try {
          message = JSON.stringify(responseDetail, null, 2)
        } catch {
          message = fallbackMessage || 'Failed to generate summary'
        }
      } else {
        message = String(error?.message || fallbackMessage || 'Failed to generate summary')
      }
    }
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

    if (!tips.length) {
      tips = looksLikeNetworkOrClientBlock
        ? [
            'Retry once (temporary network/API hiccups happen).',
            'Hard refresh the page, then try again.',
          ]
        : ['Retry once and, if it repeats, adjust the request before trying again.']
    }

    setSummaryErrorModal({
      isOpen: true,
      title: 'Couldn’t generate summary',
      message,
      tips,
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
    if (!currentAnalysisId || !userId) {
      setLocalAnalysisSnapshot(null)
      return
    }
    const history = DashboardStorage.loadAnalysisHistory(userId)
    const snapshot = history.find(item => item.analysisId === currentAnalysisId) ?? null
    const normalizedSnapshot = normalizeStoredAnalysisSnapshot(snapshot, currentAnalysisId)
    setLocalAnalysisSnapshot(normalizedSnapshot)

    if (snapshot && normalizedSnapshot && snapshotWasNormalized(snapshot, normalizedSnapshot)) {
      DashboardStorage.upsertAnalysisSnapshot(normalizedSnapshot, userId)
      emitDashboardSync()
    }
  }, [currentAnalysisId, userId])

  useEffect(() => {
    setAnalysisFallbackChartData(null)
    setAnalysisFallbackRatios(null)

    if (!isLocalAnalysisId) return
    if (!localAnalysisSnapshot) return
    const fallbackFilingId =
      localAnalysisSnapshot?.filingId ??
      getDerivedSummaryFilingId(currentAnalysisId)
    if (!fallbackFilingId) return

    const snapshotHasChartData = hasNumericValues(
      (localAnalysisSnapshot.chartData?.current_period as Record<string, unknown> | undefined) ?? undefined,
    )
    const snapshotHasRatios = hasNumericValues(
      (localAnalysisSnapshot.ratios as Record<string, unknown> | undefined) ?? undefined,
    )

    if (snapshotHasChartData || snapshotHasRatios) return

    let cancelled = false
    const filingId = String(fallbackFilingId)
    void (async () => {
      try {
        const response = await filingsApi.getFilingHealth(filingId)
        if (cancelled) return

        const calculatedMetrics =
          response?.data?.calculated_metrics && typeof response.data.calculated_metrics === 'object'
            ? (response.data.calculated_metrics as Record<string, unknown>)
            : null
        if (!calculatedMetrics) return

        const fallbackChartData = buildFallbackChartDataFromCalculatedMetrics(
          calculatedMetrics,
          localAnalysisSnapshot.filingType,
        )
        const fallbackRatios = buildRatiosFromCalculatedMetrics(calculatedMetrics, fallbackChartData)

        if (!fallbackChartData && !fallbackRatios) return
        if (cancelled) return

        setAnalysisFallbackChartData(fallbackChartData)
        setAnalysisFallbackRatios(fallbackRatios)

        if (userId) {
          DashboardStorage.upsertAnalysisSnapshot(
            {
              ...localAnalysisSnapshot,
              filingId,
              chartData: fallbackChartData ?? localAnalysisSnapshot.chartData ?? null,
              ratios: fallbackRatios ?? localAnalysisSnapshot.ratios ?? null,
            },
            userId,
          )
          emitDashboardSync()
        }
      } catch (error) {
        console.warn('Unable to hydrate chart fallback for snapshot analysis.', error)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [isLocalAnalysisId, localAnalysisSnapshot, currentAnalysisId, userId])

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

    const normalizedProxyBase = '/api/backend'
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
    if (inFlightSummaryRequestsRef.current.has(filingId)) {
      return
    }
    inFlightSummaryRequestsRef.current.add(filingId)

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

    const persistSummaryResponse = (
      response: any,
      responseMetadata?: SummaryPreferenceSnapshot,
    ) => {
      if (isMountedRef.current) {
        setSummaryProgress(prev => ({
          ...prev,
          [filingId]: { status: 'Complete', percent: 100, percent_exact: 100, eta_seconds: 0 },
        }))
      }

      // Extract health rating if present
      let healthRating: number | undefined
      let healthComponents: HealthComponentScores | undefined

      if (response.data.health_score !== undefined) {
        healthRating = normalizeValidatedHealthScore(response.data.health_score) ?? undefined
      }

      if (response.data.health_components) {
        healthComponents = response.data.health_components
      }

      // Capture dynamic weights, descriptions, and metrics from user settings
      const healthComponentWeights = response.data.health_component_weights
      const healthComponentDescriptions = response.data.health_component_descriptions
      const healthComponentMetrics = response.data.health_component_metrics
      const companyCountry = response.data.company_country ?? null
      const chartData = response.data.chart_data ?? null
      const degraded = Boolean(response.data.degraded)
      const degradedReason = typeof response.data.degraded_reason === 'string' ? response.data.degraded_reason : null
      const warnings = Array.isArray(response.data.warnings)
        ? response.data.warnings.map((item: unknown) => String(item))
        : []
      const contractWarnings = Array.isArray(response.data.contract_warnings)
        ? response.data.contract_warnings.map((item: unknown) => String(item))
        : []

      if (isMountedRef.current) {
        const generatedAt = new Date().toISOString()
        setFilingSummaries(prev => ({
          ...prev,
          [filingId]: {
            content: response.data.summary,
            metadata: responseMetadata ?? metadata ?? { mode: 'custom' },
            generatedAt,
            companyCountry,
            degraded,
            degradedReason,
            warnings,
            contractWarnings,
            healthRating,
            healthComponents,
            healthComponentWeights,
            healthComponentDescriptions,
            healthComponentMetrics,
            chartData,
          },
        }))
        if (userId) {
          DashboardStorage.appendSummaryEvent(
            {
              eventId: `summary:${filingId}:${generatedAt}`,
              generatedAt,
              kind: 'summary',
              filingId,
              companyId: company?.id ?? null,
            },
            userId,
          )
          setTimeout(emitDashboardSync, 2000)
        }
      }
    }

    try {
      const response = await filingsApi.summarizeFiling(filingId, preferences)
      persistSummaryResponse(response, metadata)
    } catch (error: any) {
      if (isMountedRef.current) {
        openSummaryError(error, 'Failed to generate summary')
      }
    } finally {
      isPolling = false
      inFlightSummaryRequestsRef.current.delete(filingId)
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
    const healthScore = normalizeValidatedHealthScore(summary.healthRating)
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
      chartData: summary.chartData ?? null,
      ratios: null,
    }, userId)
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

    const chartAppendix = buildSummaryChartAppendix(summary)
    const clipboardText = chartAppendix
      ? `${summary.content.trim()}\n\n${chartAppendix}`
      : summary.content

    await writeTextToClipboard(clipboardText)

    setCopiedSummaries(prev => ({ ...prev, [filingId]: true }))
    window.setTimeout(() => clearCopiedFlag(filingId), 1500)
  }

  const handleCopyAnalysis = async () => {
    const content = analysisContent
    if (!content) return

    await writeTextToClipboard(content)

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
    let clientPdfCaptureError: unknown = null

    setExportingSummaries(prev => ({ ...prev, [filingId]: format }))
    try {
      const exportNode = summaryExportRefs.current[filingId]
      if (format === 'pdf' && exportNode) {
        try {
          await exportSummaryElementToPdf(exportNode, filename)
          return
        } catch (captureError) {
          clientPdfCaptureError = captureError
          // Continue to backend export fallback so users still get a file.
          console.warn('Client PDF capture failed; falling back to backend export.', captureError)
        }
      }

      const response = await filingsApi.exportSummary(filingId, {
        format,
        title: `${companyLabel} ${filingType} Brief`,
        summary: summary.content,
        filing_type: filingType,
        filing_date: filingDate || undefined,
        generated_at: summary.generatedAt || undefined,
      })

      const blob = response.data as Blob
      downloadBlob(blob, filename)
    } catch (error: any) {
      const captureMessage =
        clientPdfCaptureError instanceof Error ? clientPdfCaptureError.message : undefined
      const message =
        error?.response?.data?.detail ||
        error?.message ||
        captureMessage ||
        'Failed to export summary'
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
    const content = analysisContent
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
    let clientPdfCaptureError: unknown = null

    setExportingAnalysis(format)

    try {
      if (format === 'pdf' && analysisExportRef.current) {
        try {
          await exportSummaryElementToPdf(analysisExportRef.current, filename)
          return
        } catch (captureError) {
          clientPdfCaptureError = captureError
          // Fall back to backend export so the user still gets a file.
          console.warn('Client analysis PDF capture failed; falling back to backend export.', captureError)
        }
      }

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
      downloadBlob(blob, filename)
    } catch (error: any) {
      const captureMessage =
        clientPdfCaptureError instanceof Error ? clientPdfCaptureError.message : undefined
      const message =
        error?.response?.data?.detail ||
        error?.message ||
        captureMessage ||
        'Failed to export analysis'
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
      // Fetch enough history so the user can select the oldest available filings.
      // Default backend pagination is 50 (≈10 years of 10-Q/10-K), which hides older history.
      const response = await filingsApi.listCompanyFilings(companyId, { limit: 1000, offset: 0 })
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
        normalizeValidatedHealthScore(analysis.health_score)
        ?? normalizeValidatedHealthScore(analysis.healthScore)
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
        selectedPersona: analysis.selected_persona ?? analysis.selectedPersona ?? null,
        chartData: analysis.chart_data ?? analysis.chartData ?? null,
        ratios: analysis.ratios ?? analysis.financial_ratios ?? null,
        source: 'analysis',
      }, userId)
      DashboardStorage.appendSummaryEvent(
        {
          eventId: `analysis:${String(identifier)}`,
          generatedAt,
          kind: 'analysis',
          analysisId: String(identifier),
          companyId: company.id,
        },
        userId,
      )
      setTimeout(emitDashboardSync, 2000)
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
      if (userId && response.data.analysis_id) {
        DashboardStorage.appendSummaryEvent(
          {
            eventId: `analysis:${response.data.analysis_id}`,
            generatedAt: new Date().toISOString(),
            kind: 'analysis',
            analysisId: response.data.analysis_id,
            companyId: companyId ?? null,
          },
          userId,
        )
        setTimeout(emitDashboardSync, 2000)
      }
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

  const analysisFromListMatch = useMemo(() => {
    if (!currentAnalysisId || !analyses?.length) return null
    return analyses.find((analysis: any) => String(analysis?.id || '') === String(currentAnalysisId)) ?? null
  }, [analyses, currentAnalysisId])

  const analysisFromSnapshot = useMemo(() => {
    if (!localAnalysisSnapshot) return null
    return {
      id: localAnalysisSnapshot.analysisId,
      summary_md: localAnalysisSnapshot.summaryMd ?? null,
      investor_persona_summaries: null,
      ratios: localAnalysisSnapshot.ratios ?? null,
      chart_data: localAnalysisSnapshot.chartData ?? null,
      selected_persona: localAnalysisSnapshot.selectedPersona ?? null,
    }
  }, [localAnalysisSnapshot])

  const analysisToDisplay = useMemo(() => {
    if (currentAnalysisId) {
      if (!isLocalAnalysisId && currentAnalysis) return currentAnalysis
      if (analysisFromSnapshot) return analysisFromSnapshot
      if (!isLocalAnalysisId && analysisFromListMatch) return analysisFromListMatch
      return null
    }
    return latestAnalysis
  }, [
    currentAnalysisId,
    isLocalAnalysisId,
    currentAnalysis,
    analysisFromSnapshot,
    analysisFromListMatch,
    latestAnalysis,
  ])

  const analysisContent = useMemo(
    () =>
      (analysisToDisplay as any)?.summary_md ||
      (analysisToDisplay as any)?.summaryMd ||
      (analysisToDisplay as any)?.content ||
      '',
    [analysisToDisplay],
  )

  const analysisChartData = useMemo(
    () =>
      ((analysisToDisplay as any)?.chart_data ||
        (analysisToDisplay as any)?.chartData ||
        localAnalysisSnapshot?.chartData ||
        analysisFallbackChartData) as ChartData | null | undefined,
    [analysisToDisplay, localAnalysisSnapshot, analysisFallbackChartData],
  )

  const analysisRatios = useMemo(() => {
    const explicitRatios =
      ((analysisToDisplay as any)?.financial_ratios ||
        (analysisToDisplay as any)?.ratios ||
        localAnalysisSnapshot?.ratios ||
        analysisFallbackRatios) as Record<string, number | null> | null | undefined
    if (explicitRatios && hasNumericValues(explicitRatios as Record<string, unknown>)) {
      return explicitRatios
    }
    return buildRatiosFromChartData(analysisChartData)
  }, [analysisToDisplay, localAnalysisSnapshot, analysisFallbackRatios, analysisChartData])

  const analysisPersona = useMemo(() => {
    const personaId =
      ((analysisToDisplay as any)?.selected_persona ||
        (analysisToDisplay as any)?.selectedPersona ||
        localAnalysisSnapshot?.selectedPersona) as string | null | undefined
    if (!personaId) return null
    return INVESTOR_PERSONAS.find((persona) => persona.id === personaId) ?? null
  }, [analysisToDisplay, localAnalysisSnapshot])

  // Get the most recent health display data - prefer from filing summaries (has user preferences) over base analysis
  const currentHealthDisplay = useMemo(() => {
    // 1) Prefer the currently selected filing (if it already has a generated summary)
    const selectedSummary = selectedFilingForSummary
      ? filingSummaries[selectedFilingForSummary]
      : undefined
    if (selectedSummary) {
      return {
        score: normalizeValidatedHealthScore(selectedSummary.healthRating),
        components: selectedSummary.healthComponents,
        weights: selectedSummary.healthComponentWeights,
        descriptions: selectedSummary.healthComponentDescriptions,
        metrics: selectedSummary.healthComponentMetrics,
      }
    }

    // 2) Otherwise, use the most recently generated summary (by timestamp)
    let newest: FilingSummary | null = null
    for (const [, summary] of Object.entries(filingSummaries)) {
      if (!newest || summary.generatedAt > newest.generatedAt) newest = summary
    }
    if (newest) {
      return {
        score: normalizeValidatedHealthScore(newest.healthRating),
        components: newest.healthComponents,
        weights: newest.healthComponentWeights,
        descriptions: newest.healthComponentDescriptions,
        metrics: newest.healthComponentMetrics,
      }
    }

    // Fall back to latestAnalysis data
    return {
      score: normalizeValidatedHealthScore(latestAnalysis?.health_score),
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
    const candidate = currentAnalysis ?? analysisFromSnapshot ?? latestAnalysis
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
              {summaryErrorModal.tips.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
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
                      <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed">
                        Length targets are soft by default. If strict contract mode is not enabled, summaries return the
                        best full draft with warnings instead of hard-failing.
                      </p>
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
                            const summaryHealthScore = normalizeValidatedHealthScore(summary.healthRating)
                            
                            return (
                              <motion.div
                                key={fid}
                                initial={{ opacity: 0, y: 20 }}
                                whileInView={{ opacity: 1, y: 0 }}
                                viewport={{ once: true, margin: "-50px" }}
                                transition={{ duration: 0.4, delay: idx * 0.08 }}
                                className="group"
                              >
                                <div
                                  ref={(node) => setSummaryExportRef(fid, node)}
                                  data-summary-export-root="true"
                                  className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-0 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] overflow-hidden transition-all duration-300 group-hover:shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] dark:group-hover:shadow-[8px_8px_0px_0px_rgba(255,255,255,1)] group-hover:-translate-y-1"
                                >
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
                                          {summaryHealthScore != null && (
                                            <HealthScoreBadge
                                              score={summaryHealthScore}
                                              band={scoreToRating(summaryHealthScore).label}
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
                                      
                                      <div className="flex flex-wrap gap-2 justify-end" data-export-ignore="true">
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
                                      {(summary.degraded || (summary.contractWarnings?.length ?? 0) > 0) && (
                                        <div className="mb-4 border-2 border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 not-prose">
                                          <p className="font-semibold">
                                            {summary.degradedReason === 'timeout'
                                              ? 'Summary completed with timeout fallback.'
                                              : 'Summary completed (soft target mode).'}
                                          </p>
                                          {(summary.contractWarnings?.length ?? 0) > 0 && (
                                            <p className="mt-1">
                                              Soft-target warnings: {summary.contractWarnings?.slice(0, 3).join(' | ')}
                                            </p>
                                          )}
                                        </div>
                                      )}
                                      <EnhancedSummary
                                        content={summary.content}
                                        persona={summary.metadata?.selectedPersona ? INVESTOR_PERSONAS.find(p => p.id === summary.metadata.selectedPersona) : null}
                                        chartData={summary.chartData}
                                        healthData={{
                                          score: summaryHealthScore ?? undefined,
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
                    Filings
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
                      <div className="flex flex-wrap gap-2 justify-end" data-export-ignore="true">
                        <BrutalButton
                          onClick={handleManualDashboardUpdate}
                          variant="outline-rounded"
                          className="text-xs"
                        >
                          Update Dashboard
                        </BrutalButton>
                        <BrutalButton
                          onClick={handleCopyAnalysis}
                          disabled={!analysisContent}
                          className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-yellow-300 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)] disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {copiedAnalysis ? 'Copied' : 'Copy'}
                        </BrutalButton>
                        <BrutalButton
                          onClick={() => handleExportAnalysis('pdf')}
                          disabled={!analysisContent || !!exportingAnalysis}
                          className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white bg-blue-300 text-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)] disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {exportingAnalysis === 'pdf' ? 'Exporting...' : 'Export PDF'}
                        </BrutalButton>
                        <BrutalButton
                          onClick={() => handleExportAnalysis('docx')}
                          disabled={!analysisContent || !!exportingAnalysis}
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
                    <div ref={analysisExportRef} className="space-y-8">
                      {/* Financial Charts */}
                      {analysisRatios && (
                        <FinancialCharts ratios={analysisRatios} />
                      )}

                      {/* Analysis Content */}
                      <div className="border-t-2 border-gray-100 dark:border-gray-800 pt-8">
                        {analysisChartData?.current_period ? (
                          <EnhancedSummary
                            content={analysisContent}
                            persona={analysisPersona}
                            chartData={analysisChartData}
                          />
                        ) : (
                          <ReactMarkdown
                            className="max-w-none text-base leading-relaxed font-mono space-y-2"
                            components={summaryMarkdownComponents as any}
                          >
                            {analysisContent}
                          </ReactMarkdown>
                        )}
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
