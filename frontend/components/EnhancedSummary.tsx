'use client'

import ReactMarkdown from 'react-markdown'
import React, { useMemo, useCallback, useRef, useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CollapsibleSummary } from './ui/CollapsibleSummary'
import { cn } from '@/lib/utils'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { filingsApi } from '@/lib/api-client'
// Import new premium chart components
import { ChartOrchestrator, type KPIData } from './charts/kpi'
import { normalizePercentageValue } from '@/lib/chart-utils'

export interface CompanyKPI {
  name: string
  value: number
  prior_value?: number | null
  unit?: string
  description?: string
  company_specific?: boolean
  chart_type?: string
  period_label?: string
  prior_period_label?: string
  source_filing_id?: string
  history?: { period_label: string; value: number }[]
  // For charts that need segments/breakdown
  segments?: { label: string; value: number; color?: string }[]
  source_quote?: string
}

export interface ChartData {
  current_period: {
    revenue?: number | null
    operating_income?: number | null
    net_income?: number | null
    free_cash_flow?: number | null
    operating_margin?: number | null
    net_margin?: number | null
    gross_margin?: number | null
    // Additional metrics
    gross_profit?: number | null
    ebitda?: number | null
    operating_cash_flow?: number | null
    capex?: number | null
    total_debt?: number | null
    cash_and_equivalents?: number | null
    total_assets?: number | null
    total_equity?: number | null
    eps?: number | null
    eps_diluted?: number | null
    rnd_expense?: number | null
    sga_expense?: number | null
    roe?: number | null
    roa?: number | null
    roic?: number | null
    debt_to_equity?: number | null
    current_ratio?: number | null
    ebitda_margin?: number | null
    fcf_margin?: number | null
    [key: string]: number | null | undefined
  }
  prior_period?: {
    revenue?: number | null
    operating_income?: number | null
    net_income?: number | null
    free_cash_flow?: number | null
    operating_margin?: number | null
    net_margin?: number | null
    gross_margin?: number | null
    gross_profit?: number | null
    ebitda?: number | null
    operating_cash_flow?: number | null
    capex?: number | null
    total_debt?: number | null
    cash_and_equivalents?: number | null
    total_assets?: number | null
    total_equity?: number | null
    eps?: number | null
    eps_diluted?: number | null
    rnd_expense?: number | null
    sga_expense?: number | null
    roe?: number | null
    roa?: number | null
    roic?: number | null
    debt_to_equity?: number | null
    current_ratio?: number | null
    ebitda_margin?: number | null
    fcf_margin?: number | null
    [key: string]: number | null | undefined
  } | null
  period_type: 'quarterly' | 'annual'
  current_label: string
  prior_label: string
  company_kpi?: CompanyKPI | null
  company_charts?: CompanyKPI[] | null
}

export interface HealthDisplayData {
  score?: number
  components?: Record<string, number>
  weights?: Record<string, number>
  descriptions?: Record<string, string>
  metrics?: Record<string, string>
}

interface EnhancedSummaryProps {
  content: string
  persona?: {
    name: string
    image: string
    tagline: string
  } | null
  chartData?: ChartData | null
  healthData?: HealthDisplayData | null
  filingId?: string | null
}

