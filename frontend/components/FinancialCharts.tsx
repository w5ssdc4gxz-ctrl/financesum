'use client'

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useTheme } from 'next-themes'
import { motion, AnimatePresence } from 'framer-motion'
import * as d3 from 'd3'
import { cn } from '@/lib/utils'

// ────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────

interface FinancialChartsProps {
  ratios: Record<string, number | null>
}

interface RatioItem {
  name: string
  value: number
  unit: 'percent' | 'ratio' | 'times'
  context: string
  health: 'strong' | 'moderate' | 'weak' | 'neutral'
}

type Category = 'profitability' | 'liquidity' | 'leverage'

// ────────────────────────────────────────────────────────────────
// Ratio health assessment
// ────────────────────────────────────────────────────────────────

function assessHealth(name: string, value: number): RatioItem['health'] {
  const key = name.toLowerCase()
  if (key.includes('gross margin')) return value >= 40 ? 'strong' : value >= 20 ? 'moderate' : 'weak'
  if (key.includes('operating margin')) return value >= 15 ? 'strong' : value >= 5 ? 'moderate' : 'weak'
  if (key.includes('net margin')) return value >= 10 ? 'strong' : value >= 2 ? 'moderate' : 'weak'
  if (key.includes('roe')) return value >= 15 ? 'strong' : value >= 8 ? 'moderate' : 'weak'
  if (key.includes('roa')) return value >= 8 ? 'strong' : value >= 3 ? 'moderate' : 'weak'
  if (key.includes('current ratio')) return value >= 1.5 ? 'strong' : value >= 1 ? 'moderate' : 'weak'
  if (key.includes('quick ratio')) return value >= 1 ? 'strong' : value >= 0.7 ? 'moderate' : 'weak'
  if (key.includes('debt to equity')) return value <= 0.5 ? 'strong' : value <= 1.5 ? 'moderate' : 'weak'
  if (key.includes('net debt') || key.includes('ebitda')) return value <= 2 ? 'strong' : value <= 4 ? 'moderate' : 'weak'
  if (key.includes('interest coverage')) return value >= 5 ? 'strong' : value >= 2 ? 'moderate' : 'weak'
  return 'neutral'
}

function getContext(name: string, _value: number, health: RatioItem['health']): string {
  const labels = { strong: 'Strong', moderate: 'Moderate', weak: 'Needs attention', neutral: '' }
  const label = labels[health]
  const key = name.toLowerCase()
  if (key.includes('current ratio')) return `${label} short-term liquidity`
  if (key.includes('quick ratio')) return `${label} immediate liquidity`
  if (key.includes('gross margin')) return `${label} cost efficiency`
  if (key.includes('operating margin')) return `${label} operating efficiency`
  if (key.includes('net margin')) return `${label} bottom-line profitability`
  if (key.includes('roe')) return `${label} equity returns`
  if (key.includes('roa')) return `${label} asset utilization`
  if (key.includes('debt to equity')) return `${label} leverage position`
  if (key.includes('interest coverage')) return `${label} debt servicing capacity`
  if (key.includes('net debt') || key.includes('ebitda')) return `${label} debt load`
  return label
}

// ────────────────────────────────────────────────────────────────
// Build categories
// ────────────────────────────────────────────────────────────────

function buildCategories(ratios: Record<string, number | null>): Record<Category, RatioItem[]> {
  const make = (name: string, raw: number | null, unit: RatioItem['unit'], multiply = false): RatioItem | null => {
    if (raw == null) return null
    const value = multiply ? raw * 100 : raw
    const health = assessHealth(name, value)
    return { name, value, unit, context: getContext(name, value, health), health }
  }

  const profitability = [
    make('Gross Margin', ratios.gross_margin, 'percent', true),
    make('Operating Margin', ratios.operating_margin, 'percent', true),
    make('Net Margin', ratios.net_margin, 'percent', true),
    make('Return on Equity', ratios.roe, 'percent', true),
    make('Return on Assets', ratios.roa, 'percent', true),
  ].filter((x): x is RatioItem => x != null)

  const liquidity = [
    make('Current Ratio', ratios.current_ratio, 'ratio'),
    make('Quick Ratio', ratios.quick_ratio, 'ratio'),
    make('Days Sales Outstanding', ratios.dso, 'ratio'),
    make('Inventory Turnover', ratios.inventory_turnover, 'times'),
  ].filter((x): x is RatioItem => x != null)

  const leverage = [
    make('Debt to Equity', ratios.debt_to_equity, 'ratio'),
    make('Net Debt / EBITDA', ratios.net_debt_to_ebitda, 'ratio'),
    make('Interest Coverage', ratios.interest_coverage, 'times'),
  ].filter((x): x is RatioItem => x != null)

  return { profitability, liquidity, leverage }
}

