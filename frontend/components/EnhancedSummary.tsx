'use client'

/* eslint-disable @next/next/no-img-element -- Persona images are dynamic summary assets. */

import ReactMarkdown from 'react-markdown'
import React, { useMemo, useCallback, useRef, useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { CollapsibleSummary } from './ui/CollapsibleSummary'
import { cn } from '@/lib/utils'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import {
  isRiskFactorsSection,
  parseRiskFactorsContent,
  parseSummary,
  type ParsedSummary,
  type StructuredRiskFactor,
  type SummarySection,
} from '@/lib/summary-sections'

export interface ChartData {
  current_period: {
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
}

// ─── Formatting helpers ───────────────────────────────────────────────

function formatCurrency(value: number, currencySymbol: string = '$'): string {
  const symbol = currencySymbol?.trim() ? currencySymbol : '$'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${symbol}${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${symbol}${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${symbol}${(value / 1_000).toFixed(1)}K`
  return `${symbol}${value.toFixed(0)}`
}

function formatCompact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(0)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(0)}K`
  return value.toFixed(0)
}

function calcChange(current: number, prior: number): { value: number; display: string } | null {
  if (prior === 0) return null
  const change = ((current - prior) / Math.abs(prior)) * 100
  return {
    value: change,
    display: `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`
  }
}

function getMarginTone(value: number): { bar: string; text: string; dot: string } {
  if (value >= 20) return { bar: 'bg-gradient-to-r from-emerald-500 to-teal-400', text: 'text-emerald-600 dark:text-emerald-400', dot: 'bg-emerald-500' }
  if (value >= 10) return { bar: 'bg-gradient-to-r from-sky-500 to-blue-500', text: 'text-sky-600 dark:text-sky-400', dot: 'bg-sky-500' }
  if (value >= 5) return { bar: 'bg-gradient-to-r from-amber-400 to-orange-400', text: 'text-amber-600 dark:text-amber-400', dot: 'bg-amber-400' }
  if (value >= 0) return { bar: 'bg-gradient-to-r from-orange-400 to-amber-400', text: 'text-orange-600 dark:text-orange-400', dot: 'bg-orange-400' }
  return { bar: 'bg-gradient-to-r from-rose-500 to-red-500', text: 'text-rose-600 dark:text-rose-400', dot: 'bg-rose-500' }
}

function findScrollContainer(node: HTMLElement | null): HTMLElement | null {
  let parent = node?.parentElement ?? null

  while (parent) {
    const style = window.getComputedStyle(parent)
    const overflowY = style.overflowY
    const overflow = style.overflow
    const canScroll =
      ['auto', 'scroll', 'overlay'].includes(overflowY) ||
      ['auto', 'scroll', 'overlay'].includes(overflow)

    if (canScroll && parent.scrollHeight > parent.clientHeight) {
      return parent
    }

    parent = parent.parentElement
  }

  return null
}

function scrollToElement(target: HTMLElement, scrollContainer: HTMLElement | null, offset: number) {
  // If no scroll container provided, resolve from the target element itself
  const sc = scrollContainer || findScrollContainer(target)

  if (sc) {
    const parentRect = sc.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const top = sc.scrollTop + (targetRect.top - parentRect.top) - offset

    sc.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' })
    return
  }

  const top = target.getBoundingClientRect().top + window.scrollY - offset
  window.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' })
}

// ─── Financial sub-components ────────────────────────────────────────

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
        <span className={cn("font-medium tabular-nums", current < 0 ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-gray-100")}>
          {formatValue(current)}
        </span>
        {change && (
          <span className={cn("text-sm tabular-nums", change.value >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500 dark:text-red-400")}>
            {change.display}
          </span>
        )}
      </div>
    </div>
  )
}

function InlineMarginsCard({ chartData }: { chartData: ChartData }) {
  const { current_period } = chartData
  const margins = [
    { label: 'Gross', value: current_period.gross_margin },
    { label: 'Operating', value: current_period.operating_margin },
    { label: 'Net', value: current_period.net_margin },
  ].filter(m => m.value != null)

  if (margins.length === 0) return null

  const normalizeMargin = (raw: number): number => (Math.abs(raw) <= 1.2 ? raw * 100 : raw)
  const normalizedMargins = margins.map(margin => ({ ...margin, value: normalizeMargin(margin.value!) }))
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
            <div key={margin.label} className="rounded-xl border border-slate-100 dark:border-gray-800 bg-slate-50/70 dark:bg-gray-900/40 p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className={cn("h-2.5 w-2.5 rounded-full shadow-sm", tone.dot)} />
                  <span className="text-xs font-semibold text-slate-600 dark:text-gray-300">{margin.label}</span>
                </div>
                <span className={cn("text-sm font-semibold tabular-nums", tone.text)}>{value.toFixed(1)}%</span>
              </div>
              <div className="relative h-3 rounded-full bg-white dark:bg-gray-900 shadow-inner overflow-hidden">
                {tickValues.map((tick) => {
                  const tickPct = (tick / maxScale) * 100
                  const isCenter = tick === 0
                  return (
                    <div
                      key={tick}
                      className={cn("absolute top-0 bottom-0 w-px", isCenter ? "bg-slate-300 dark:bg-gray-600" : "bg-slate-200/80 dark:bg-gray-700/70")}
                      style={{ left: `${tickPct}%` }}
                    />
                  )
                })}
                <motion.div
                  className={cn("absolute top-0 bottom-0 rounded-full", isNegative ? "bg-gradient-to-r from-rose-500 to-red-500" : tone.bar)}
                  style={{ left: 0 }}
                  initial={{ width: 0 }}
                  animate={{ width: `${widthPct}%` }}
                  transition={{ duration: 0.7, delay: 0.05 + idx * 0.12, ease: [0.4, 0, 0.2, 1] }}
                />
                <motion.div
                  className={cn("absolute top-1/2 -translate-y-1/2 h-3.5 w-3.5 rounded-full border-2 border-white dark:border-gray-900 shadow", isNegative ? "bg-rose-500" : tone.dot)}
                  style={{ left: `calc(${markerPct}% - 7px)` }}
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ duration: 0.4, delay: 0.2 + idx * 0.12 }}
                />
              </div>
              {isNegative && <p className="mt-2 text-[11px] text-rose-500">Below breakeven</p>}
              {value > maxScale && <p className="mt-2 text-[11px] text-slate-500 dark:text-gray-400">Clipped to {maxScale.toFixed(0)}% scale</p>}
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

function GroupedComparisonChart({ data, currentLabel, priorLabel }: { data: { label: string; current: number; prior: number }[]; currentLabel: string; priorLabel: string }) {
  const [mounted, setMounted] = useState(false)
  useEffect(() => { const t = setTimeout(() => setMounted(true), 100); return () => clearTimeout(t) }, [])

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
      <div className="px-5 py-4 border-b border-slate-100 dark:border-gray-800 bg-gradient-to-r from-slate-50 to-white dark:from-gray-800 dark:to-gray-900">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-4 bg-gradient-to-b from-sky-500 to-emerald-500 rounded-full" />
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Period Comparison</h3>
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
      <div className="p-4">
        <div className="h-[200px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 20, right: 10, left: -10, bottom: 5 }} barCategoryGap="20%">
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: '#6B7280', fontSize: 11, fontWeight: 500 }} />
              <YAxis axisLine={false} tickLine={false} tick={{ fill: '#9CA3AF', fontSize: 10 }} tickFormatter={(value) => formatCompact(value)} width={45} />
              <Tooltip content={<CustomBarTooltip />} cursor={{ fill: 'rgba(0,0,0,0.04)' }} />
              <Bar dataKey={currentLabel} fill="url(#barGradient)" radius={[4, 4, 0, 0]} animationDuration={800} animationBegin={0} />
              <Bar dataKey={priorLabel} fill="#D1D5DB" radius={[4, 4, 0, 0]} animationDuration={800} animationBegin={200} />
              <defs>
                <linearGradient id="barGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3B82F6" />
                  <stop offset="100%" stopColor="#6366F1" />
                </linearGradient>
              </defs>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-4 pt-4 border-t border-gray-100 dark:border-gray-800">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {chartData.map((item, idx) => {
              const isPositive = item.change >= 0
              return (
                <motion.div key={item.name} className="text-center" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 + idx * 0.1 }}>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{item.name}</p>
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100 tabular-nums">{formatCurrency(item.currentRaw)}</p>
                  <span className={cn("inline-block mt-1 text-xs font-medium px-2 py-0.5 rounded-full", isPositive ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400" : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400")}>
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

function FinancialSummaryCard({ chartData }: { chartData: ChartData }) {
  const { current_period, prior_period, current_label, prior_label } = chartData
  const hasFinancials = current_period.revenue != null || current_period.operating_income != null || current_period.net_income != null || current_period.free_cash_flow != null
  if (!hasFinancials) return null

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }} className="not-prose mb-8 rounded-2xl border border-slate-200/80 dark:border-gray-700 bg-gradient-to-br from-slate-50 to-white dark:from-gray-900 dark:to-gray-800 overflow-hidden shadow-sm">
      <div className="px-5 py-3 border-b border-slate-100 dark:border-gray-800 bg-white/50 dark:bg-gray-900/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-4 bg-gradient-to-b from-sky-500 to-emerald-500 rounded-full" />
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Key Financials</h3>
          </div>
          {prior_period && <span className="text-xs text-gray-500 dark:text-gray-400">{current_label} vs {prior_label}</span>}
        </div>
      </div>
      <div className="p-5">
        {current_period.revenue != null && <MetricRow label="Revenue" current={current_period.revenue} prior={prior_period?.revenue} />}
        {current_period.operating_income != null && <MetricRow label="Operating Income" current={current_period.operating_income} prior={prior_period?.operating_income} />}
        {current_period.net_income != null && <MetricRow label="Net Income" current={current_period.net_income} prior={prior_period?.net_income} />}
        {current_period.free_cash_flow != null && <MetricRow label="Free Cash Flow" current={current_period.free_cash_flow} prior={prior_period?.free_cash_flow} />}
      </div>
    </motion.div>
  )
}