// Format currency values
function formatCurrency(value: number, currencySymbol: string = '$'): string {
  const symbol = currencySymbol?.trim() ? currencySymbol : '$'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${symbol}${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${symbol}${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${symbol}${(value / 1_000).toFixed(1)}K`
  return `${symbol}${value.toFixed(0)}`
}

// Format compact number
function formatCompact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(0)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(0)}K`
  return value.toFixed(0)
}

function formatKpiValue(value: number, unit?: string): string {
  if (unit === '%') return `${value.toFixed(1)}%`
  if (unit === 'M') return `${value.toFixed(1)}M`
  if (unit === 'B') return `${value.toFixed(2)}B`
  if (unit === '$' || unit === '€' || unit === '£') return formatCurrency(value, unit)
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(0)
}

function formatSourceQuoteForDisplay(kpi: CompanyKPI): { display: string; full: string } {
  const full = (kpi.source_quote || '').trim()
  if (!full) return { display: '', full: '' }

  const normalized = full.replace(/\s+/g, ' ').trim()
  const parts = normalized
    .split('|')
    .map(p => p.trim())
    .filter(Boolean)

  const nameTokens = (kpi.name || '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]+/g, ' ')
    .split(' ')
    .map(t => t.trim())
    .filter(t => t.length >= 4)

  const pick =
    parts.find(p => nameTokens.some(t => p.toLowerCase().includes(t))) ||
    parts.find(p =>
      /customers?|accounts?|installed base|units shipped|shipments|orders|transactions|subscribers?|mau|dau/i.test(p)
    ) ||
    parts[0] ||
    normalized

  const clipped = pick.length > 180 ? `${pick.slice(0, 180).trim()}…` : pick
  return { display: clipped, full: normalized }
}

// Calculate change between periods
function calcChange(current: number, prior: number): { value: number; display: string } | null {
  if (prior === 0) return null
  const change = ((current - prior) / Math.abs(prior)) * 100
  return {
    value: change,
    display: `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`
  }
}

type CompanyChartType = 'comparison' | 'trend' | 'breakdown'

const COMPANY_CHART_TYPE_ALIASES: Record<string, CompanyChartType> = {
  donut: 'breakdown',
  doughnut: 'breakdown',
  ring: 'breakdown',
  pie: 'breakdown',
  breakdown: 'breakdown',
  mix: 'breakdown',
  bar: 'comparison',
  column: 'comparison',
  compare: 'comparison',
  comparison: 'comparison',
  delta: 'comparison',
  trend: 'trend',
  sparkline: 'trend',
  line: 'trend',
  series: 'trend',
  radial: 'comparison',
  circle: 'comparison',
  gauge: 'comparison',
  progress: 'comparison',
  meter: 'comparison',
}

function normalizeCompanyChartType(value?: string | null): CompanyChartType | undefined {
  if (!value) return undefined
  const key = value.toLowerCase().trim()
  return COMPANY_CHART_TYPE_ALIASES[key]
}

function resolveCompanyChartType(kpi: CompanyKPI): CompanyChartType {
  const explicit = normalizeCompanyChartType(kpi.chart_type)
  
  // Helper to check if segments are valid for donut chart
  const hasValidSegments = kpi.segments && 
    kpi.segments.length >= 2 && 
    kpi.segments.reduce((sum, s) => sum + (s.value || 0), 0) > 0
  const hasHistory = Array.isArray(kpi.history) && kpi.history.length >= 3
  
  if (explicit) {
    // Validate explicit choice so we don't render misleading charts.
    if (explicit === 'breakdown' && !hasValidSegments) {
      return hasHistory ? 'trend' : 'comparison'
    }
    if (explicit === 'trend' && !hasHistory) {
      return hasValidSegments ? 'breakdown' : 'comparison'
    }
    return explicit
  }
  if (hasValidSegments) return 'breakdown'
  if (hasHistory) return 'trend'
  return 'comparison'
}

function normalizeKpiUnit(value?: string | null): string | undefined {
  if (!value) return undefined
  const normalized = value.trim()
  const lower = normalized.toLowerCase()
  if (['percent', 'percentage', 'pct', '%'].includes(lower)) return '%'
  if (['usd', '$', 'dollars', 'us$', 'us dollars'].includes(lower)) return '$'
  if (['eur', '€', 'euro', 'euros'].includes(lower)) return '€'
  if (['gbp', '£', 'pound', 'pounds', 'sterling'].includes(lower)) return '£'
  // Never treat scale words as the unit; the backend should encode scale in the value itself.
  if (['thousand', 'thousands', 'million', 'millions', 'billion', 'billions', 'trillion', 'trillions'].includes(lower)) return undefined
  return normalized
}

const GENERIC_COMPANY_KPI_NAMES = new Set([
  'revenue',
  'total revenue',
  'net revenue',
  'revenue growth',
  'sales growth',
  'gross profit',
  'operating income',
  'net income',
  'operating margin',
  'net margin',
  'gross margin',
  'fcf',
  'free cash flow',
  'ocf',
  'operating cash flow',
  'cfo',
  'cash from operations',
  'cash flow',
  'ebitda',
  'eps',
  'earnings per share',
  'diluted eps',
  'capital expenditure',
  'capital expenditures',
  'capex',
  'cash position',
  'cash and equivalents',
  'cash',
  'total debt',
  'net cash',
  'capital returned',
  'capital return',
  'capital allocation',
  'capital deployment',
  'return of capital',
  'shareholder return',
  'shareholder returns',
  'total shareholder return',
  'tsr',
  'share repurchases',
  'stock repurchases',
  'repurchases',
  'buybacks',
  'buyback',
  'dividends',
  'dividends paid',
  'dividend yield',
  'payout ratio',
  'repurchase yield',
  'return on equity',
  'return on assets',
  'return on capital',
  'return on invested capital',
  'roe',
  'roa',
  'roic',
  'net cash cash debt',
])

const GENERIC_COMPANY_KPI_PATTERNS: RegExp[] = [
  /\bfree cash flow\b/,
  /\bfcf\b/,
  /\boperating cash flow\b/,
  /\bocf\b/,
  /\bcfo\b/,
  /\bcash from operations\b/,
  /\bcash flow\b/,
  /\bebitda\b/,
  /\bgross profit\b/,
  /\boperating income\b/,
  /\bnet income\b/,
  /\bearnings per share\b/,
  /\beps\b/,
  /\bcapital expenditures?\b/,
  /\bcapex\b/,
  /\btotal debt\b/,
  /\bcash position\b/,
  /\bcash and equivalents\b/,
  /\bnet cash\b/,
  /\brevenue growth\b/,
  /\bsales growth\b/,
  /\bshareholder returns?\b/,
  /\btotal shareholder return\b/,
  /\btsr\b/,
  /\breturn on equity\b/,
  /\breturn on assets\b/,
  /\breturn on capital\b/,
  /\breturn on invested capital\b/,
  /\broic\b/,
  /\broe\b/,
  /\broa\b/,
  /\bdividend yield\b/,
  /\bpayout ratio\b/,
  /\brepurchase yield\b/,
  /\breturn of capital\b/,
  /\bcapital allocation\b/,
  /\bcapital deployment\b/,
  /\bshareholder distributions?\b/,
  /\bcapital return(?:ed)?\b/,
  /\bbuybacks?\b/,
  /\brepurchase(?:s|d)?\b/,
  /\bshare repurchase(?:s|d)?\b/,
  /\bstock repurchase(?:s|d)?\b/,
  /\bdividends?\b/,
  // Generic accounting disclosures (not company "signature" KPIs)
  /\bdeferred revenue\b/,
  /\bcontract liabilit(?:y|ies)\b/,
  /\brevenue recognition\b/,
  /\basc\s*606\b/,
]

function normalizeCompanyKpiName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function isGenericCompanyKpi(name: string): boolean {
  if (!name) return true
  const normalized = normalizeCompanyKpiName(name)
  // Allow ratio/breakdown KPIs even if they mention generic base terms.
  if ([
    'conversion',
    'intensity',
    'mix',
    'breakdown',
    'share',
    'bridge',
    'per user',
    'per customer',
    'per account',
    'per subscriber',
    'per merchant',
    'per store',
    'per location',
    'per vehicle',
    'per rider',
    'per order',
  ].some(hint => normalized.includes(hint))) {
    return false
  }
  if (GENERIC_COMPANY_KPI_NAMES.has(normalized)) return true
  return GENERIC_COMPANY_KPI_PATTERNS.some((pattern) => pattern.test(normalized))
}

function getMarginTone(value: number): { bar: string; text: string; dot: string } {
  if (value >= 20) {
    return {
      bar: 'bg-gradient-to-r from-emerald-500 to-teal-400',
      text: 'text-emerald-600 dark:text-emerald-400',
      dot: 'bg-emerald-500',
    }
  }
  if (value >= 10) {
    return {
      bar: 'bg-gradient-to-r from-sky-500 to-blue-500',
      text: 'text-sky-600 dark:text-sky-400',
      dot: 'bg-sky-500',
    }
  }
  if (value >= 5) {
    return {
      bar: 'bg-gradient-to-r from-amber-400 to-orange-400',
      text: 'text-amber-600 dark:text-amber-400',
      dot: 'bg-amber-400',
    }
  }
  if (value >= 0) {
    return {
      bar: 'bg-gradient-to-r from-orange-400 to-amber-400',
      text: 'text-orange-600 dark:text-orange-400',
      dot: 'bg-orange-400',
    }
  }
  return {
    bar: 'bg-gradient-to-r from-rose-500 to-red-500',
    text: 'text-rose-600 dark:text-rose-400',
    dot: 'bg-rose-500',
  }
}

/**
 * Clean inline metric row - Notion style
 */
function MetricRow({ 
  label, 
  current, 
  prior,
  type = 'currency',
}: { 
  label: string
  current: number
  prior?: number | null
  type?: 'currency' | 'percent'
}) {
  const hasPrior = prior != null && prior !== 0
  const change = hasPrior ? calcChange(current, prior) : null
  
  const formatValue = type === 'percent' 
    ? (v: number) => `${v.toFixed(1)}%`
    : formatCurrency

  return (
    <div className="flex items-center justify-between py-3 border-b border-gray-100 dark:border-gray-800 last:border-0">
      <span className="text-sm text-gray-600 dark:text-gray-400">{label}</span>
      <div className="flex items-center gap-3">
        <span className={cn(
          "font-medium tabular-nums",
          current < 0 ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-gray-100"
        )}>
          {formatValue(current)}
        </span>
        {change && (
          <span className={cn(
            "text-sm tabular-nums",
            change.value >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500 dark:text-red-400"
          )}>
            {change.display}
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * Vertical Progress Bar for Margins - handles negative values properly
 */
function MarginProgressBar({ 
  value, 
  label,
  delay = 0 
}: { 
  value: number
  label: string
  delay?: number
}) {
  const [mounted, setMounted] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 50)
    return () => clearTimeout(timer)
  }, [])
  
  const isNegative = value < 0
  const absValue = Math.abs(value)
  // Scale: for positive, 0-50% fills the bar; for negative, show red
  const barHeight = Math.min(absValue, 50) * 2
  
  const getColor = () => {
    if (isNegative) return { bar: 'bg-gradient-to-t from-red-500 to-red-400', text: 'text-red-600 dark:text-red-400' }
    if (value >= 20) return { bar: 'bg-gradient-to-t from-emerald-500 to-emerald-400', text: 'text-emerald-600 dark:text-emerald-400' }
    if (value >= 10) return { bar: 'bg-gradient-to-t from-blue-500 to-blue-400', text: 'text-blue-600 dark:text-blue-400' }
    if (value >= 5) return { bar: 'bg-gradient-to-t from-amber-500 to-amber-400', text: 'text-amber-600 dark:text-amber-400' }
    return { bar: 'bg-gradient-to-t from-orange-500 to-orange-400', text: 'text-orange-600 dark:text-orange-400' }
  }
  
  const colors = getColor()
  
  return (
    <motion.div 
      className="flex flex-col items-center gap-2"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: mounted ? 1 : 0, y: mounted ? 0 : 20 }}
      transition={{ duration: 0.5, delay }}
    >
      {/* Value display */}
      <motion.span 
        className={cn("text-lg font-bold tabular-nums", colors.text)}
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: delay + 0.3 }}
      >
        {value.toFixed(1)}%
      </motion.span>
      
      {/* Vertical bar container */}
      <div className="relative w-12 h-20 bg-gray-100 dark:bg-gray-800 rounded-lg overflow-hidden">
        <motion.div
          className={cn("absolute bottom-0 left-0 right-0 rounded-lg", colors.bar)}
          initial={{ height: 0 }}
          animate={{ height: mounted ? `${barHeight}%` : 0 }}
          transition={{ duration: 0.8, delay: delay + 0.1, ease: [0.4, 0, 0.2, 1] }}
        />
        {/* Center line for reference */}
        <div className="absolute top-1/2 left-0 right-0 h-px bg-gray-300 dark:bg-gray-600" />
      </div>
      
      {/* Label */}
      <span className="text-xs font-medium text-gray-600 dark:text-gray-400 text-center">
        {label}
      </span>
    </motion.div>
  )
}

/**
 * Inline Margins Card with Meter Bars
 */