// ────────────────────────────────────────────────────────────────
// Health colors
// ────────────────────────────────────────────────────────────────

const healthPalette = {
  strong: { bar: ['#34D399', '#10B981'], dot: '#059669', text: '#059669', textDark: '#34D399' },
  moderate: { bar: ['#60A5FA', '#3B82F6'], dot: '#2563EB', text: '#2563EB', textDark: '#60A5FA' },
  weak: { bar: ['#FCA5A5', '#EF4444'], dot: '#DC2626', text: '#DC2626', textDark: '#F87171' },
  neutral: { bar: ['#A5B4FC', '#6366F1'], dot: '#4F46E5', text: '#4F46E5', textDark: '#A5B4FC' },
}

// ────────────────────────────────────────────────────────────────
// Category metadata
// ────────────────────────────────────────────────────────────────

const categoryMeta: Record<Category, { label: string; color: string; gradient: string }> = {
  profitability: { label: 'Profitability', color: '#10B981', gradient: 'from-emerald-500 to-teal-500' },
  liquidity: { label: 'Liquidity', color: '#3B82F6', gradient: 'from-blue-500 to-sky-500' },
  leverage: { label: 'Leverage', color: '#F59E0B', gradient: 'from-amber-500 to-orange-500' },
}

// ────────────────────────────────────────────────────────────────
// Format helpers
// ────────────────────────────────────────────────────────────────

function formatRatio(value: number, unit: RatioItem['unit']): string {
  if (unit === 'percent') return `${value.toFixed(1)}%`
  if (unit === 'times') return `${value.toFixed(1)}x`
  return value.toFixed(2)
}

// ────────────────────────────────────────────────────────────────
// Animated Bar Row
// ────────────────────────────────────────────────────────────────

