'use client'

import React, { useState, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { PieChart, Pie, Cell, Sector, ResponsiveContainer, Tooltip } from 'recharts'
import { cn } from '@/lib/utils'
import { AnimatedNumber } from '../primitives/AnimatedNumber'
import { GlassCard } from '../primitives/Card3D'
import { 
  formatKpiValue, 
  normalizePercentageValue, 
  calculateChange,
  chartColors,
} from '@/lib/chart-utils'
import type { KPIData } from './BarKPIChart'

interface DonutKPIChartProps {
  kpi: KPIData
  currentLabel: string
}

interface DonutTooltipProps {
  active?: boolean
  payload?: any[]
  formatValue: (value: number) => string
  formatPct: (value: number) => string
}

function DonutTooltip({ active, payload, formatValue, formatPct }: DonutTooltipProps) {
  if (!active || !payload?.length) return null
  const seg = payload[0]?.payload
  if (!seg) return null

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm px-3 py-2 rounded-xl shadow-xl border border-gray-200/50 dark:border-gray-700/50"
    >
      <div className="flex items-center gap-2 mb-1">
        <div
          className="w-3 h-3 rounded-full"
          style={{ backgroundColor: seg.color }}
        />
        <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
          {seg.label}
        </p>
      </div>
      <p className="text-sm font-bold tabular-nums text-gray-700 dark:text-gray-300">
        {formatValue(seg.value)} • {formatPct(seg.value)}
      </p>
    </motion.div>
  )
}

/**
 * DonutKPIChart - Premium donut chart for segment breakdowns
 * 
 * Features:
 * - Interactive segment hover with expansion
 * - Animated center value that changes on hover
 * - Staggered segment reveal animation
 * - Gradient segment colors with glow
 */