function InlineMarginsCard({ chartData }: { chartData: ChartData }) {
  const { current_period } = chartData

  const margins = [
    { label: 'Gross', value: current_period.gross_margin },
    { label: 'Operating', value: current_period.operating_margin },
    { label: 'Net', value: current_period.net_margin },
  ].filter(m => m.value != null)

  if (margins.length === 0) return null

  const normalizeMargin = (raw: number): number => (Math.abs(raw) <= 1.2 ? raw * 100 : raw)

  const normalizedMargins = margins.map(margin => ({
    ...margin,
    value: normalizeMargin(margin.value!),
  }))

  const maxValue = Math.max(...normalizedMargins.map(m => m.value ?? 0), 0)
  const maxScale = (() => {
    const minScale = 30
    const maxAllowed = 100
    const rounded = Math.ceil(Math.max(minScale, maxValue) / 10) * 10
    return Math.min(maxAllowed, rounded)
  })()
  const tickValues = [0, maxScale / 2, maxScale]

  return (
    <motion.div
      className="not-prose my-6 rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-hidden shadow-sm"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="px-5 py-4 border-b border-slate-100 dark:border-gray-800 bg-gradient-to-r from-slate-50 to-white dark:from-gray-800 dark:to-gray-900">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-4 bg-gradient-to-b from-emerald-500 to-sky-500 rounded-full" />
            <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Profitability Margins</h4>
          </div>
          <span className="text-xs text-gray-500 dark:text-gray-400">Percent of revenue</span>
        </div>
      </div>

      <div className="p-5 space-y-4">
        {normalizedMargins.map((margin, idx) => {
          const value = margin.value!
          const clamped = Math.max(0, Math.min(maxScale, value))
          const isNegative = value < 0
          const tone = getMarginTone(value)
          const widthPct = (clamped / maxScale) * 100
          const markerPct = isNegative ? 0 : widthPct

          return (
            <div
              key={margin.label}
              className="rounded-xl border border-slate-100 dark:border-gray-800 bg-slate-50/70 dark:bg-gray-900/40 p-4"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className={cn("h-2.5 w-2.5 rounded-full shadow-sm", tone.dot)} />
                  <span className="text-xs font-semibold text-slate-600 dark:text-gray-300">
                    {margin.label}
                  </span>
                </div>
                  <span className={cn("text-sm font-semibold tabular-nums", tone.text)}>
                    {value.toFixed(1)}%
                  </span>
                </div>

              <div className="relative h-3 rounded-full bg-white dark:bg-gray-900 shadow-inner overflow-hidden">
                {/* Ticks */}
                {tickValues.map((tick) => {
                  const tickPct = (tick / maxScale) * 100
                  const isCenter = tick === 0
                  return (
                    <div
                      key={tick}
                      className={cn(
                        "absolute top-0 bottom-0 w-px",
                        isCenter
                          ? "bg-slate-300 dark:bg-gray-600"
                          : "bg-slate-200/80 dark:bg-gray-700/70"
                      )}
                      style={{ left: `${tickPct}%` }}
                    />
                  )
                })}
                <motion.div
                  className={cn(
                    "absolute top-0 bottom-0 rounded-full",
                    isNegative ? "bg-gradient-to-r from-rose-500 to-red-500" : tone.bar
                  )}
                  style={{ left: 0 }}
                  initial={{ width: 0 }}
                  animate={{ width: `${widthPct}%` }}
                  transition={{ duration: 0.7, delay: 0.05 + idx * 0.12, ease: [0.4, 0, 0.2, 1] }}
                />
                <motion.div
                  className={cn(
                    "absolute top-1/2 -translate-y-1/2 h-3.5 w-3.5 rounded-full border-2 border-white dark:border-gray-900 shadow",
                    isNegative ? "bg-rose-500" : tone.dot
                  )}
                  style={{ left: `calc(${markerPct}% - 7px)` }}
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ duration: 0.4, delay: 0.2 + idx * 0.12 }}
                />
              </div>
              {isNegative && (
                <p className="mt-2 text-[11px] text-rose-500">Below breakeven</p>
              )}
              {value > maxScale && (
                <p className="mt-2 text-[11px] text-slate-500 dark:text-gray-400">
                  Clipped to {maxScale.toFixed(0)}% scale
                </p>
              )}
            </div>
          )
        })}
      </div>

      <div className="px-5 pb-4 flex items-center justify-between text-[11px] text-slate-500 dark:text-gray-400">
        <span>0%</span>
        <span>{(maxScale / 2).toFixed(0)}%</span>
        <span>{maxScale.toFixed(0)}%</span>
      </div>
    </motion.div>
  )
}

/**
 * Smart KPI Chart - picks the right chart type based on kpi.chart_type
 * Now uses the new premium ChartOrchestrator with 3D effects and animations
 */
function CompanyKPIChart({
  kpi,
  currentLabel,
  priorLabel
}: {
  kpi: CompanyKPI
  currentLabel: string
  priorLabel: string
}) {
  const effectiveCurrentLabel = (kpi.period_label && kpi.period_label.trim().length > 0)
    ? kpi.period_label
    : currentLabel
  const effectivePriorLabel = (kpi.prior_period_label && kpi.prior_period_label.trim().length > 0)
    ? kpi.prior_period_label
    : priorLabel

  // Convert CompanyKPI to KPIData format for new chart components
  const kpiData: KPIData = {
    name: kpi.name,
    value: kpi.value,
    prior_value: kpi.prior_value,
    unit: kpi.unit,
    description: kpi.description,
    company_specific: kpi.company_specific,
    chart_type: kpi.chart_type,
    period_label: effectiveCurrentLabel,
    prior_period_label: effectivePriorLabel,
    source_quote: kpi.source_quote,
    segments: kpi.segments,
    history: kpi.history,
  }

  // Use the new premium ChartOrchestrator
  return (
    <ChartOrchestrator
      kpi={kpiData}
      currentLabel={effectiveCurrentLabel}
      priorLabel={effectivePriorLabel}
      use3D={true}
    />
  )
}

function normalizeCompanyKpi(input: unknown): CompanyKPI | null {
  if (!input || typeof input !== 'object') return null
  const anyInput = input as any
  if (typeof anyInput.name !== 'string' || anyInput.name.trim().length === 0) return null

  const companySpecific =
    typeof anyInput.company_specific === 'boolean'
      ? anyInput.company_specific
      : typeof anyInput.companySpecific === 'boolean'
        ? anyInput.companySpecific
        : undefined

  // Company Spotlight should ONLY show evidence-backed company-specific KPIs.
  if (companySpecific === false) return null
  if (isGenericCompanyKpi(anyInput.name)) return null

  const toNumber = (value: unknown): number | null => {
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value !== 'string') return null
    let text = value.trim()
    if (!text) return null

    let negative = false
    if (text.startsWith('(') && text.endsWith(')')) {
      negative = true
      text = text.slice(1, -1).trim()
    }

    text = text.replace(/[,]/g, '')
    text = text.replace(/[$€£]/g, '')
    text = text.replace(/\s+/g, '')
    text = text.replace(/%/g, '')

    const match = text.match(
      /^([+-]?\d+(?:\.\d+)?)(K|M|B|T|THOUSAND|MILLION|BILLION|TRILLION|BN|MM)?$/i,
    )
    if (!match) return null

    const parsed = Number(match[1])
    if (!Number.isFinite(parsed)) return null

    const suffix = (match[2] ?? '').toUpperCase()
    const multiplier =
      suffix === 'K' || suffix === 'THOUSAND'
        ? 1_000
        : suffix === 'M' || suffix === 'MILLION' || suffix === 'MM'
          ? 1_000_000
          : suffix === 'B' || suffix === 'BILLION' || suffix === 'BN'
            ? 1_000_000_000
            : suffix === 'T' || suffix === 'TRILLION'
              ? 1_000_000_000_000
              : 1

    const out = parsed * multiplier
    return negative ? -out : out
  }

  // Prefer `value`, but accept explicit `current_value` variants from the backend.
  const rawValue = toNumber(anyInput.value ?? anyInput.current_value ?? anyInput.currentValue)
  if (rawValue == null) return null

  const unit = typeof anyInput.unit === 'string' ? normalizeKpiUnit(anyInput.unit) : undefined
  const value = normalizePercentageValue(rawValue, unit)
  const priorValueRaw = toNumber(anyInput.prior_value ?? anyInput.priorValue ?? anyInput.previous_value ?? anyInput.previousValue)
  const priorValue = priorValueRaw == null ? null : normalizePercentageValue(priorValueRaw, unit)
  const description = typeof anyInput.description === 'string' ? anyInput.description : undefined
  const sourceQuote =
    typeof anyInput.source_quote === 'string'
      ? anyInput.source_quote
      : typeof anyInput.sourceQuote === 'string'
        ? anyInput.sourceQuote
        : undefined

  const periodLabel =
    typeof anyInput.period_label === 'string'
      ? anyInput.period_label
      : typeof anyInput.periodLabel === 'string'
        ? anyInput.periodLabel
        : undefined

  const priorPeriodLabel =
    typeof anyInput.prior_period_label === 'string'
      ? anyInput.prior_period_label
      : typeof anyInput.priorPeriodLabel === 'string'
        ? anyInput.priorPeriodLabel
        : undefined

  const sourceFilingId =
    typeof anyInput.source_filing_id === 'string'
      ? anyInput.source_filing_id
      : typeof anyInput.sourceFilingId === 'string'
        ? anyInput.sourceFilingId
        : undefined

  const rawChartType =
    typeof anyInput.chart_type === 'string'
      ? anyInput.chart_type
      : typeof anyInput.chartType === 'string'
        ? anyInput.chartType
        : typeof anyInput.chart === 'string'
          ? anyInput.chart
          : typeof anyInput.type === 'string'
            ? anyInput.type
            : undefined
  const chartType = typeof rawChartType === 'string' ? rawChartType.trim().toLowerCase() : undefined

  const rawSegments = Array.isArray(anyInput.segments)
    ? anyInput.segments
    : Array.isArray(anyInput.breakdown)
      ? anyInput.breakdown
      : Array.isArray(anyInput.parts)
        ? anyInput.parts
        : undefined

  const segments: CompanyKPI['segments'] | undefined = rawSegments
    ? rawSegments
        .map((seg: any) => {
          if (!seg || typeof seg !== 'object') return null
          if (typeof seg.label !== 'string' || seg.label.trim().length === 0) return null
          const segValueRaw = toNumber(seg.value)
          if (segValueRaw == null || segValueRaw <= 0) return null
          const segValue = normalizePercentageValue(segValueRaw, unit)
          const color = typeof seg.color === 'string' ? seg.color : undefined
          return { label: seg.label.trim(), value: segValue, color }
        })
        .filter(Boolean)
    : undefined

  const rawHistory = Array.isArray(anyInput.history)
    ? anyInput.history
    : Array.isArray(anyInput.series)
      ? anyInput.series
      : Array.isArray(anyInput.trend)
        ? anyInput.trend
        : undefined

  const history: CompanyKPI['history'] | undefined = rawHistory
    ? rawHistory
        .map((entry: any) => {
          if (!entry || typeof entry !== 'object') return null
          const periodLabel =
            typeof entry.period_label === 'string'
              ? entry.period_label
              : typeof entry.periodLabel === 'string'
                ? entry.periodLabel
                : typeof entry.label === 'string'
                  ? entry.label
                  : undefined
          if (!periodLabel || periodLabel.trim().length === 0) return null
          const valueRaw = toNumber(entry.value)
          if (valueRaw == null) return null
          return { period_label: periodLabel.trim(), value: normalizePercentageValue(valueRaw, unit) }
        })
        .filter(Boolean)
    : undefined

  return {
    name: anyInput.name.trim(),
    value,
    prior_value: priorValueRaw == null ? null : priorValue,
    unit,
    description,
    company_specific: companySpecific,
    chart_type: chartType,
    period_label: periodLabel,
    prior_period_label: priorPeriodLabel,
    source_filing_id: sourceFilingId,
    history: history && history.length > 0 ? history : undefined,
    segments: segments && segments.length > 0 ? segments : undefined,
    source_quote: sourceQuote,
  }
}