function MetricCard({ label, current, prior, delay = 0, formatFn = formatCurrency }: { label: string; current: number; prior?: number | null; delay?: number; formatFn?: (v: number) => string }) {
  const hasPrior = prior != null && prior !== 0
  const change = hasPrior ? ((current - prior) / Math.abs(prior)) * 100 : null
  const isPositive = change != null && change >= 0
  const [mounted, setMounted] = useState(false)
  useEffect(() => { const t = setTimeout(() => setMounted(true), 50); return () => clearTimeout(t) }, [])

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
          <motion.span className={cn("text-xs font-semibold px-2 py-0.5 rounded-full", isPositive ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400" : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400")} initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: delay + 0.3 }}>
            {isPositive ? '+' : ''}{change.toFixed(1)}%
          </motion.span>
        )}
      </div>
      <motion.div className="text-xl font-bold text-gray-900 dark:text-gray-100 tabular-nums mb-2" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: delay + 0.2 }}>
        {formatFn(current)}
      </motion.div>
      {hasPrior && <div className="text-xs text-gray-400 tabular-nums">vs {formatFn(prior)}</div>}
    </motion.div>
  )
}

function FinancialPerformanceCard({ chartData }: { chartData: ChartData }) {
  const { current_period, prior_period, current_label, prior_label } = chartData
  const [mounted, setMounted] = useState(false)
  useEffect(() => { const t = setTimeout(() => setMounted(true), 50); return () => clearTimeout(t) }, [])
  if (!prior_period) return null

  const comparisonMetrics: { label: string; current: number; prior: number }[] = []
  if (current_period.operating_cash_flow != null && prior_period.operating_cash_flow != null) comparisonMetrics.push({ label: 'Operating Cash Flow', current: current_period.operating_cash_flow, prior: prior_period.operating_cash_flow })
  if (current_period.gross_profit != null && prior_period.gross_profit != null) comparisonMetrics.push({ label: 'Gross Profit', current: current_period.gross_profit, prior: prior_period.gross_profit })
  if (current_period.ebitda != null && prior_period.ebitda != null) comparisonMetrics.push({ label: 'EBITDA', current: current_period.ebitda, prior: prior_period.ebitda })
  if (current_period.capex != null && prior_period.capex != null) comparisonMetrics.push({ label: 'Capital Expenditure', current: Math.abs(current_period.capex), prior: Math.abs(prior_period.capex) })

  const healthMetrics: { label: string; current: number; prior?: number | null; formatFn?: (v: number) => string }[] = []
  if (current_period.cash_and_equivalents != null) healthMetrics.push({ label: 'Cash Position', current: current_period.cash_and_equivalents, prior: prior_period.cash_and_equivalents })
  if (current_period.total_debt != null) healthMetrics.push({ label: 'Total Debt', current: current_period.total_debt, prior: prior_period.total_debt })
  if (current_period.eps != null) healthMetrics.push({ label: 'Earnings Per Share', current: current_period.eps, prior: prior_period.eps, formatFn: (v: number) => `$${v.toFixed(2)}` })
  if (current_period.rnd_expense != null) healthMetrics.push({ label: 'R&D Investment', current: current_period.rnd_expense, prior: prior_period.rnd_expense })

  if (comparisonMetrics.length === 0 && healthMetrics.length === 0) return null

  return (
    <motion.div className="my-6 space-y-5" initial={{ opacity: 0 }} animate={{ opacity: mounted ? 1 : 0 }} transition={{ duration: 0.3 }}>
      {comparisonMetrics.length > 0 && <GroupedComparisonChart data={comparisonMetrics} currentLabel={current_label} priorLabel={prior_label} />}
      {healthMetrics.length > 0 && (
        <div className="grid grid-cols-2 gap-3">
          {healthMetrics.slice(0, 4).map((metric, index) => (
            <MetricCard key={metric.label} label={metric.label} current={metric.current} prior={metric.prior} delay={index * 0.1} formatFn={metric.formatFn} />
          ))}
        </div>
      )}
    </motion.div>
  )
}

