'use client'

import React, { useState, useEffect, useMemo, useId } from 'react'
import { motion } from 'framer-motion'
import { ResponsiveContainer, AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip } from 'recharts'
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

interface TrendKPIChartProps {
  kpi: KPIData
  currentLabel: string
}

interface TrendTooltipProps {
  active?: boolean
  payload?: any[]
  trendColor: string
  unit?: string
}

function TrendTooltip({ active, payload, trendColor, unit }: TrendTooltipProps) {
  if (!active || !payload?.length) return null
  const entry = payload[0]
  return (
    <motion.div
      initial={{ opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-white/95 dark:bg-gray-900/95 backdrop-blur-sm px-3 py-2 rounded-lg shadow-xl border border-gray-200/50 dark:border-gray-700/50"
    >
      <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
        {entry?.payload?.label}
      </p>
      <p className="text-sm font-bold tabular-nums" style={{ color: trendColor }}>
        {formatKpiValue(Number(entry?.value ?? 0), unit)}
      </p>
    </motion.div>
  )
}

interface TrendDotProps {
  cx?: number
  cy?: number
  index?: number
  seriesLength: number
  activeIndex: number | null
  trendColor: string
}

function TrendDot({ cx, cy, index = 0, seriesLength, activeIndex, trendColor }: TrendDotProps) {
  if (cx == null || cy == null) return null

  const isLast = index === seriesLength - 1
  const isActive = activeIndex === index

  return (
    <motion.g>
      {/* Glow ring for last point */}
      {isLast && (
        <motion.circle
          cx={cx}
          cy={cy}
          r={8}
          fill="none"
          stroke={trendColor}
          strokeWidth={2}
          initial={{ scale: 0.5, opacity: 0 }}
          animate={{
            scale: [1, 1.5, 1],
            opacity: [0.5, 0, 0.5],
          }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      )}

      {/* Main dot */}
      <motion.circle
        cx={cx}
        cy={cy}
        r={isActive || isLast ? 5 : 3}
        fill={isLast ? trendColor : '#fff'}
        stroke={trendColor}
        strokeWidth={2}
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ delay: 0.5 + index * 0.1, type: 'spring' }}
        style={{
          filter: isLast ? `drop-shadow(0 0 6px ${trendColor})` : undefined,
        }}
      />
    </motion.g>
  )
}

/**
 * TrendKPIChart - Premium trend visualization with gradient area
 * 
 * Features:
 * - Gradient area fill with animated reveal
 * - Glowing trend line
 * - Interactive tooltip
 * - Animated data points
 */
export function TrendKPIChart({
  kpi,
  currentLabel,
}: TrendKPIChartProps) {
  const svgId = useId().replace(/:/g, '')
  const [mounted, setMounted] = useState(false)
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 100)
    return () => clearTimeout(timer)
  }, [])

  // Process history data
  const series = useMemo(() => {
    if (!Array.isArray(kpi.history)) return []
    return kpi.history
      .filter(Boolean)
      .slice(-8)
      .map((point) => ({
        label: point.period_label,
        value: normalizePercentageValue(point.value, kpi.unit),
      }))
  }, [kpi.history, kpi.unit])

  // Calculate stats
  const latestPoint = series.length > 0 ? series[series.length - 1] : null
  const priorPoint = series.length > 1 ? series[series.length - 2] : null
  const latestValue = latestPoint?.value ?? normalizePercentageValue(kpi.value, kpi.unit)
  const priorValue = priorPoint?.value ?? (kpi.prior_value != null ? normalizePercentageValue(kpi.prior_value, kpi.unit) : null)
  
  const change = priorValue != null ? calculateChange(latestValue, priorValue) : null
  const effectiveLabel = latestPoint?.label?.trim() || currentLabel

  // Determine if trend is up or down
  const trendUp = series.length >= 2 && series[series.length - 1].value >= series[0].value
  const trendColor = trendUp ? '#10B981' : '#EF4444'
  const trendGradientId = `trendGradient-${svgId}`
  const trendGlowId = `trendGlow-${svgId}`

  return (
    <motion.div
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5 }}
    >
      <GlassCard className="relative overflow-hidden">
        {/* Background glow */}
        <div 
          className="pointer-events-none absolute -top-20 -right-20 h-40 w-40 rounded-full blur-3xl opacity-30"
          style={{ backgroundColor: trendColor }}
        />
        
        <div className="relative p-5">
          {/* Header */}
          <div className="flex items-start justify-between mb-4">
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <motion.div 
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: trendColor }}
                  animate={{ scale: [1, 1.2, 1] }}
                  transition={{ duration: 2, repeat: Infinity }}
                />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                  Trend
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

          {/* Value and Chart */}
          <div className="flex items-end gap-4">
            <div className="min-w-[120px]">
              <motion.div
                className="text-3xl font-bold text-gray-900 dark:text-gray-100 tabular-nums"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 }}
              >
                <AnimatedNumber
                  value={latestValue}
                  format={(v) => formatKpiValue(v, kpi.unit)}
                  duration={1.5}
                />
              </motion.div>
              <motion.div
                className="mt-1 text-xs text-gray-500 dark:text-gray-400"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.4 }}
              >
                {effectiveLabel}
              </motion.div>
              
              {priorValue != null && (
                <motion.div
                  className="mt-3 text-xs text-gray-500 dark:text-gray-400"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.6 }}
                >
                  Previous:{' '}
                  <span className="font-medium text-gray-700 dark:text-gray-300 tabular-nums">
                    {formatKpiValue(priorValue, kpi.unit)}
                  </span>
                </motion.div>
              )}
            </div>

            {/* Chart */}
            <motion.div
              className="flex-1 h-24"
              initial={{ opacity: 0, scaleX: 0 }}
              animate={{ opacity: 1, scaleX: 1 }}
              transition={{ delay: 0.3, duration: 0.8 }}
              style={{ transformOrigin: 'left' }}
            >
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={series.length >= 2 ? series : [{ label: effectiveLabel, value: latestValue }]}
                  onMouseMove={(e) => {
                    if (e.activeTooltipIndex !== undefined) {
                      setActiveIndex(e.activeTooltipIndex)
                    }
                  }}
                  onMouseLeave={() => setActiveIndex(null)}
                >
                  <defs>
                    {/* Gradient fill */}
                    <linearGradient id={trendGradientId} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={trendColor} stopOpacity={0.3} />
                      <stop offset="100%" stopColor={trendColor} stopOpacity={0.02} />
                    </linearGradient>
                    
                    {/* Glow filter */}
                    <filter id={trendGlowId} x="-20%" y="-20%" width="140%" height="140%">
                      <feGaussianBlur in="SourceGraphic" stdDeviation="2" result="blur" />
                      <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                  </defs>
                  
                  <XAxis dataKey="label" hide />
                  <YAxis hide domain={['auto', 'auto']} />
                  <Tooltip content={<TrendTooltip trendColor={trendColor} unit={kpi.unit} />} cursor={false} />
                  
                  {/* Area fill */}
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="none"
                    fill={`url(#${trendGradientId})`}
                    isAnimationActive={mounted}
                    animationDuration={1000}
                  />
                  
                  {/* Main line */}
                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke={trendColor}
                    strokeWidth={3}
                    dot={<TrendDot seriesLength={series.length} activeIndex={activeIndex} trendColor={trendColor} />}
                    activeDot={{ r: 6, fill: trendColor, stroke: '#fff', strokeWidth: 2 }}
                    isAnimationActive={mounted}
                    animationDuration={1500}
                    style={{ filter: `url(#${trendGlowId})` }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </motion.div>
          </div>

          {/* KPI explanation */}
          {kpi.description && (
            <motion.div
              className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-800"
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

export default TrendKPIChart