function buildCompanyCharts(chartData: ChartData): CompanyKPI[] {
  const rawCharts =
    chartData.company_charts && Array.isArray(chartData.company_charts) && chartData.company_charts.length > 0
      ? chartData.company_charts
      : chartData.company_kpi
        ? [chartData.company_kpi]
        : []

  return rawCharts
    .map(normalizeCompanyKpi)
    .filter((kpi): kpi is CompanyKPI => kpi != null)
    .filter((kpi) => kpi.company_specific === true)
    .filter((kpi, idx, arr) => arr.findIndex(other => other.name === kpi.name) === idx)
    .slice(0, 1)
}

function CompanyInsightsSection({ chartData, filingId }: { chartData: ChartData; filingId?: string | null }) {
  const baseCharts = useMemo(() => buildCompanyCharts(chartData), [chartData])
  const [remoteCharts, setRemoteCharts] = useState<CompanyKPI[] | null>(null)
  const [remoteLoading, setRemoteLoading] = useState(false)
  const [remoteReason, setRemoteReason] = useState<string | null>(null)
  const [remoteSlow, setRemoteSlow] = useState(false)
  const [remoteVerySlow, setRemoteVerySlow] = useState(false)
  const [spotlightRetryKey, setSpotlightRetryKey] = useState(0)
  const spotlightRequestSeq = useRef(0)
  const baseHasCompanySpecific = baseCharts.some((kpi) => kpi.company_specific === true)
  const shouldFetchRemoteSpotlight = Boolean(filingId && !baseHasCompanySpecific)
  const charts = shouldFetchRemoteSpotlight ? (remoteCharts ?? []) : baseCharts
  const awaitingRemote = Boolean(shouldFetchRemoteSpotlight && remoteCharts == null)
  const displayCharts = awaitingRemote
    ? []
    : charts.length > 0
      ? charts
      : []
  const spotlightTier: 'loading' | 'spotlight' | 'none' =
    awaitingRemote ? 'loading' : charts.length > 0 ? 'spotlight' : 'none'
  const remoteReasonLower = (remoteReason || '').toLowerCase()
  const spotlightNoKpiMessage = (() => {
    if (!remoteReasonLower) {
      return "This filing didn't contain an extractable company-specific operating metric with a clear numeric value."
    }

    if (remoteReasonLower.includes('timeout')) {
      return 'KPI extraction timed out while reading this filing. Try again, or try a smaller/alternate filing.'
    }

    const infrastructureFailureReasons = [
      'pass1_failed',
      'pass2_failed',
      'timeout_before_pass1',
      'timeout_before_pass2',
      'spotlight_evidence_exception',
      'spotlight_evidence_timeout',
      'upload_failed',
      'no_file_uri',
      'no_local_document',
    ]
    if (infrastructureFailureReasons.some(r => remoteReasonLower.includes(r))) {
      return 'KPI extraction hit a temporary issue while reading this filing. Try again (or try an alternate filing).'
    }

    const limitationReasons = [
      'file_pipeline_file_too_large',
      'file_pipeline_upload_disabled',
      'file_pipeline_empty_file',
      'file_pipeline_read_failed',
      'no_text_layer',
    ]
    if (limitationReasons.some(r => remoteReasonLower.includes(r))) {
      return 'KPI extraction could not analyze this filing content reliably (file too large / unreadable text). Try a different filing.'
    }

    return "This filing didn't contain an extractable company-specific operating metric with a clear numeric value."
  })()

  useEffect(() => {
    setSpotlightRetryKey(0)
  }, [filingId])

  useEffect(() => {
    if (!shouldFetchRemoteSpotlight) {
      setRemoteCharts(null)
      setRemoteLoading(false)
      setRemoteReason(null)
      setRemoteSlow(false)
      setRemoteVerySlow(false)
      return
    }
    if (!filingId) return

    const seq = ++spotlightRequestSeq.current
    setRemoteCharts(null)
    setRemoteLoading(true)
    setRemoteReason(null)
    setRemoteSlow(false)
    setRemoteVerySlow(false)

    // Soft timeout: show "still working" but keep waiting for the response.
    const softTimeoutId = window.setTimeout(() => {
      if (spotlightRequestSeq.current !== seq) return
      setRemoteSlow(true)
    }, 45_000)

    // Longer filings can take a while (EDGAR download, OCR, multi-pass extraction).
    // Keep waiting, but update the UI so users know it's not stuck.
    const verySlowTimeoutId = window.setTimeout(() => {
      if (spotlightRequestSeq.current !== seq) return
      setRemoteVerySlow(true)
    }, 120_000)

    filingsApi
      .getSpotlightKpi(filingId, { refresh: spotlightRetryKey > 0 })
      .then((res) => {
        if (spotlightRequestSeq.current !== seq) return
        const reason = typeof res?.data?.reason === 'string' ? res.data.reason : null
        setRemoteReason(reason)
        const rawCharts: unknown[] = Array.isArray(res?.data?.company_charts)
          ? res.data.company_charts
          : res?.data?.company_kpi
            ? [res.data.company_kpi]
            : []

        const normalized = rawCharts
          .map(normalizeCompanyKpi)
          .filter((kpi): kpi is CompanyKPI => kpi != null)
          .filter((kpi) => (shouldFetchRemoteSpotlight ? kpi.company_specific === true : true))
          .filter((kpi, idx, arr) => arr.findIndex(other => other.name === kpi.name) === idx)

        setRemoteCharts(normalized)
      })
      .catch(() => {
        if (spotlightRequestSeq.current !== seq) return
        setRemoteCharts([])
        setRemoteReason('spotlight_error')
      })
      .finally(() => {
        window.clearTimeout(softTimeoutId)
        window.clearTimeout(verySlowTimeoutId)
        if (spotlightRequestSeq.current !== seq) return
        setRemoteLoading(false)
      })

    return () => {
      window.clearTimeout(softTimeoutId)
      window.clearTimeout(verySlowTimeoutId)
      spotlightRequestSeq.current += 1
    }
  }, [filingId, shouldFetchRemoteSpotlight, spotlightRetryKey])

  const highlightPrimary = charts.length > 2
  const gridLayout =
    displayCharts.length === 1 ? 'grid-cols-1' : displayCharts.length === 2 ? 'md:grid-cols-2' : 'md:grid-cols-2 lg:grid-cols-3'

  return (
    <>
      {awaitingRemote ? (
        <motion.div
          className="not-prose mb-6 rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-gradient-to-br from-white via-white to-slate-50 dark:from-gray-900 dark:to-gray-800 p-5 shadow-sm"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-4 bg-gradient-to-b from-sky-500 to-emerald-500 rounded-full" />
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Company Spotlight</h3>
            </div>
            <span className="inline-flex items-center gap-2 rounded-full border border-slate-200/80 dark:border-gray-700 bg-white/80 dark:bg-gray-900/80 px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500 dark:text-gray-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
              Finding KPI…
            </span>
          </div>

          <p className="mb-4 text-xs text-slate-500 dark:text-gray-400 leading-relaxed">
            {remoteVerySlow
              ? 'Still working… older filings can take a couple minutes to analyze (downloads + OCR + verification).'
              : remoteSlow
                ? "Still working… this filing is taking longer than usual to analyze."
                : 'Scanning the filing text for a company-specific operating KPI…'}
          </p>

          {remoteVerySlow ? (
            <div className="mb-4">
              <button
                type="button"
                onClick={() => setSpotlightRetryKey((v) => v + 1)}
                className="inline-flex items-center gap-2 rounded-full border border-slate-200/80 dark:border-gray-700 bg-white/80 dark:bg-gray-900/80 px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500 dark:text-gray-400 hover:text-slate-700 dark:hover:text-gray-200"
              >
                Retry
              </button>
            </div>
          ) : null}

          <div className={cn("grid gap-4", displayCharts.length === 2 ? 'md:grid-cols-2' : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3')}>
            {Array.from({ length: 3 }).map((_, idx) => (
              <div
                key={`spotlight-skeleton-${idx}`}
                className={cn(
                  "rounded-2xl border border-slate-200/60 dark:border-gray-800 bg-white/70 dark:bg-gray-900/40 p-5 animate-pulse",
                  idx === 0 && "lg:col-span-2"
                )}
              >
                <div className="h-3 w-28 bg-slate-200/80 dark:bg-gray-800 rounded" />
                <div className="mt-3 h-8 w-2/3 bg-slate-200/80 dark:bg-gray-800 rounded" />
                <div className="mt-6 h-2 w-full bg-slate-200/60 dark:bg-gray-800 rounded" />
                <div className="mt-2 h-2 w-5/6 bg-slate-200/60 dark:bg-gray-800 rounded" />
              </div>
            ))}
          </div>
        </motion.div>
      ) : displayCharts.length > 0 ? (
        <motion.div
          className="not-prose mb-6 rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-gradient-to-br from-white via-white to-slate-50 dark:from-gray-900 dark:to-gray-800 p-5 shadow-sm"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
	          <div className="flex items-center justify-between mb-4">
	            <div className="flex items-center gap-2">
	              <div className="w-1.5 h-4 bg-gradient-to-b from-sky-500 to-emerald-500 rounded-full" />
	              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Company Spotlight</h3>
	            </div>
		            <span className="inline-flex items-center gap-2 rounded-full border border-slate-200/80 dark:border-gray-700 bg-white/80 dark:bg-gray-900/80 px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500 dark:text-gray-400">
		              <span
	                  className={cn(
	                    "h-1.5 w-1.5 rounded-full",
	                    spotlightTier === "loading"
	                      ? "bg-amber-400"
	                      : spotlightTier === "spotlight"
	                        ? "bg-emerald-500"
	                        : "bg-slate-400"
                  )}
	                />
		              {spotlightTier === "loading"
		                  ? 'Finding KPI…'
	                : displayCharts.length === 1
	                  ? 'Spotlight KPI'
	                  : `KPI Candidates • ${displayCharts.length}`}
		            </span>
		          </div>

          <div className={cn("grid gap-4", gridLayout)}>
            {displayCharts.map((kpi, idx) => (
              <div
                key={`${kpi.name}-${idx}`}
                className={cn(highlightPrimary && idx === 0 && "lg:col-span-2")}
              >
                <CompanyKPIChart
                  kpi={kpi}
                  currentLabel={chartData.current_label}
                  priorLabel={chartData.prior_label}
                />
              </div>
            ))}
          </div>
        </motion.div>
      ) : (
        <motion.div
          className="not-prose mb-6 rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-gradient-to-br from-white via-white to-slate-50 dark:from-gray-900 dark:to-gray-800 p-5 shadow-sm"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-4 bg-gradient-to-b from-slate-400 to-slate-500 rounded-full" />
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Company Spotlight</h3>
            </div>
            <span className="inline-flex items-center gap-2 rounded-full border border-slate-200/80 dark:border-gray-700 bg-white/80 dark:bg-gray-900/80 px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500 dark:text-gray-400">
              <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
              No Unique KPI
            </span>
          </div>

	            <div className="rounded-2xl border border-slate-200/60 dark:border-gray-800 bg-white/70 dark:bg-gray-900/40 p-5">
	            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">No company-specific KPI found</p>
	            <p className="mt-2 text-xs text-slate-600 dark:text-gray-300 leading-relaxed">
	              {spotlightNoKpiMessage}
	            </p>
	            {remoteLoading && (
	              <p className="mt-2 text-xs text-slate-500 dark:text-gray-400">
	                Checking recent filings for a disclosed company-specific KPI…
	              </p>
            )}
            {remoteReason && !remoteLoading && (
              <p className="mt-2 text-[11px] text-slate-400 dark:text-gray-500">
                Backend reason: {remoteReason}
              </p>
            )}
            <p className="mt-2 text-xs text-slate-500 dark:text-gray-400">
              Try a different quarter/annual filing. This widget only shows numeric company-specific KPIs that appear in the filing text.
            </p>
          </div>
        </motion.div>
          )}

    </>
  )
}

/**
 * Custom Tooltip for Bar Chart
 */
function CustomBarTooltip({ active, payload, label }: any) {
  if (!active || !payload || !payload.length) return null
  
  return (
    <div className="bg-white dark:bg-gray-900 px-3 py-2 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700">
      <p className="text-xs font-medium text-gray-900 dark:text-gray-100 mb-1">{label}</p>
      {payload.map((entry: any, index: number) => (
        <p key={index} className="text-xs" style={{ color: entry.fill === 'url(#barGradient)' ? '#3B82F6' : entry.fill }}>
          {entry.name}: {formatCurrency(entry.value)}
        </p>
      ))}
    </div>
  )
}

/**
 * Grouped Vertical Bar Chart for Period Comparison
 */
function GroupedComparisonChart({ 
  data,
  currentLabel,
  priorLabel
}: { 
  data: { label: string; current: number; prior: number }[]
  currentLabel: string
  priorLabel: string
}) {
  const [mounted, setMounted] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 100)
    return () => clearTimeout(timer)
  }, [])

  // Transform data for Recharts
  const chartData = data.map(item => ({
    name: item.label.replace('Operating Cash Flow', 'OCF').replace('Capital Expenditure', 'CapEx').replace('Gross Profit', 'Gross'),
    [currentLabel]: Math.abs(item.current),
    [priorLabel]: Math.abs(item.prior),
    currentRaw: item.current,
    priorRaw: item.prior,
    change: item.prior !== 0 ? ((item.current - item.prior) / Math.abs(item.prior)) * 100 : 0,
  }))

  return (
    <motion.div 
      className="not-prose rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-hidden shadow-sm"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: mounted ? 1 : 0, y: mounted ? 0 : 10 }}
      transition={{ duration: 0.4 }}
    >
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-100 dark:border-gray-800 bg-gradient-to-r from-slate-50 to-white dark:from-gray-800 dark:to-gray-900">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-4 bg-gradient-to-b from-sky-500 to-emerald-500 rounded-full" />
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Period Comparison
            </h3>
          </div>
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-gradient-to-br from-blue-500 to-indigo-500" />
              <span className="text-gray-600 dark:text-gray-400">{currentLabel}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-gray-300 dark:bg-gray-600" />
              <span className="text-gray-600 dark:text-gray-400">{priorLabel}</span>
            </div>
          </div>
        </div>
      </div>
      
      {/* Chart */}
      <div className="p-4">
        <div className="h-[200px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart 
              data={chartData} 
              margin={{ top: 20, right: 10, left: -10, bottom: 5 }}
              barCategoryGap="20%"
            >
              <XAxis 
                dataKey="name" 
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#6B7280', fontSize: 11, fontWeight: 500 }}
              />
              <YAxis 
                axisLine={false}
                tickLine={false}
                tick={{ fill: '#9CA3AF', fontSize: 10 }}
                tickFormatter={(value) => formatCompact(value)}
                width={45}
              />
              <Tooltip content={<CustomBarTooltip />} cursor={{ fill: 'rgba(0,0,0,0.04)' }} />
              <Bar 
                dataKey={currentLabel} 
                fill="url(#barGradient)" 
                radius={[4, 4, 0, 0]}
                animationDuration={800}
                animationBegin={0}
              />
              <Bar 
                dataKey={priorLabel} 
                fill="#D1D5DB"
                radius={[4, 4, 0, 0]}
                animationDuration={800}
                animationBegin={200}
              />
              <defs>
                <linearGradient id="barGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3B82F6" />
                  <stop offset="100%" stopColor="#6366F1" />
                </linearGradient>
              </defs>
            </BarChart>
          </ResponsiveContainer>
        </div>
        
        {/* Change indicators below chart */}
        <div className="mt-4 pt-4 border-t border-gray-100 dark:border-gray-800">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {chartData.map((item, idx) => {
              const isPositive = item.change >= 0
              return (
                <motion.div 
                  key={item.name}
                  className="text-center"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.3 + idx * 0.1 }}
                >
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{item.name}</p>
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 tabular-nums">
                    {formatCurrency(item.currentRaw)}
                  </p>
                  <span className={cn(
                    "inline-block mt-1 text-xs font-medium px-2 py-0.5 rounded-full",
                    isPositive 
                      ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
                      : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400"
                  )}>
                    {isPositive ? '+' : ''}{item.change.toFixed(1)}%
                  </span>
                </motion.div>
              )
            })}
          </div>
        </div>
      </div>
    </motion.div>
  )
}