function InlineFinancialVisuals({ chartData }: { chartData: ChartData }) {
  const hasPriorData = chartData.prior_period && (chartData.prior_period.operating_cash_flow != null || chartData.prior_period.gross_profit != null || chartData.prior_period.ebitda != null || chartData.prior_period.cash_and_equivalents != null || chartData.prior_period.eps != null)
  const hasMargins = chartData.current_period.gross_margin != null || chartData.current_period.operating_margin != null || chartData.current_period.net_margin != null
  if (!hasPriorData && !hasMargins) return null
  return (
    <>
      {hasPriorData && <FinancialPerformanceCard chartData={chartData} />}
      {hasMargins && <InlineMarginsCard chartData={chartData} />}
    </>
  )
}

// ─── Key Data Appendix ────────────────────────────────────────────────

function parseMetricLine(line: string): { label: string; value: string } | null {
  const match = line.match(/^(.+?):\s*(.+)$/)
  return match ? { label: match[1].trim(), value: match[2].trim() } : null
}

function parseMetricsFromLine(line: string, currentLabel?: string | null): { label: string; value: string }[] {
  const cleaned = line.replace(/[→]/g, '|')
  const segments = cleaned.split('|').map(seg => seg.trim()).filter(Boolean)
  const metrics: { label: string; value: string }[] = []
  let usedCurrentLabel = false
  for (const segment of segments) {
    const parsed = parseMetricLine(segment.replace(/\.$/, ''))
    if (parsed) { metrics.push(parsed); continue }
    if (currentLabel && !usedCurrentLabel) {
      const value = segment.replace(/\.$/, '').trim()
      if (value) { metrics.push({ label: currentLabel, value }); usedCurrentLabel = true }
    }
  }
  return metrics
}

function getValueSentiment(value: string): 'positive' | 'negative' | 'neutral' {
  if (value.startsWith('-') || value.startsWith('(') || value.toLowerCase().includes('loss')) return 'negative'
  return 'neutral'
}

type AppendixMetric = { label: string; value: string; icon?: string }

