'use client'

import React, { useState, useEffect, useMemo } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'
import { AnimatedNumber, AnimatedCurrency } from '../primitives/AnimatedNumber'
import { Card3D, GlassCard } from '../primitives/Card3D'
import { 
  formatKpiValue, 
  normalizePercentageValue, 
  calculateChange,
  chartColors,
} from '@/lib/chart-utils'

export interface KPIData {
  name: string
  value: number
  prior_value?: number | null
  unit?: string
  description?: string
  company_specific?: boolean
  chart_type?: string
  period_label?: string
  prior_period_label?: string
  source_quote?: string
  segments?: { label: string; value: number; color?: string }[]
  history?: { period_label: string; value: number }[]
}

interface BarKPIChartProps {
  kpi: KPIData
  currentLabel: string
  priorLabel: string
  use3D?: boolean
}

/**
 * BarKPIChart - Premium horizontal bar comparison chart
 * 
 * Features:
 * - Spring physics bar animations
 * - Gradient fills with glow
 * - 3D card option with tilt
 * - Animated value counters
 */
export function BarKPIChart({
  kpi,
  currentLabel,
  priorLabel,
  use3D = true,
}: BarKPIChartProps) {
  const [mounted, setMounted] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 100)
    return () => clearTimeout(timer)
  }, [])

  // Normalize percentage values
  const value = normalizePercentageValue(kpi.value, kpi.unit)
  const priorValue = kpi.prior_value != null 
    ? normalizePercentageValue(kpi.prior_value, kpi.unit)
    : null
  
  const hasPrior = priorValue != null && priorValue !== 0
  const change = hasPrior ? calculateChange(value, priorValue) : null

  // Calculate bar widths
  const maxMagnitude = Math.max(Math.abs(value), Math.abs(priorValue || 0))
  const maxVal = maxMagnitude === 0 ? 1 : maxMagnitude * 1.15
  const isBoundedPercent = kpi.unit === '%' && Math.abs(value) <= 100 && (!hasPrior || Math.abs(priorValue!) <= 100)
  const scaleMax = isBoundedPercent ? 100 : maxVal
  
  const currentWidth = hasPrior
    ? (Math.abs(value) / scaleMax) * 100
    : isBoundedPercent
      ? Math.abs(value)
      : 100
  const priorWidth = hasPrior ? (Math.abs(priorValue!) / scaleMax) * 100 : 0

  const CardWrapper = use3D ? Card3D : GlassCard

  const content = (
    <div className="p-5">
      {/* Decorative background blur */}
      <div className="pointer-events-none absolute -top-12 right-0 h-28 w-28 rounded-full bg-sky-200/40 blur-2xl dark:bg-sky-900/30" />
      
      {/* Hover glow effect */}
      <motion.div
        className="pointer-events-none absolute inset-0 opacity-0 rounded-2xl"
        animate={{ opacity: isHovered ? 1 : 0 }}
        transition={{ duration: 0.25 }}
        style={{
          background: 'radial-gradient(900px circle at 30% 20%, rgba(56,189,248,0.12), transparent 45%), radial-gradient(700px circle at 80% 70%, rgba(16,185,129,0.08), transparent 45%)',
        }}
      />
      
      {/* Header */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <motion.div 
              className="w-2 h-2 rounded-full bg-gradient-to-r from-sky-500 to-emerald-500"
              animate={{ scale: [1, 1.2, 1] }}
              transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
            />
            <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              {kpi.company_specific === false ? 'Financial Metric' : 'Key Metric'}
            </span>
          </div>
          <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {kpi.name}
          </h3>
        </div>
        
        {/* Change badge */}
        {change && (
          <motion.div
            initial={{ opacity: 0, x: 10, scale: 0.9 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            transition={{ delay: 0.5, type: 'spring', stiffness: 200 }}
            className={cn(
              'flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold',
              change.isPositive
                ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400'
                : 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400'
            )}
          >
            <motion.svg
              className={cn('w-3 h-3', !change.isPositive && 'rotate-180')}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              initial={{ y: change.isPositive ? 5 : -5 }}
              animate={{ y: 0 }}
              transition={{ delay: 0.6, type: 'spring' }}
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 10l7-7m0 0l7 7m-7-7v18" />
            </motion.svg>
            {Math.abs(change.value).toFixed(1)}%
          </motion.div>
        )}
      </div>

      {/* Bars */}
      <div className="space-y-4">
        {/* Current Period */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400">{currentLabel}</span>
            <span className="text-sm font-bold text-gray-900 dark:text-gray-100 tabular-nums">
              <AnimatedNumber
                value={value}
                format={(v) => formatKpiValue(v, kpi.unit)}
                duration={1.2}
                delay={0.2}
              />
            </span>
          </div>
          <div className="relative h-5 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
            {/* Gradient track effect */}
            <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent" />
            
            <motion.div
              className={cn(
                'h-full rounded-full relative overflow-hidden',
                value >= 0
                  ? 'bg-gradient-to-r from-sky-500 via-teal-500 to-emerald-500'
                  : 'bg-gradient-to-r from-rose-500 to-red-500'
              )}
              initial={{ width: 0 }}
              animate={{ width: mounted ? `${currentWidth}%` : 0 }}
              transition={{ 
                duration: 1,
                delay: 0.1,
                ease: [0.34, 1.56, 0.64, 1], // Spring-like
              }}
            >
              {/* Shimmer effect */}
              <motion.div
                className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent"
                animate={{ x: ['-100%', '200%'] }}
                transition={{ duration: 2, delay: 1, repeat: Infinity, repeatDelay: 3 }}
              />
            </motion.div>
          </div>
        </div>
        
        {/* Prior Period */}
        {hasPrior && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-gray-500 dark:text-gray-400">{priorLabel}</span>
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400 tabular-nums">
                <AnimatedNumber
                  value={priorValue!}
                  format={(v) => formatKpiValue(v, kpi.unit)}
                  duration={1.2}
                  delay={0.4}
                />
              </span>
            </div>
            <div className="h-4 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
              <motion.div
                className={cn(
                  'h-full rounded-full',
                  priorValue! < 0 
                    ? 'bg-rose-200/80 dark:bg-rose-900/40' 
                    : 'bg-slate-300/80 dark:bg-gray-600'
                )}
                initial={{ width: 0 }}
                animate={{ width: mounted ? `${priorWidth}%` : 0 }}
                transition={{ duration: 1, delay: 0.3, ease: [0.4, 0, 0.2, 1] }}
              />
            </div>
          </div>
        )}
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
  )

  return (
    <motion.div
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5 }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <CardWrapper className="relative overflow-hidden">
        {content}
      </CardWrapper>
    </motion.div>
  )
}

/**
 * MetricCard - Simple hero metric display
 */
export function MetricCard({
  kpi,
  currentLabel,
}: {
  kpi: KPIData
  currentLabel: string
}) {
  const value = normalizePercentageValue(kpi.value, kpi.unit)
  const priorValue = kpi.prior_value != null 
    ? normalizePercentageValue(kpi.prior_value, kpi.unit)
    : null
  const change = priorValue ? calculateChange(value, priorValue) : null

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
    >
      <GlassCard className="p-5">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-gradient-to-r from-sky-500 to-emerald-500" />
            <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">
              {kpi.company_specific === false ? 'Financial Metric' : 'Key Metric'}
            </span>
          </div>
          {change && (
            <span className={cn(
              'text-xs font-semibold px-2 py-0.5 rounded-full',
              change.isPositive
                ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400'
                : 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400'
            )}>
              {change.display}
            </span>
          )}
        </div>
        
        <h3 className="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">
          {kpi.name}
        </h3>
        
        <div className="text-3xl font-bold text-gray-900 dark:text-gray-100 tabular-nums">
          <AnimatedNumber
            value={value}
            format={(v) => formatKpiValue(v, kpi.unit)}
            duration={1.5}
          />
        </div>
        
        <div className="mt-2 text-xs text-gray-500">
          {currentLabel}
        </div>
        
        {priorValue != null && (
          <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-800 flex items-center gap-2">
            <span className="text-xs text-gray-400">Previous:</span>
            <span className="text-sm font-medium text-gray-600 dark:text-gray-300 tabular-nums">
              {formatKpiValue(priorValue, kpi.unit)}
            </span>
          </div>
        )}

        {kpi.description && (
          <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
            <p className="text-[11px] text-slate-500 dark:text-gray-400 leading-snug line-clamp-2">
              {kpi.description}
            </p>
          </div>
        )}
      </GlassCard>
    </motion.div>
  )
}

export default BarKPIChart