/**
 * Radial Gauge for Company KPI
 */
function RadialKPIGauge({ 
  kpi, 
  currentLabel, 
  priorLabel 
}: { 
  kpi: CompanyKPI
  currentLabel: string
  priorLabel: string
}) {
  const [animatedValue, setAnimatedValue] = useState(0)
  const [mounted, setMounted] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 100)
    return () => clearTimeout(timer)
  }, [])
  
  useEffect(() => {
    if (!mounted) return
    const duration = 1500
    const steps = 60
    const increment = kpi.value / steps
    let current = 0
    const timer = setInterval(() => {
      current += increment
      if (current >= kpi.value) {
        setAnimatedValue(kpi.value)
        clearInterval(timer)
      } else {
        setAnimatedValue(current)
      }
    }, duration / steps)
    return () => clearInterval(timer)
  }, [kpi.value, mounted])

  const hasPrior = kpi.prior_value != null && kpi.prior_value !== 0
  const change = hasPrior ? ((kpi.value - kpi.prior_value!) / Math.abs(kpi.prior_value!)) * 100 : null
  const isPositive = change != null && change >= 0
  
  // Calculate gauge progress (0 to 1) based on prior comparison or arbitrary max
  const maxValue = hasPrior ? Math.max(kpi.value, kpi.prior_value!) * 1.2 : kpi.value * 1.5
  const progress = Math.min(kpi.value / maxValue, 1)
  
  // SVG arc calculations
  const size = 140
  const strokeWidth = 12
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const arcLength = circumference * 0.75 // 270 degrees
  const strokeDashoffset = arcLength * (1 - (animatedValue / maxValue))

  const formatValue = (v: number) => {
    if (kpi.unit === '%') return `${v.toFixed(1)}%`
    if (kpi.unit === 'M') return `${v.toFixed(1)}M`
    if (kpi.unit === 'B') return `${v.toFixed(2)}B`
    if (kpi.unit === '$' || kpi.unit === '€' || kpi.unit === '£') return formatCurrency(v, kpi.unit)
    if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}B`
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
    if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
    return v.toFixed(0)
  }

  return (
    <motion.div 
      className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-sky-500 via-teal-500 to-emerald-500 p-[2px]"
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: mounted ? 1 : 0, scale: mounted ? 1 : 0.95 }}
      transition={{ duration: 0.5 }}
    >
      <div className="rounded-2xl bg-white dark:bg-gray-900 p-5">
        {/* Header */}
        <div className="flex items-start justify-between mb-2">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-gradient-to-r from-sky-500 to-emerald-500 animate-pulse" />
              <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                {kpi.company_specific === false ? 'Financial Metric' : 'Key Metric'}
              </span>
            </div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              {kpi.name}
            </h3>
          </div>
          {change != null && (
            <motion.div
              initial={{ opacity: 0, x: 10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.8 }}
              className={cn(
                "flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold",
                isPositive 
                  ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
                  : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400"
              )}
            >
              <svg 
                className={cn("w-3 h-3", !isPositive && "rotate-180")} 
                fill="none" 
                viewBox="0 0 24 24" 
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 10l7-7m0 0l7 7m-7-7v18" />
              </svg>
              {Math.abs(change).toFixed(1)}%
            </motion.div>
          )}
        </div>

        {/* Gauge */}
        <div className="flex items-center gap-4">
          <div className="relative">
            <svg 
              width={size} 
              height={size} 
              className="transform -rotate-[135deg]"
            >
              {/* Background arc */}
              <circle
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke="currentColor"
                strokeWidth={strokeWidth}
                strokeDasharray={arcLength}
                strokeDashoffset={0}
                strokeLinecap="round"
                className="text-gray-100 dark:text-gray-800"
              />
              {/* Animated foreground arc */}
              <motion.circle
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke="url(#kpiGradient)"
                strokeWidth={strokeWidth}
                strokeDasharray={arcLength}
                initial={{ strokeDashoffset: arcLength }}
                animate={{ strokeDashoffset }}
                transition={{ duration: 1.5, ease: [0.4, 0, 0.2, 1] }}
                strokeLinecap="round"
              />
              <defs>
                <linearGradient id="kpiGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#0EA5E9" />
                  <stop offset="55%" stopColor="#14B8A6" />
                  <stop offset="100%" stopColor="#10B981" />
                </linearGradient>
              </defs>
            </svg>
            {/* Center value */}
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <motion.span 
                className="text-2xl font-bold bg-gradient-to-r from-sky-600 to-emerald-500 dark:from-sky-400 dark:to-emerald-400 bg-clip-text text-transparent tabular-nums"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.3 }}
              >
                {formatValue(animatedValue)}
              </motion.span>
              <span className="text-[10px] text-gray-400 mt-0.5">{currentLabel}</span>
            </div>
          </div>
          
          {/* Side info */}
          <div className="flex-1 space-y-3">
            {hasPrior && (
              <div className="text-sm">
                <p className="text-xs text-gray-400 mb-0.5">Previous</p>
                <p className="font-medium text-gray-600 dark:text-gray-300 tabular-nums">
                  {formatValue(kpi.prior_value!)}
                </p>
                <p className="text-[10px] text-gray-400">{priorLabel}</p>
              </div>
            )}
            {kpi.description && (
              <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-2">
                {kpi.description}
              </p>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  )
}

/**
 * Clean Financial Summary Card - Notion style (Top card - financials only, no margins)
 */
function FinancialSummaryCard({ chartData }: { chartData: ChartData }) {
  const { current_period, prior_period, current_label, prior_label } = chartData
  
  const hasFinancials = current_period.revenue != null || 
    current_period.operating_income != null || 
    current_period.net_income != null ||
    current_period.free_cash_flow != null

  if (!hasFinancials) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="not-prose mb-8 rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-gradient-to-br from-slate-50 to-white dark:from-gray-900 dark:to-gray-800 overflow-hidden shadow-sm"
    >
      {/* Header */}
      <div className="px-5 py-3 border-b border-slate-100 dark:border-gray-800 bg-white/50 dark:bg-gray-900/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-4 bg-gradient-to-b from-sky-500 to-emerald-500 rounded-full" />
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Key Financials
            </h3>
          </div>
          {prior_period && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {current_label} vs {prior_label}
            </span>
          )}
        </div>
      </div>

      <div className="p-5">
        {current_period.revenue != null && (
          <MetricRow label="Revenue" current={current_period.revenue} prior={prior_period?.revenue} />
        )}
        {current_period.operating_income != null && (
          <MetricRow label="Operating Income" current={current_period.operating_income} prior={prior_period?.operating_income} />
        )}
        {current_period.net_income != null && (
          <MetricRow label="Net Income" current={current_period.net_income} prior={prior_period?.net_income} />
        )}
        {current_period.free_cash_flow != null && (
          <MetricRow label="Free Cash Flow" current={current_period.free_cash_flow} prior={prior_period?.free_cash_flow} />
        )}
      </div>
    </motion.div>
  )
}

/**
 * Enhanced Metric Card with visual improvements
 */
function MetricCard({ 
  label, 
  current, 
  prior,
  delay = 0,
  formatFn = formatCurrency
}: { 
  label: string
  current: number
  prior?: number | null
  delay?: number
  formatFn?: (v: number) => string
}) {
  const hasPrior = prior != null && prior !== 0
  const change = hasPrior ? ((current - prior) / Math.abs(prior)) * 100 : null
  const isPositive = change != null && change >= 0
  const [mounted, setMounted] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 50)
    return () => clearTimeout(timer)
  }, [])
  
  return (
    <motion.div 
      className="p-4 rounded-xl bg-gradient-to-br from-white to-gray-50 dark:from-gray-900 dark:to-gray-800 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md transition-shadow"
      initial={{ opacity: 0, y: 15, scale: 0.95 }}
      animate={{ opacity: mounted ? 1 : 0, y: mounted ? 0 : 15, scale: mounted ? 1 : 0.95 }}
      transition={{ duration: 0.4, delay }}
    >
      <div className="flex items-start justify-between mb-3">
        <span className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</span>
        {change != null && (
          <motion.span 
            className={cn(
              "text-xs font-semibold px-2 py-0.5 rounded-full",
              isPositive 
                ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
                : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400"
            )}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: delay + 0.3 }}
          >
            {isPositive ? '+' : ''}{change.toFixed(1)}%
          </motion.span>
        )}
      </div>
      <motion.div 
        className="text-xl font-bold text-gray-900 dark:text-gray-100 tabular-nums mb-2"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: delay + 0.2 }}
      >
        {formatFn(current)}
      </motion.div>
      {hasPrior && (
        <div className="text-xs text-gray-400 tabular-nums">
          vs {formatFn(prior)}
        </div>
      )}
    </motion.div>
  )
}

/**
 * Beautiful Financial Performance Card - Uses new chart components
 */
function FinancialPerformanceCard({ chartData }: { chartData: ChartData }) {
  const { current_period, prior_period, current_label, prior_label } = chartData
  const [mounted, setMounted] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 50)
    return () => clearTimeout(timer)
  }, [])
  
  if (!prior_period) return null

  // Collect comparison metrics for the grouped bar chart
  const comparisonMetrics: { label: string; current: number; prior: number }[] = []
  
  if (current_period.operating_cash_flow != null && prior_period.operating_cash_flow != null) {
    comparisonMetrics.push({ label: 'Operating Cash Flow', current: current_period.operating_cash_flow, prior: prior_period.operating_cash_flow })
  }
  if (current_period.gross_profit != null && prior_period.gross_profit != null) {
    comparisonMetrics.push({ label: 'Gross Profit', current: current_period.gross_profit, prior: prior_period.gross_profit })
  }
  if (current_period.ebitda != null && prior_period.ebitda != null) {
    comparisonMetrics.push({ label: 'EBITDA', current: current_period.ebitda, prior: prior_period.ebitda })
  }
  if (current_period.capex != null && prior_period.capex != null) {
    comparisonMetrics.push({ label: 'Capital Expenditure', current: Math.abs(current_period.capex), prior: Math.abs(prior_period.capex) })
  }

  // Key financial health metrics for cards
  const healthMetrics: { label: string; current: number; prior?: number | null; formatFn?: (v: number) => string }[] = []
  
  if (current_period.cash_and_equivalents != null) {
    healthMetrics.push({ 
      label: 'Cash Position', 
      current: current_period.cash_and_equivalents, 
      prior: prior_period.cash_and_equivalents 
    })
  }
  if (current_period.total_debt != null) {
    healthMetrics.push({ 
      label: 'Total Debt', 
      current: current_period.total_debt, 
      prior: prior_period.total_debt 
    })
  }
  if (current_period.eps != null) {
    healthMetrics.push({ 
      label: 'Earnings Per Share', 
      current: current_period.eps, 
      prior: prior_period.eps,
      formatFn: (v: number) => `$${v.toFixed(2)}`
    })
  }
  if (current_period.rnd_expense != null) {
    healthMetrics.push({ 
      label: 'R&D Investment', 
      current: current_period.rnd_expense, 
      prior: prior_period.rnd_expense 
    })
  }

  const hasContent = comparisonMetrics.length > 0 || healthMetrics.length > 0
  if (!hasContent) return null

  return (
    <motion.div 
      className="my-6 space-y-5"
      initial={{ opacity: 0 }}
      animate={{ opacity: mounted ? 1 : 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Grouped Bar Chart for Period Comparison */}
      {comparisonMetrics.length > 0 && (
        <GroupedComparisonChart
          data={comparisonMetrics}
          currentLabel={current_label}
          priorLabel={prior_label}
        />
      )}

      {/* Key Metrics Grid */}
      {healthMetrics.length > 0 && (
        <div className="grid grid-cols-2 gap-3">
          {healthMetrics.slice(0, 4).map((metric, index) => (
            <MetricCard
              key={metric.label}
              label={metric.label}
              current={metric.current}
              prior={metric.prior}
              delay={index * 0.1}
              formatFn={metric.formatFn}
            />
          ))}
        </div>
      )}
    </motion.div>
  )
}

/**
 * Combined Financial Visuals - clean and beautiful
 */
function InlineFinancialVisuals({ chartData }: { chartData: ChartData }) {
  const hasPriorData = chartData.prior_period && (
    chartData.prior_period.operating_cash_flow != null ||
    chartData.prior_period.gross_profit != null ||
    chartData.prior_period.ebitda != null ||
    chartData.prior_period.cash_and_equivalents != null ||
    chartData.prior_period.eps != null
  )
  
  const hasMargins = chartData.current_period.gross_margin != null || 
    chartData.current_period.operating_margin != null || 
    chartData.current_period.net_margin != null

  if (!hasPriorData && !hasMargins) return null

  return (
    <>
      {hasPriorData && <FinancialPerformanceCard chartData={chartData} />}
      {hasMargins && <InlineMarginsCard chartData={chartData} />}
    </>
  )
}

/**
 * Parse a metric line like "Revenue: $13.47B" into label and value
 */
function parseMetricLine(line: string): { label: string; value: string } | null {
  const match = line.match(/^(.+?):\s*(.+)$/)
  if (match) {
    return { label: match[1].trim(), value: match[2].trim() }
  }
  return null
}

function parseMetricsFromLine(line: string, currentLabel?: string | null): { label: string; value: string }[] {
  const cleaned = line.replace(/[→]/g, '|')
  const segments = cleaned.split('|').map(seg => seg.trim()).filter(Boolean)
  const metrics: { label: string; value: string }[] = []
  let usedCurrentLabel = false

  for (const segment of segments) {
    const parsed = parseMetricLine(segment.replace(/\.$/, ''))
    if (parsed) {
      metrics.push(parsed)
      continue
    }
    if (currentLabel && !usedCurrentLabel) {
      const value = segment.replace(/\.$/, '').trim()
      if (value) {
        metrics.push({ label: currentLabel, value })
        usedCurrentLabel = true
      }
    }
  }
  return metrics
}

function getValueSentiment(value: string): 'positive' | 'negative' | 'neutral' {
  if (value.startsWith('-') || value.startsWith('(') || value.toLowerCase().includes('loss')) {
    return 'negative'
  }
  return 'neutral'
}

/**
 * Clean Metrics Grid - Notion style
 */
function MetricsGrid({ metrics }: { metrics: { label: string; value: string; icon?: string }[] }) {
  return (
    <div className="my-8 rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/50 overflow-hidden">
      <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">
          Key Metrics
        </h3>
      </div>
      <div className="p-5">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-6">
          {metrics.map((metric, index) => {
            const sentiment = getValueSentiment(metric.value)
            return (
              <div key={index}>
                <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{metric.label}</p>
                <p className={cn(
                  "text-lg font-medium tabular-nums",
                  sentiment === 'negative' ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-gray-100"
                )}>
                  {metric.value}
                </p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function preprocessContent(text: string): string {
  if (!text) return text
  let result = text
  result = result.replace(/([^\n])\n(#{1,6}\s+)/g, '$1\n\n$2')
  result = result.replace(/([^\n])\s+(#{1,6}\s+)/g, '$1\n\n$2')
  result = result.replace(/(#{1,6}\s+[^\n]+)\n([^#\n])/g, '$1\n\n$2')
  result = result.replace(/\n{3,}/g, '\n\n')
  return result
}

function normalizeCasing(text: string): string {
  if (!text) return text
  const sentenceCase = (line: string) =>
    line.toLowerCase().replace(/(^|[.!?]\s+)(\w)/g, (_, prefix: string, char: string) => `${prefix}${char.toUpperCase()}`)

  return text.split('\n').map((line) => {
    const trimmed = line.trimStart()
    if (trimmed.startsWith('#')) return line
    const alphaChars = line.match(/[A-Za-z]/g)
    if (!alphaChars?.length) return line
    const upperRatio = alphaChars.filter((c) => c === c.toUpperCase()).length / alphaChars.length
    const lettersOnly = alphaChars.join('')
    const isAllCaps = lettersOnly === lettersOnly.toUpperCase()
    if (isAllCaps || upperRatio >= 0.4) return sentenceCase(line)
    return line
  }).join('\n')
}

function extractKeyDataAppendix(content: string): {
  beforeAppendix: string
  metrics: { label: string; value: string; icon?: string }[]
  afterAppendix: string
} | null {
  const gridMatch = content.match(/DATA_GRID_START\s*\n([\s\S]*?)\n\s*DATA_GRID_END/i)
  if (gridMatch) {
    const gridContent = gridMatch[1]
    const gridStart = content.indexOf(gridMatch[0])
    const gridEnd = gridStart + gridMatch[0].length
    const lines = gridContent.split('\n').filter(l => l.includes('|'))
    const metrics = lines.map(line => {
      const [label, value, icon] = line.split('|').map(s => s.trim())
      return { label, value, icon }
    })
    if (metrics.length > 0) {
      const beforeGrid = content.slice(0, gridStart).trim()
      const headerMatch = beforeGrid.match(/##\s*(?:Key\s+Metrics|Key\s+Data\s+Appendix|Financial\s+Snapshot)\s*$/i)
      let finalBefore = beforeGrid
      if (headerMatch) {
        finalBefore = beforeGrid.slice(0, beforeGrid.lastIndexOf(headerMatch[0])).trim()
      }
      return { beforeAppendix: finalBefore, metrics, afterAppendix: content.slice(gridEnd).trim() }
    }
  }

  const appendixMatch = content.match(/##\s*(?:Key\s+Metrics|Key\s+Data\s+Appendix)\s*\n([\s\S]*?)(?=\n##\s|\n\n##\s|$)/i)
  if (!appendixMatch) return null

  const appendixContent = appendixMatch[1]
  const appendixStart = content.indexOf(appendixMatch[0])
  const appendixEnd = appendixStart + appendixMatch[0].length
  const lines = appendixContent.split('\n')
  const metrics: { label: string; value: string; icon?: string }[] = []
  let currentLabel: string | null = null

  for (const line of lines) {
    const cleanLine = line.replace(/^[\s\-\*→]+/, '').trim()
    if (!cleanLine) continue
    const looksLikeLabel = !cleanLine.includes(':') && !/\d/.test(cleanLine) && cleanLine.length <= 60
    if (looksLikeLabel) {
      currentLabel = cleanLine
      continue
    }
    const parsedMetrics = parseMetricsFromLine(cleanLine, currentLabel)
    if (parsedMetrics.length) {
      metrics.push(...parsedMetrics)
      currentLabel = null
    }
  }

  if (metrics.length === 0) return null
  return { beforeAppendix: content.slice(0, appendixStart), metrics, afterAppendix: content.slice(appendixEnd) }
}

function highlightNumbers(text: string): React.ReactNode {
  const pattern = /(\$[\d,.]+\s*(?:billion|million|B|M)?|\d+\.?\d*%|\d+\.?\d*x)/gi
  const parts = text.split(pattern)
  
  return parts.map((part, index) => {
    if (pattern.test(part)) {
      pattern.lastIndex = 0
      return (
        <span key={index} className="text-emerald-600 dark:text-emerald-400 font-medium">
          {part}
        </span>
      )
    }
    return part
  })
}

export default function EnhancedSummary({ content, persona, chartData, healthData, filingId }: EnhancedSummaryProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const normalizedContent = useMemo(() => normalizeCasing(content), [content])
  const processedContent = preprocessContent(normalizedContent)

  const renderMarkdown = useCallback((text: string) => {
    return (
      <ReactMarkdown
        className="text-gray-800 dark:text-gray-200"
        components={{
          p: ({ children }) => {
            const processedChildren = React.Children.map(children, child => {
              if (typeof child === 'string') return highlightNumbers(child)
              return child
            })
            
            return (
              <p className="text-[15px] leading-relaxed mb-4 text-gray-700 dark:text-gray-300">
                {processedChildren}
              </p>
            )
          },
          h2: ({ children }) => (
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-10 mb-4 pb-2 border-b border-gray-200 dark:border-gray-800">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-lg font-medium text-gray-800 dark:text-gray-200 mt-8 mb-3">
              {children}
            </h3>
          ),
          ul: ({ children }) => (
            <ul className="space-y-2 my-4 ml-1">{children}</ul>
          ),
          li: ({ children }) => {
            const processedChildren = React.Children.map(children, child => {
              if (typeof child === 'string') return highlightNumbers(child)
              return child
            })
            return (
              <li className="flex items-start gap-2 text-[15px] text-gray-700 dark:text-gray-300">
                <span className="text-gray-400 mt-1.5 text-xs">•</span>
                <span className="flex-1">{processedChildren}</span>
              </li>
            )
          },
          strong: ({ children }) => (
            <strong className="font-semibold text-gray-900 dark:text-gray-100">{children}</strong>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-gray-300 dark:border-gray-700 pl-4 my-4 text-gray-600 dark:text-gray-400 italic">
              {children}
            </blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    )
  }, [])

  const hasFinancialVisuals = chartData?.current_period && (
    chartData.current_period.gross_margin != null ||
    chartData.current_period.operating_margin != null ||
    chartData.current_period.net_margin != null ||
    (chartData.prior_period && (
      chartData.prior_period.revenue != null ||
      chartData.prior_period.operating_income != null ||
      chartData.prior_period.net_income != null ||
      chartData.prior_period.free_cash_flow != null
    ))
  )

  // Render markdown with section-aware injection of financial visuals (comparison chart + margins)
  const renderMarkdownWithInjection = useCallback((text: string) => {
    // Check if we should inject visuals after Financial Performance section
    if (hasFinancialVisuals && chartData) {
      // Split by "## Financial Performance" or similar headers
      const financialPerfPattern = /^(##\s*Financial\s+Performance.*?)(\n)/im
      const match = text.match(financialPerfPattern)
      
      if (match) {
        const splitIndex = text.indexOf(match[0]) + match[0].length
        const beforeSection = text.slice(0, splitIndex)
        const afterSection = text.slice(splitIndex)
        
        // Find the next section header to know where to inject
        const nextSectionMatch = afterSection.match(/^([\s\S]*?)(?=\n##\s|$)/m)
        const sectionContent = nextSectionMatch ? nextSectionMatch[1] : afterSection
        const remainingContent = nextSectionMatch ? afterSection.slice(sectionContent.length) : ''
        
        return (
          <>
            {renderMarkdown(beforeSection + sectionContent)}
            <InlineFinancialVisuals chartData={chartData} />
            {remainingContent && renderMarkdown(remainingContent)}
          </>
        )
      }
    }
    
    return renderMarkdown(text)
  }, [renderMarkdown, hasFinancialVisuals, chartData])

  const renderFullContent = useCallback((text: string) => {
    const data = extractKeyDataAppendix(text)
    if (data) {
      return (
        <>
          {renderMarkdownWithInjection(data.beforeAppendix)}
          <MetricsGrid metrics={data.metrics} />
          {data.afterAppendix && renderMarkdownWithInjection(data.afterAppendix)}
        </>
      )
    }
    return renderMarkdownWithInjection(text)
  }, [renderMarkdownWithInjection])

  return (
    <div ref={containerRef} className="relative max-w-none">
      {/* Persona badge - subtle */}
      {persona && (
        <motion.div 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="flex items-center gap-2 mb-6 pb-4 border-b border-gray-100 dark:border-gray-800"
        >
          <div className="w-8 h-8 rounded-full overflow-hidden bg-gray-100 dark:bg-gray-800 shrink-0">
            <img src={persona.image} alt={persona.name} className="w-full h-full object-cover" />
          </div>
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{persona.name}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">{persona.tagline}</p>
          </div>
        </motion.div>
      )}

      {/* Financial Summary Card - Clean, Notion-like */}
      {chartData?.current_period && (
        <FinancialSummaryCard chartData={chartData} />
      )}

      {chartData && <CompanyInsightsSection chartData={chartData} filingId={filingId} />}

      {/* Main Content */}
      <CollapsibleSummary
        content={processedContent}
        previewLength={800}
        renderMarkdown={renderFullContent}
        className="prose-sm max-w-none"
      />
    </div>
  )
}