function MetricsGrid({
  metrics,
  watchItems = [],
}: {
  metrics: AppendixMetric[]
  watchItems?: string[]
}) {
  return (
    <div className="my-8 rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/50 overflow-hidden">
      <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">Key Metrics</h3>
      </div>
      {watchItems.length > 0 && (
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-800 bg-white/80 dark:bg-gray-900/80">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">What Matters</p>
          <ul className="mt-3 space-y-2">
            {watchItems.map((item, index) => (
              <li key={`${item}-${index}`} className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                <span className="mt-1 h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="p-5">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-6">
          {metrics.map((metric, index) => {
            const sentiment = getValueSentiment(metric.value)
            return (
              <div key={index}>
                <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{metric.label}</p>
                <p className={cn("text-lg font-medium tabular-nums", sentiment === 'negative' ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-gray-100")}>{metric.value}</p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ─── Content preprocessing ────────────────────────────────────────────

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

function extractWatchItems(sectionBody: string): { watchItems: string[]; remainder: string } {
  const normalized = sectionBody.replace(/^\s+/, '')
  if (!normalized) return { watchItems: [], remainder: sectionBody }

  const lines = normalized.split('\n')
  let index = 0

  while (index < lines.length && !lines[index].trim()) index += 1
  if (index >= lines.length || !/^what matters:?\s*$/i.test(lines[index].trim())) {
    return { watchItems: [], remainder: normalized }
  }

  index += 1
  const watchItems: string[] = []

  while (index < lines.length) {
    const rawLine = lines[index]
    const trimmed = rawLine.trim()
    if (!trimmed) {
      index += 1
      if (watchItems.length > 0) break
      continue
    }
    if (/^(data_grid_start|##\s+)/i.test(trimmed)) break
    const bulletMatch = trimmed.match(/^[-*•]\s+(.+)$/)
    if (!bulletMatch) break
    const item = bulletMatch[1].trim()
    if (item) watchItems.push(item)
    index += 1
  }

  if (watchItems.length === 0) {
    return { watchItems: [], remainder: normalized }
  }

  return {
    watchItems,
    remainder: lines.slice(index).join('\n').trim(),
  }
}

function parseAppendixMetrics(sectionBody: string): AppendixMetric[] {
  const gridMatch = sectionBody.match(/DATA_GRID_START\s*\n([\s\S]*?)\n\s*DATA_GRID_END/i)
  if (gridMatch) {
    return gridMatch[1]
      .split('\n')
      .filter(line => line.includes('|'))
      .map(line => {
        const [label, value, icon] = line.split('|').map(part => part.trim())
        return { label, value, icon }
      })
      .filter(metric => metric.label && metric.value)
  }

  const lines = sectionBody.split('\n')
  const metrics: AppendixMetric[] = []
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
  return metrics
}

function extractKeyDataAppendix(content: string): { beforeAppendix: string; watchItems: string[]; metrics: AppendixMetric[]; afterAppendix: string } | null {
  const sectionMatch = content.match(/(^|\n)(##\s*(?:Key\s+Metrics|Key\s+Data\s+Appendix|Financial\s+Snapshot)\s*\n)([\s\S]*?)(?=\n##\s|\n\n##\s|$)/i)
  if (sectionMatch) {
    const sectionStart = (sectionMatch.index ?? 0) + sectionMatch[1].length
    const sectionEnd = sectionStart + sectionMatch[2].length + sectionMatch[3].length
    const { watchItems, remainder } = extractWatchItems(sectionMatch[3])
    const metrics = parseAppendixMetrics(remainder)
    if (metrics.length > 0) {
      return {
        beforeAppendix: content.slice(0, sectionStart).trim(),
        watchItems,
        metrics,
        afterAppendix: content.slice(sectionEnd).trim(),
      }
    }
  }

  const gridMatch = content.match(/DATA_GRID_START\s*\n([\s\S]*?)\n\s*DATA_GRID_END/i)
  if (!gridMatch) return null
  const gridStart = content.indexOf(gridMatch[0])
  const gridEnd = gridStart + gridMatch[0].length
  const metrics = parseAppendixMetrics(gridMatch[0])
  if (metrics.length === 0) return null
  return {
    beforeAppendix: content.slice(0, gridStart).trim(),
    watchItems: [],
    metrics,
    afterAppendix: content.slice(gridEnd).trim(),
  }
}

function highlightNumbers(text: string): React.ReactNode {
  const pattern = /(\$[\d,.]+\s*(?:billion|million|B|M)?|\d+\.?\d*%|\d+\.?\d*x)/gi
  const parts = text.split(pattern)
  return parts.map((part, index) => {
    if (pattern.test(part)) { pattern.lastIndex = 0; return <span key={index} className="text-emerald-600 dark:text-emerald-400 font-medium">{part}</span> }
    return part
  })
}

// ─── Section-aware rendering components ───────────────────────────────

const SECTION_BG: Record<string, { light: string; dark: string; border: string }> = {
  blue:    { light: 'bg-blue-50/40',    dark: 'dark:bg-blue-950/15',    border: 'border-l-blue-500' },
  emerald: { light: 'bg-emerald-50/40', dark: 'dark:bg-emerald-950/15', border: 'border-l-emerald-500' },
  amber:   { light: 'bg-amber-50/40',   dark: 'dark:bg-amber-950/15',   border: 'border-l-amber-500' },
  purple:  { light: 'bg-purple-50/40',  dark: 'dark:bg-purple-950/15',  border: 'border-l-purple-500' },
  rose:    { light: 'bg-rose-50/40',    dark: 'dark:bg-rose-950/15',    border: 'border-l-rose-500' },
  sky:     { light: 'bg-sky-50/40',     dark: 'dark:bg-sky-950/15',     border: 'border-l-sky-500' },
  gray:    { light: 'bg-gray-50/40',    dark: 'dark:bg-gray-950/15',    border: 'border-l-gray-500' },
}

const SECTION_TOC_TONE: Record<string, { dot: string; ring: string }> = {
  blue: { dot: 'bg-blue-500', ring: 'border-blue-500' },
  emerald: { dot: 'bg-emerald-500', ring: 'border-emerald-500' },
  amber: { dot: 'bg-amber-500', ring: 'border-amber-500' },
  purple: { dot: 'bg-purple-500', ring: 'border-purple-500' },
  rose: { dot: 'bg-rose-500', ring: 'border-rose-500' },
  sky: { dot: 'bg-sky-500', ring: 'border-sky-500' },
  gray: { dot: 'bg-gray-500', ring: 'border-gray-500' },
}

function SectionDivider() {
  return (
    <div className="flex justify-center items-center py-3 gap-1.5">
      <div className="w-1 h-1 rounded-full bg-gray-300 dark:bg-gray-700" />
      <div className="w-1 h-1 rounded-full bg-gray-300 dark:bg-gray-700" />
      <div className="w-1 h-1 rounded-full bg-gray-300 dark:bg-gray-700" />
    </div>
  )
}

function RiskSignalCard({
  item,
  index,
}: {
  item: StructuredRiskFactor
  index: number
}) {
  return (
    <motion.article
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-80px' }}
      whileHover={{ y: -4 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="group relative overflow-hidden rounded-[28px] border border-rose-200/70 bg-[radial-gradient(circle_at_top_right,rgba(251,113,133,0.18),transparent_34%),radial-gradient(circle_at_bottom_left,rgba(253,186,116,0.14),transparent_30%),linear-gradient(135deg,rgba(255,255,255,0.98),rgba(255,246,247,0.94))] p-6 shadow-[0_26px_80px_-42px_rgba(225,29,72,0.5)] dark:border-rose-500/20 dark:bg-[radial-gradient(circle_at_top_right,rgba(251,113,133,0.16),transparent_32%),radial-gradient(circle_at_bottom_left,rgba(56,189,248,0.12),transparent_28%),linear-gradient(135deg,rgba(15,23,42,0.94),rgba(17,24,39,0.98))]"
    >
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-rose-300/90 to-transparent dark:via-rose-400/40" />
      <div className="absolute right-6 top-6 h-20 w-20 rounded-full bg-rose-200/40 blur-3xl transition-transform duration-500 group-hover:scale-125 dark:bg-rose-500/20" />

      <div className="relative">
        <div className="flex items-start justify-between gap-4">
          <div>
            <span className="inline-flex items-center rounded-full border border-rose-200/70 bg-white/80 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.3em] text-rose-500 shadow-sm dark:border-rose-400/20 dark:bg-slate-900/80 dark:text-rose-200">
              Risk {String(index + 1).padStart(2, '0')}
            </span>
            <h3 className="mt-4 max-w-3xl text-[1.15rem] font-semibold leading-tight text-slate-900 dark:text-white md:text-[1.35rem]">
              {item.title}
            </h3>
          </div>

          <div className="hidden h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-white/70 bg-white/75 shadow-sm backdrop-blur md:flex dark:border-white/10 dark:bg-slate-900/70">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="text-rose-500 dark:text-rose-200">
              <path d="M7 17 17 7" />
              <path d="M8 7h9v9" />
            </svg>
          </div>
        </div>

        <p className="mt-5 text-[15px] leading-7 text-slate-700 dark:text-gray-300">
          {highlightNumbers(item.body)}
        </p>
      </div>
    </motion.article>
  )
}

function RiskFactorsDeck({
  content,
  renderMarkdown,
}: {
  content: string
  renderMarkdown: (text: string) => React.ReactNode
}) {
  const parsed = parseRiskFactorsContent(content)

  if (parsed.items.length === 0) {
    return <>{renderMarkdown(content)}</>
  }

  return (
    <div className="space-y-6">
      {parsed.intro && (
        <div className="max-w-3xl">
          {renderMarkdown(parsed.intro)}
        </div>
      )}

      <div className="grid gap-4">
        {parsed.items.map((item, index) => (
          <RiskSignalCard key={item.id} item={item} index={index} />
        ))}
      </div>

      {parsed.outro && (
        <div className="max-w-3xl pt-1">
          {renderMarkdown(parsed.outro)}
        </div>
      )}
    </div>
  )
}

function SectionCard({
  section,
  children,
  animate: shouldAnimate = true,
}: {
  section: SummarySection
  children: React.ReactNode
  animate?: boolean
}) {
  const bg = SECTION_BG[section.accentColor] || SECTION_BG.gray

  return (
    <motion.div
      id={`section-${section.id}`}
      initial={shouldAnimate ? { opacity: 0, y: 24 } : false}
      whileInView={shouldAnimate ? { opacity: 1, y: 0 } : undefined}
      viewport={{ once: true, margin: '-60px' }}
      transition={{ duration: 0.5, ease: [0.25, 0.1, 0.25, 1] }}
      className={cn(
        'rounded-xl border-l-2 px-6 py-5 transition-colors',
        bg.light, bg.dark, bg.border
      )}
    >
      {/* Section heading */}
      <div className="flex items-center gap-3 mb-4 pb-2 border-b border-gray-200/60 dark:border-gray-700/60">
        <span className="text-xs font-mono text-gray-400 dark:text-gray-600 tabular-nums select-none">
          {String(section.index + 1).padStart(2, '0')}
        </span>
        <div className={cn('w-1 h-5 rounded-full bg-gradient-to-b', section.accentGradient)} />
        <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
          {section.title}
        </h2>
      </div>

      {/* Pull quote */}
      {section.pullQuote && (
        <div className="relative mb-5 pl-6">
          <span
            className={cn(
              'absolute left-0 -top-1 text-4xl font-playfair leading-none select-none opacity-20',
              `text-${section.accentColor}-500`
            )}
          >
            &ldquo;
          </span>
          <p className="font-playfair text-lg leading-relaxed text-gray-800 dark:text-gray-200 italic">
            {section.pullQuote}
          </p>
          <div className="mt-3 h-px bg-gray-200 dark:bg-gray-800" />
        </div>
      )}

      {/* Section body */}
      {children}
    </motion.div>
  )
}

// ─── Section Progress Pill (CSS-transition-based, no Framer Motion) ──

function SectionProgressPill({
  sections,
  containerRef,
  summaryExpanded,
  onRequestExpand,
}: {
  sections: SummarySection[]
  containerRef: React.RefObject<HTMLDivElement | null>
  summaryExpanded: boolean
  onRequestExpand?: () => void
}) {
  const [activeSectionId, setActiveSectionId] = useState<string>(sections[0]?.id ?? '')
  const [isOpen, setIsOpen] = useState(false)
  const pillRef = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)
  const [visible, setVisible] = useState(false)
  const scrollContainerRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    setMounted(true)
    scrollContainerRef.current = findScrollContainer(containerRef.current)
    if (!scrollContainerRef.current) {
      const t = setTimeout(() => {
        scrollContainerRef.current = findScrollContainer(containerRef.current)
      }, 500)
      return () => clearTimeout(t)
    }
  }, [containerRef, summaryExpanded])

  // Track summary visibility to show/hide pill
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.05 }
    )
    observer.observe(container)
    return () => observer.disconnect()
  }, [containerRef])

  // Track active section via IntersectionObserver + scroll fallback for bottom-of-page
  useEffect(() => {
    // Lazy re-resolve scroll container if null from mount
    if (!scrollContainerRef.current) {
      scrollContainerRef.current = findScrollContainer(containerRef.current)
    }
    const sc = scrollContainerRef.current

    const handleIntersect = (entries: IntersectionObserverEntry[]) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const id = entry.target.id.replace('section-', '')
          setActiveSectionId(id)
        }
      }
    }

    const observer = new IntersectionObserver(handleIntersect, {
      root: sc,
      rootMargin: '-20% 0px -60% 0px',
      threshold: 0,
    })

    const timer = setTimeout(() => {
      sections.forEach((section) => {
        const el = document.getElementById(`section-${section.id}`)
        if (el) observer.observe(el)
      })
    }, 300)

    // Scroll fallback: when near the bottom of the scroll container,
    // the last sections can never reach the IO observation band.
    // Detect near-bottom and set active to the last visible section.
    const handleScroll = () => {
      if (!sc) return
      const nearBottom = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 100
      if (!nearBottom) return

      for (let i = sections.length - 1; i >= 0; i--) {
        const el = document.getElementById(`section-${sections[i].id}`)
        if (el) {
          const rect = el.getBoundingClientRect()
          const scRect = sc.getBoundingClientRect()
          if (rect.top < scRect.bottom) {
            setActiveSectionId(sections[i].id)
            break
          }
        }
      }
    }

    sc?.addEventListener('scroll', handleScroll, { passive: true })

    return () => {
      clearTimeout(timer)
      observer.disconnect()
      sc?.removeEventListener('scroll', handleScroll)
    }
  }, [sections, summaryExpanded, containerRef])

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: MouseEvent) => {
      if (pillRef.current && !pillRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [isOpen])

  const activeSection = sections.find((s) => s.id === activeSectionId)
  const activeIndex = sections.findIndex((s) => s.id === activeSectionId)
  const accentColor = activeSection?.accentColor ?? 'blue'

  const scrollToSection = (sectionId: string) => {
    // Lazy re-resolve scroll container in case it was null at mount
    if (!scrollContainerRef.current) {
      scrollContainerRef.current = findScrollContainer(containerRef.current)
    }

    if (!summaryExpanded) {
      // Summary is collapsed — expand first, then scroll after animation settles
      onRequestExpand?.()
      let attempts = 0
      const retry = () => {
        // Re-resolve scroll container after expand (content height changed)
        scrollContainerRef.current = findScrollContainer(containerRef.current)
        const sc = scrollContainerRef.current
        const target = document.getElementById(`section-${sectionId}`)
        if (target && sc) {
          scrollToElement(target, sc, 24)
          setIsOpen(false)
        } else if (attempts++ < 20) {
          requestAnimationFrame(retry)
        }
      }
      setTimeout(retry, 100)
      return
    }

    // Already expanded — scroll directly
    const sc = scrollContainerRef.current
    const el = document.getElementById(`section-${sectionId}`)
    if (el) {
      scrollToElement(el, sc, 24)
      setIsOpen(false)
    }
  }

  const dotColorClass = {
    blue: 'bg-blue-500',
    emerald: 'bg-emerald-500',
    amber: 'bg-amber-500',
    purple: 'bg-purple-500',
    rose: 'bg-rose-500',
    sky: 'bg-sky-500',
    gray: 'bg-gray-400',
  }[accentColor] ?? 'bg-blue-500'

  const ringColorClass = {
    blue: 'border-blue-400',
    emerald: 'border-emerald-400',
    amber: 'border-amber-400',
    purple: 'border-purple-400',
    rose: 'border-rose-400',
    sky: 'border-sky-400',
    gray: 'border-gray-300',
  }[accentColor] ?? 'border-blue-400'

  if (!mounted) return null

  const pill = (
    <div
      ref={pillRef}
      className="not-prose fixed z-50 bottom-6 right-6 transition-all duration-300 ease-out"
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? 'translateY(0) scale(1)' : 'translateY(16px) scale(0.96)',
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      {/* Expanded menu — opens upward from pill */}
      <div
        className="absolute right-0 bottom-full mb-3 w-60 max-w-[calc(100vw-2rem)] rounded-2xl border border-gray-200 bg-white/95 p-2.5 shadow-lg backdrop-blur-md dark:border-gray-700 dark:bg-slate-900/95 transition-all duration-200 ease-out origin-bottom-right"
        style={{
          opacity: isOpen ? 1 : 0,
          transform: isOpen ? 'scale(1) translateY(0)' : 'scale(0.95) translateY(8px)',
          pointerEvents: isOpen ? 'auto' : 'none',
        }}
      >
        <div className="space-y-0.5">
          {sections.map((section) => {
            const isActive = section.id === activeSectionId
            const sectionDot = {
              blue: 'bg-blue-500',
              emerald: 'bg-emerald-500',
              amber: 'bg-amber-500',
              purple: 'bg-purple-500',
              rose: 'bg-rose-500',
              sky: 'bg-sky-500',
              gray: 'bg-gray-400',
            }[section.accentColor] ?? 'bg-gray-400'

            return (
              <button
                key={section.id}
                onClick={() => scrollToSection(section.id)}
                className={cn(
                  'group flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition-colors duration-150 cursor-pointer',
                  isActive
                    ? 'bg-gray-100 dark:bg-slate-800'
                    : 'hover:bg-gray-50 dark:hover:bg-slate-800/50'
                )}
              >
                {/* Dot */}
                <span
                  className={cn(
                    'h-2 w-2 shrink-0 rounded-full transition-transform duration-150',
                    sectionDot,
                    isActive ? 'scale-125' : 'opacity-50'
                  )}
                />
                {/* Section number + title */}
                <div className="min-w-0 flex-1">
                  <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 tabular-nums">
                    {String(section.index + 1).padStart(2, '0')}
                  </p>
                  <p
                    className={cn(
                      'truncate text-[13px] leading-tight',
                      isActive
                        ? 'font-semibold text-gray-900 dark:text-gray-100'
                        : 'text-gray-500 dark:text-gray-400'
                    )}
                  >
                    {section.title}
                  </p>
                </div>
              </button>
            )
          })}
        </div>

        {/* Progress counter */}
        <div className="mt-2 flex items-center justify-center border-t border-gray-100 pt-2 dark:border-gray-800">
          <p className="text-[10px] font-medium tracking-wide text-gray-400 dark:text-gray-500">
            {activeIndex >= 0 ? activeIndex + 1 : 1}/{sections.length} sections
          </p>
        </div>
      </div>

      {/* Collapsed pill */}
      <button
        onClick={() => setIsOpen((o) => !o)}
        className="flex h-10 items-center gap-2 rounded-full border border-gray-200 bg-white/90 pl-2.5 pr-3 shadow-lg backdrop-blur-md cursor-pointer transition-transform duration-150 hover:scale-[1.03] active:scale-[0.97] dark:border-gray-700 dark:bg-slate-900/90"
      >
        {/* Dot with ring */}
        <span className="relative flex h-6 w-6 shrink-0 items-center justify-center">
          <span className={cn('h-2 w-2 rounded-full', dotColorClass)} />
          <span
            className={cn(
              'absolute h-4.5 w-4.5 rounded-full border-[1.5px] transition-colors duration-200',
              ringColorClass
            )}
          />
        </span>

        {/* Step counter + title */}
        <span className="text-xs font-bold tabular-nums text-gray-400 dark:text-gray-500">
          {activeIndex >= 0 ? activeIndex + 1 : 1}/{sections.length}
        </span>
        <span className="max-w-[120px] truncate text-xs font-medium text-gray-700 dark:text-gray-300">
          {activeSection?.title ?? sections[0]?.title ?? 'Sections'}
        </span>

        {/* Chevron — points up when menu opens upward */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-200"
          style={{ transform: isOpen ? 'rotate(0deg)' : 'rotate(180deg)' }}
        >
          <path d="M18 15l-6-6-6 6" />
        </svg>
      </button>
    </div>
  )

  return createPortal(pill, document.body)
}

// ─── Main Component ───────────────────────────────────────────────────

export default function EnhancedSummary({ content, persona, chartData, healthData: _healthData }: EnhancedSummaryProps) {
  void _healthData
  const containerRef = useRef<HTMLDivElement>(null)
  const normalizedContent = useMemo(() => normalizeCasing(content), [content])
  const processedContent = preprocessContent(normalizedContent)
  const [summaryExpanded, setSummaryExpanded] = useState(false)
  const [expandRequestSignal, setExpandRequestSignal] = useState(0)

  // Parse sections for long-form features
  const parsed: ParsedSummary = useMemo(() => parseSummary(processedContent), [processedContent])

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

  // ─── Markdown renderer for individual section bodies ─────────

  const renderSectionMarkdown = useCallback((text: string) => {
    return (
      <ReactMarkdown
        className="text-gray-800 dark:text-gray-200"
        components={{
          p: ({ children }) => {
            const processedChildren = React.Children.map(children, child =>
              typeof child === 'string' ? highlightNumbers(child) : child
            )
            return <p className="text-[15px] leading-relaxed mb-4 text-gray-700 dark:text-gray-300">{processedChildren}</p>
          },
          // In section-aware mode, h2 is already rendered by SectionCard — skip it
          h2: () => null,
          h3: ({ children }) => (
            <h3 className="text-lg font-medium text-gray-800 dark:text-gray-200 mt-8 mb-3">{children}</h3>
          ),
          ul: ({ children }) => <ul className="space-y-2 my-4 ml-1">{children}</ul>,
          li: ({ children }) => {
            const processedChildren = React.Children.map(children, child =>
              typeof child === 'string' ? highlightNumbers(child) : child
            )
            return (
              <li className="flex items-start gap-2 text-[15px] text-gray-700 dark:text-gray-300">
                <span className="text-gray-400 mt-1.5 text-xs">&#8226;</span>
                <span className="flex-1">{processedChildren}</span>
              </li>
            )
          },
          strong: ({ children }) => <strong className="font-semibold text-gray-900 dark:text-gray-100">{children}</strong>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-gray-300 dark:border-gray-700 pl-4 my-4 text-gray-600 dark:text-gray-400 italic">{children}</blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    )
  }, [])

  // ─── Classic flat markdown renderer (short-form / fallback) ─────────

  const renderFlatMarkdown = useCallback((text: string) => {
    return (
      <ReactMarkdown
        className="text-gray-800 dark:text-gray-200"
        components={{
          p: ({ children }) => {
            const processedChildren = React.Children.map(children, child =>
              typeof child === 'string' ? highlightNumbers(child) : child
            )
            return <p className="text-[15px] leading-relaxed mb-4 text-gray-700 dark:text-gray-300">{processedChildren}</p>
          },
          h2: ({ children }) => {
            const headingText = typeof children === 'string' ? children : Array.isArray(children) ? children.map(c => typeof c === 'string' ? c : '').join('') : ''
            const accent = parsed.sections.find(s => s.title.toLowerCase() === headingText.toLowerCase())?.accentGradient || 'from-gray-400 to-gray-500'
            return (
              <div className="mt-10 mb-4 pb-2 border-b border-gray-200 dark:border-gray-800 flex items-center gap-2.5">
                <div className={cn("w-1 h-5 rounded-full bg-gradient-to-b", accent)} />
                <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">{children}</h2>
              </div>
            )
          },
          h3: ({ children }) => <h3 className="text-lg font-medium text-gray-800 dark:text-gray-200 mt-8 mb-3">{children}</h3>,
          ul: ({ children }) => <ul className="space-y-2 my-4 ml-1">{children}</ul>,
          li: ({ children }) => {
            const processedChildren = React.Children.map(children, child =>
              typeof child === 'string' ? highlightNumbers(child) : child
            )
            return (
              <li className="flex items-start gap-2 text-[15px] text-gray-700 dark:text-gray-300">
                <span className="text-gray-400 mt-1.5 text-xs">&#8226;</span>
                <span className="flex-1">{processedChildren}</span>
              </li>
            )
          },
          strong: ({ children }) => <strong className="font-semibold text-gray-900 dark:text-gray-100">{children}</strong>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-gray-300 dark:border-gray-700 pl-4 my-4 text-gray-600 dark:text-gray-400 italic">{children}</blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    )
  }, [parsed.sections])

  // ─── Section-aware render (long-form) ─────────

  const renderSectioned = useCallback((text: string) => {
    const localParsed = parseSummary(text)

    // Handle key data appendix
    const appendixData = extractKeyDataAppendix(text)

    const elements: React.ReactNode[] = []

    // Preamble
    if (localParsed.preamble) {
      elements.push(
        <div key="preamble" className="mb-4">
          {renderSectionMarkdown(localParsed.preamble)}
        </div>
      )
    }

    localParsed.sections.forEach((section, idx) => {
      // Key metrics / key data appendix — rendered as MetricsGrid but keep section ID for pill navigation
      if (appendixData && (
        section.title.toLowerCase().includes('key metrics') ||
        section.title.toLowerCase().includes('key data appendix')
      )) {
        elements.push(
          <div key={`metrics-${idx}`} id={`section-${section.id}`}>
            <MetricsGrid metrics={appendixData.metrics} watchItems={appendixData.watchItems} />
          </div>
        )
        return
      }

      // Divider between sections
      if (idx > 0) {
        elements.push(<SectionDivider key={`divider-${idx}`} />)
      }

      elements.push(
        <SectionCard key={section.id} section={section} animate={true}>
          {isRiskFactorsSection(section.title) ? (
            <RiskFactorsDeck
              content={section.content}
              renderMarkdown={renderSectionMarkdown}
            />
          ) : (
            renderSectionMarkdown(section.content)
          )}

          {/* Inject financial visuals after Financial Performance section */}
          {section.id === 'financial-performance' && hasFinancialVisuals && chartData && (
            <InlineFinancialVisuals chartData={chartData} />
          )}
        </SectionCard>
      )
    })

    return <div className="space-y-2">{elements}</div>
  }, [renderSectionMarkdown, hasFinancialVisuals, chartData])

  // ─── Flat render with injection (short-form) ─────────

  const renderFlatWithInjection = useCallback((text: string) => {
    const appendixData = extractKeyDataAppendix(text)

    if (appendixData) {
      const renderChunk = (chunk: string) => {
        if (hasFinancialVisuals && chartData) {
          const financialPerfPattern = /^(##\s*Financial\s+Performance.*?)(\n)/im
          const match = chunk.match(financialPerfPattern)
          if (match) {
            const splitIndex = chunk.indexOf(match[0]) + match[0].length
            const before = chunk.slice(0, splitIndex)
            const after = chunk.slice(splitIndex)
            const nextSection = after.match(/^([\s\S]*?)(?=\n##\s|$)/m)
            const sectionContent = nextSection ? nextSection[1] : after
            const remaining = nextSection ? after.slice(sectionContent.length) : ''
            return (
              <>
                {renderFlatMarkdown(before + sectionContent)}
                <InlineFinancialVisuals chartData={chartData} />
                {remaining && renderFlatMarkdown(remaining)}
              </>
            )
          }
        }
        return renderFlatMarkdown(chunk)
      }

      return (
        <>
          {renderChunk(appendixData.beforeAppendix)}
          <MetricsGrid metrics={appendixData.metrics} watchItems={appendixData.watchItems} />
          {appendixData.afterAppendix && renderChunk(appendixData.afterAppendix)}
        </>
      )
    }

    if (hasFinancialVisuals && chartData) {
      const financialPerfPattern = /^(##\s*Financial\s+Performance.*?)(\n)/im
      const match = text.match(financialPerfPattern)
      if (match) {
        const splitIndex = text.indexOf(match[0]) + match[0].length
        const before = text.slice(0, splitIndex)
        const after = text.slice(splitIndex)
        const nextSection = after.match(/^([\s\S]*?)(?=\n##\s|$)/m)
        const sectionContent = nextSection ? nextSection[1] : after
        const remaining = nextSection ? after.slice(sectionContent.length) : ''
        return (
          <>
            {renderFlatMarkdown(before + sectionContent)}
            <InlineFinancialVisuals chartData={chartData} />
            {remaining && renderFlatMarkdown(remaining)}
          </>
        )
      }
    }

    return renderFlatMarkdown(text)
  }, [renderFlatMarkdown, hasFinancialVisuals, chartData])

  // ─── Choose render path ─────────

  const renderContent = useCallback((text: string) => {
    // Long-form: section cards with all visual enhancements
    const localParsed = parseSummary(text)
    if (localParsed.isLongForm && localParsed.sections.length >= 2) {
      return renderSectioned(text)
    }
    // Short-form: classic flat rendering
    return renderFlatWithInjection(text)
  }, [renderSectioned, renderFlatWithInjection])

  return (
    <div ref={containerRef} className="relative max-w-none">
      {/* Section progress pill — long-form only */}

      {/* Persona badge */}
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

      {/* Reading time badge — long-form only */}
      {parsed.isLongForm && parsed.sections.length >= 2 && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          className="flex items-center gap-2 mb-6 text-xs text-gray-500 dark:text-gray-400"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-60">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span>~{parsed.readingTimeMinutes} min read</span>
          <span className="text-gray-300 dark:text-gray-700">&#183;</span>
          <span>{parsed.sections.length} sections</span>
        </motion.div>
      )}

      {/* Financial Summary Card */}
      {chartData?.current_period && <FinancialSummaryCard chartData={chartData} />}

      {/* Section progress pill — long-form only */}
      {parsed.isLongForm && parsed.sections.length >= 2 && (
        <SectionProgressPill
          sections={parsed.sections}
          containerRef={containerRef}
          summaryExpanded={summaryExpanded}
          onRequestExpand={() => setExpandRequestSignal((current) => current + 1)}
        />
      )}

      {/* Main Content */}
      <CollapsibleSummary
        content={processedContent}
        previewLength={800}
        renderMarkdown={renderContent}
        className="prose-sm max-w-none"
        onExpandChange={setSummaryExpanded}
        externalExpandSignal={expandRequestSignal}
      />
    </div>
  )
}