function AnimatedBarRow({
  item,
  maxValue,
  index,
  isDark,
}: {
  item: RatioItem
  maxValue: number
  index: number
  isDark: boolean
}) {
  const barRef = useRef<HTMLDivElement>(null)
  const valueRef = useRef<HTMLSpanElement>(null)
  const hasAnimated = useRef(false)
  const [hovered, setHovered] = useState(false)
  const colors = healthPalette[item.health]
  const widthPct = Math.min(100, (Math.abs(item.value) / maxValue) * 100)

  useEffect(() => {
    if (!barRef.current) return
    const bar = d3.select(barRef.current)
    if (!hasAnimated.current) {
      bar.style('width', '0%')
    }
    bar
      .transition()
      .duration(hasAnimated.current ? 300 : 700)
      .delay(hasAnimated.current ? 0 : index * 80)
      .ease(d3.easeCubicOut)
      .style('width', `${widthPct}%`)
  }, [widthPct, index])

  useEffect(() => {
    if (!valueRef.current) return
    const el = valueRef.current
    const startVal = hasAnimated.current ? item.value : 0
    const interp = d3.interpolateNumber(startVal, item.value)
    const dur = hasAnimated.current ? 300 : 700
    const delay = hasAnimated.current ? 0 : index * 80
    const timeout = setTimeout(() => {
      if (hasAnimated.current) {
        el.textContent = formatRatio(item.value, item.unit)
        return
      }
      const start = performance.now()
      const tick = () => {
        const t = Math.min(1, (performance.now() - start) / dur)
        el.textContent = formatRatio(interp(d3.easeCubicOut(t)), item.unit)
        if (t < 1) requestAnimationFrame(tick)
        else hasAnimated.current = true
      }
      requestAnimationFrame(tick)
    }, delay)
    return () => clearTimeout(timeout)
  }, [item.value, item.unit, index])

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: index * 0.06 }}
      className={cn(
        'group relative rounded-xl px-4 py-3.5 transition-colors duration-200 cursor-default',
        hovered
          ? 'bg-slate-50/80 dark:bg-gray-800/40'
          : 'hover:bg-slate-50/50 dark:hover:bg-gray-800/20'
      )}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Label row */}
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-2.5">
          <span
            className="h-2 w-2 rounded-full flex-shrink-0"
            style={{ background: isDark ? colors.textDark : colors.text }}
          />
          <span className="text-[13px] font-medium text-gray-800 dark:text-gray-200">
            {item.name}
          </span>
        </div>
        <div className="flex items-center gap-2.5">
          <span
            ref={valueRef}
            className="text-[13px] font-semibold tabular-nums"
            style={{ color: isDark ? colors.textDark : colors.text }}
          >
            {formatRatio(item.value, item.unit)}
          </span>
        </div>
      </div>

      {/* Bar */}
      <div className="relative h-2 rounded-full bg-slate-100 dark:bg-gray-800/80 overflow-hidden">
        <div
          ref={barRef}
          className="h-full rounded-full"
          style={{
            width: '0%',
            background: `linear-gradient(90deg, ${colors.bar[0]}, ${colors.bar[1]})`,
          }}
        />
      </div>

      {/* Context (appears on hover) */}
      <AnimatePresence>
        {hovered && item.context && (
          <motion.p
            initial={{ opacity: 0, height: 0, marginTop: 0 }}
            animate={{ opacity: 1, height: 'auto', marginTop: 6 }}
            exit={{ opacity: 0, height: 0, marginTop: 0 }}
            transition={{ duration: 0.2 }}
            className="text-[11px] text-gray-500 dark:text-gray-400 overflow-hidden"
          >
            {item.context}
          </motion.p>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

// ────────────────────────────────────────────────────────────────
// Main Component
// ────────────────────────────────────────────────────────────────

export default function FinancialCharts({ ratios }: FinancialChartsProps) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  const categories = useMemo(() => buildCategories(ratios), [ratios])
  const availableCategories = useMemo(
    () => (Object.entries(categories) as [Category, RatioItem[]][]).filter(([, items]) => items.length > 0).map(([key]) => key),
    [categories]
  )
  const [activeCategory, setActiveCategory] = useState<Category>(availableCategories[0] || 'profitability')

  // Reset category if current one becomes empty
  useEffect(() => {
    if (!availableCategories.includes(activeCategory) && availableCategories.length > 0) {
      setActiveCategory(availableCategories[0])
    }
  }, [availableCategories, activeCategory])

  const items = useMemo(() => categories[activeCategory] || [], [categories, activeCategory])
  const maxValue = useMemo(() => {
    const vals = items.map((item) => Math.abs(item.value))
    return Math.max(1, ...vals) * 1.15
  }, [items])

  if (availableCategories.length === 0) return null

  const meta = categoryMeta[activeCategory]

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
      className="rounded-2xl border border-slate-200/80 dark:border-gray-800 bg-gradient-to-br from-white via-slate-50/30 to-white dark:from-gray-950 dark:via-gray-950 dark:to-gray-900 shadow-sm overflow-hidden"
    >
      {/* Header with category pills */}
      <div className="px-5 py-4 border-b border-slate-100 dark:border-gray-800/80">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-2.5">
            <div
              className="w-1 h-4 rounded-full"
              style={{ background: meta.color }}
            />
            <h3 className="text-[13px] font-semibold text-gray-900 dark:text-gray-100">
              Financial Ratios
            </h3>
          </div>

          {/* Category pills */}
          <div className="flex items-center gap-1 p-0.5 rounded-lg bg-slate-100/80 dark:bg-gray-800/60">
            {availableCategories.map((cat) => {
              const isActive = cat === activeCategory
              const catMeta = categoryMeta[cat]
              return (
                <button
                  key={cat}
                  onClick={() => setActiveCategory(cat)}
                  className={cn(
                    'relative px-3 py-1.5 text-[11px] font-semibold rounded-md transition-all duration-200',
                    isActive
                      ? 'text-white'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                  )}
                >
                  {isActive && (
                    <motion.div
                      layoutId="activeTab"
                      className={cn('absolute inset-0 rounded-md bg-gradient-to-r', catMeta.gradient)}
                      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                    />
                  )}
                  <span className="relative z-10">{catMeta.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {/* Chart area */}
      <div className="p-3">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeCategory}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25 }}
            className="space-y-0.5"
          >
            {items.map((item, index) => (
              <AnimatedBarRow
                key={item.name}
                item={item}
                maxValue={maxValue}
                index={index}
                isDark={isDark}
              />
            ))}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Footer summary */}
      <div className="px-5 py-3 border-t border-slate-100 dark:border-gray-800/80 bg-slate-50/30 dark:bg-gray-900/30">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-gray-400 dark:text-gray-500 uppercase tracking-wider font-medium">
            {items.length} metric{items.length !== 1 ? 's' : ''}
          </span>
          <div className="flex items-center gap-3">
            {(['strong', 'moderate', 'weak'] as const).map((health) => {
              const count = items.filter((i) => i.health === health).length
              if (count === 0) return null
              const colors = healthPalette[health]
              return (
                <span
                  key={health}
                  className="flex items-center gap-1 text-[10px] font-medium"
                  style={{ color: isDark ? colors.textDark : colors.text }}
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: isDark ? colors.textDark : colors.text }}
                  />
                  {count} {health}
                </span>
              )
            })}
          </div>
        </div>
      </div>
    </motion.div>
  )
}