export function DonutKPIChart({
  kpi,
  currentLabel,
}: DonutKPIChartProps) {
  const [mounted, setMounted] = useState(false)
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 100)
    return () => clearTimeout(timer)
  }, [])

  // Process segments
  const segments = useMemo(() => {
    if (!kpi.segments || kpi.segments.length < 2) return []
    return kpi.segments.map((seg, i) => ({
      ...seg,
      color: seg.color || chartColors.segments[i % chartColors.segments.length],
    }))
  }, [kpi.segments])

  if (segments.length < 2) return null

  const total = segments.reduce((sum, seg) => sum + (seg.value || 0), 0) || 1
  
  // Calculate change
  const value = normalizePercentageValue(kpi.value, kpi.unit)
  const priorValue = kpi.prior_value != null 
    ? normalizePercentageValue(kpi.prior_value, kpi.unit)
    : null
  const hasPrior = priorValue != null && priorValue !== 0
  const change = hasPrior ? calculateChange(value, priorValue) : null

  // Active segment for center display
  const activeSegment = activeIndex !== null ? segments[activeIndex] : null
  const centerValue = activeSegment ? activeSegment.value : value
  const centerLabel = activeSegment ? activeSegment.label : currentLabel

  // Format helpers
  const formatPct = (v: number) => `${((v / total) * 100).toFixed(1)}%`
  const formatValue = (v: number) => formatKpiValue(v, kpi.unit)

  // Custom active shape with expansion and glow
  const renderActiveShape = (props: any) => {
    const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill } = props
    
    return (
      <g>
        {/* Glow ring */}
        <Sector
          cx={cx}
          cy={cy}
          innerRadius={innerRadius - 2}
          outerRadius={outerRadius + 12}
          startAngle={startAngle}
          endAngle={endAngle}
          fill="transparent"
          stroke={fill}
          strokeWidth={2}
          strokeOpacity={0.3}
          style={{ filter: `drop-shadow(0 0 8px ${fill})` }}
        />
        {/* Expanded segment */}
        <Sector
          cx={cx}
          cy={cy}
          innerRadius={innerRadius}
          outerRadius={outerRadius + 8}
          startAngle={startAngle}
          endAngle={endAngle}
          fill={fill}
          stroke="rgba(255,255,255,0.8)"
          strokeWidth={2}
        />
      </g>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5 }}
    >
      <GlassCard className="relative overflow-hidden">
        {/* Background decoration */}
        <div className="pointer-events-none absolute -top-12 right-0 h-28 w-28 rounded-full bg-sky-200/40 blur-2xl dark:bg-sky-900/30" />
        
        <div className="relative p-5">
          {/* Header */}
          <div className="flex items-start justify-between mb-4">
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <motion.div 
                  className="w-2 h-2 rounded-full bg-gradient-to-r from-sky-500 to-emerald-500"
                  animate={{ scale: [1, 1.2, 1] }}
                  transition={{ duration: 2, repeat: Infinity }}
                />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                  {kpi.company_specific === false ? 'Financial Metric' : 'Key Metric'}
                </span>
              </div>
              <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                {kpi.name}
              </h3>
            </div>
            
            {change && (
              <motion.div
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.5 }}
                className={cn(
                  'flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold',
                  change.isPositive
                    ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400'
                    : 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400'
                )}
              >
                <svg
                  className={cn('w-3 h-3', !change.isPositive && 'rotate-180')}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 10l7-7m0 0l7 7m-7-7v18" />
                </svg>
                {Math.abs(change.value).toFixed(1)}%
              </motion.div>
            )}
          </div>

          {/* Chart and Legend */}
          <div className="flex items-center gap-4">
            {/* Donut */}
            <div className="relative w-36 h-36">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Tooltip content={<DonutTooltip formatValue={formatValue} formatPct={formatPct} />} />
                  <Pie
                    data={segments}
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={55}
                    paddingAngle={3}
                    dataKey="value"
                    animationBegin={0}
                    animationDuration={1200}
                    animationEasing="ease-out"
                    activeIndex={activeIndex ?? undefined}
                    activeShape={renderActiveShape}
                    onMouseEnter={(_, idx) => setActiveIndex(idx)}
                    onMouseLeave={() => setActiveIndex(null)}
                  >
                    {segments.map((entry, index) => (
                      <Cell 
                        key={`cell-${index}`} 
                        fill={entry.color}
                        stroke="rgba(255,255,255,0.5)"
                        strokeWidth={1}
                      />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              
              {/* Center value */}
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <AnimatePresence mode="wait">
                  <motion.div
                    key={activeSegment ? activeSegment.label : 'total'}
                    initial={{ opacity: 0, y: 5 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -5 }}
                    transition={{ duration: 0.2 }}
                    className="text-center"
                  >
                    <span className="text-xl font-bold text-gray-900 dark:text-gray-100 tabular-nums">
                      {formatValue(centerValue)}
                    </span>
                    <span className="block text-[10px] text-gray-500 dark:text-gray-400 truncate max-w-[80px] mx-auto">
                      {centerLabel}
                    </span>
                    {activeSegment && (
                      <span className="block text-[10px] font-semibold text-slate-500 dark:text-gray-400 tabular-nums">
                        {formatPct(activeSegment.value)}
                      </span>
                    )}
                  </motion.div>
                </AnimatePresence>
              </div>
            </div>
            
            {/* Legend */}
            <div className="flex-1 space-y-1.5">
              {segments.slice(0, 5).map((seg, idx) => (
                <motion.button
                  key={seg.label}
                  type="button"
                  className={cn(
                    'group w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-all duration-200',
                    activeIndex === idx 
                      ? 'bg-slate-100 dark:bg-gray-800' 
                      : 'hover:bg-slate-50 dark:hover:bg-gray-800/50'
                  )}
                  onMouseEnter={() => setActiveIndex(idx)}
                  onMouseLeave={() => setActiveIndex(null)}
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.3 + idx * 0.1 }}
                >
                  <motion.div
                    className="w-3 h-3 rounded-full shadow-sm"
                    style={{ backgroundColor: seg.color }}
                    animate={{ 
                      scale: activeIndex === idx ? 1.2 : 1,
                      boxShadow: activeIndex === idx ? `0 0 8px ${seg.color}` : 'none',
                    }}
                  />
                  <span className="flex-1 text-xs text-gray-600 dark:text-gray-300 truncate">
                    {seg.label}
                  </span>
                  <span className="text-[11px] font-semibold text-slate-500 dark:text-gray-400 tabular-nums">
                    {formatPct(seg.value)}
                  </span>
                  <span className="text-xs font-medium text-gray-900 dark:text-gray-100 tabular-nums min-w-[60px] text-right">
                    {formatValue(seg.value)}
                  </span>
                </motion.button>
              ))}
              
              {segments.length > 5 && (
                <motion.div
                  className="text-xs text-gray-400 dark:text-gray-500 pl-2.5"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.8 }}
                >
                  +{segments.length - 5} more segments
                </motion.div>
              )}
            </div>
          </div>

          {/* KPI explanation */}
          {kpi.description && (
            <motion.div
              className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-800"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 1 }}
            >
              <p className="text-[11px] text-slate-500 dark:text-gray-400 leading-snug line-clamp-2">
                {kpi.description}
              </p>
            </motion.div>
          )}
        </div>
      </GlassCard>
    </motion.div>
  )
}

export default DonutKPIChart
