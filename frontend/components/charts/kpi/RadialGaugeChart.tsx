'use client'

import React, { useState, useEffect } from 'react'
import { motion, useSpring, useTransform } from 'framer-motion'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { cn } from '@/lib/utils'
import { AnimatedNumber } from '../primitives/AnimatedNumber'
import { GlassCard } from '../primitives/Card3D'
import { 
  formatKpiValue, 
  normalizePercentageValue, 
  calculateChange,
} from '@/lib/chart-utils'
import type { KPIData } from './BarKPIChart'

interface RadialGaugeChartProps {
  kpi: KPIData
  currentLabel: string
  priorLabel: string
}

/**
 * RadialGaugeChart - Premium radial gauge for percentage/rate metrics
 * 
 * Features:
 * - Animated arc fill with gradient
 * - Pulsing glow effect
 * - GSAP-powered number animation
 * - Prior value comparison ring
 */
export function RadialGaugeChart({
  kpi,
  currentLabel,
  priorLabel,
}: RadialGaugeChartProps) {
  const [mounted, setMounted] = useState(false)
  const [isHovered, setIsHovered] = useState(false)
  
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 100)
    return () => clearTimeout(timer)
  }, [])

  // Normalize and clamp values
  const rawValue = normalizePercentageValue(kpi.value, kpi.unit)
  const value = Math.max(0, Math.min(100, rawValue))
  
  const rawPriorValue = kpi.prior_value != null 
    ? normalizePercentageValue(kpi.prior_value, kpi.unit)
    : null
  const priorValue = rawPriorValue != null 
    ? Math.max(0, Math.min(100, rawPriorValue))
    : null
  
  const hasPrior = priorValue != null && priorValue !== 0
  const change = hasPrior ? calculateChange(rawValue, rawPriorValue!) : null

  // SVG geometry
  const size = 160
  const strokeWidth = 14
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const arcLength = circumference * 0.75 // 270 degrees
  
  // Animated offset
  const springValue = useSpring(0, { stiffness: 60, damping: 20 })
  const strokeDashoffset = useTransform(springValue, [0, 100], [arcLength, 0])
  
  useEffect(() => {
    if (mounted) {
      springValue.set(value)
    }
  }, [mounted, value, springValue])

  // Color based on value
  const getColors = () => {
    if (value >= 70) return { 
      start: '#10B981', 
      end: '#059669', 
      glow: 'rgba(16, 185, 129, 0.4)',
      bg: 'from-emerald-50 to-teal-50 dark:from-emerald-900/20 dark:to-teal-900/20',
    }
    if (value >= 40) return { 
      start: '#0EA5E9', 
      end: '#0284C7', 
      glow: 'rgba(14, 165, 233, 0.4)',
      bg: 'from-sky-50 to-blue-50 dark:from-sky-900/20 dark:to-blue-900/20',
    }
    if (value >= 20) return { 
      start: '#F59E0B', 
      end: '#D97706', 
      glow: 'rgba(245, 158, 11, 0.4)',
      bg: 'from-amber-50 to-orange-50 dark:from-amber-900/20 dark:to-orange-900/20',
    }
    return { 
      start: '#EF4444', 
      end: '#DC2626', 
      glow: 'rgba(239, 68, 68, 0.4)',
      bg: 'from-red-50 to-rose-50 dark:from-red-900/20 dark:to-rose-900/20',
    }
  }
  
  const colors = getColors()
  const gradientId = `radialGaugeGradient-${Math.random().toString(36).slice(2)}`
  const glowId = `radialGaugeGlow-${Math.random().toString(36).slice(2)}`

  return (
    <motion.div
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5 }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <GlassCard className="relative overflow-hidden">
        {/* Background gradient */}
        <div className={cn(
          'absolute inset-0 bg-gradient-to-br opacity-50',
          colors.bg
        )} />
        
        <div className="relative p-5">
          {/* Header */}
          <div className="flex items-start justify-between mb-4">
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <motion.div 
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: colors.start }}
                  animate={{ 
                    scale: [1, 1.3, 1],
                    boxShadow: [
                      `0 0 0 0 ${colors.glow}`,
                      `0 0 0 8px transparent`,
                      `0 0 0 0 ${colors.glow}`,
                    ],
                  }}
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
                transition={{ delay: 0.8 }}
                className={cn(
                  'flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold',
                  change.isPositive
                    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-400'
                    : 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400'
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

          {/* Gauge */}
          <div className="flex items-center gap-6">
            <div className="relative">
              <svg
                width={size}
                height={size}
                className="transform -rotate-[135deg]"
                style={{ overflow: 'visible' }}
              >
                <defs>
                  {/* Main gradient */}
                  <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
                    <motion.stop
                      offset="0%"
                      animate={{
                        stopColor: [colors.start, colors.end, colors.start],
                      }}
                      transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
                    />
                    <stop offset="100%" stopColor={colors.end} />
                  </linearGradient>
                  
                  {/* Glow filter */}
                  <filter id={glowId} x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur" />
                    <feMerge>
                      <feMergeNode in="blur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>
                
                {/* Background track */}
                <circle
                  cx={size / 2}
                  cy={size / 2}
                  r={radius}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={strokeWidth}
                  strokeDasharray={arcLength}
                  strokeLinecap="round"
                  className="text-gray-200 dark:text-gray-700"
                />
                
                {/* Prior value indicator (thin line) */}
                {hasPrior && (
                  <motion.circle
                    cx={size / 2}
                    cy={size / 2}
                    r={radius}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    strokeDasharray={arcLength}
                    strokeDashoffset={arcLength * (1 - priorValue / 100)}
                    strokeLinecap="round"
                    className="text-gray-400 dark:text-gray-500"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 0.5 }}
                    transition={{ delay: 1.5 }}
                  />
                )}
                
                {/* Main arc */}
                <motion.circle
                  cx={size / 2}
                  cy={size / 2}
                  r={radius}
                  fill="none"
                  stroke={`url(#${gradientId})`}
                  strokeWidth={strokeWidth}
                  strokeDasharray={arcLength}
                  style={{ strokeDashoffset }}
                  strokeLinecap="round"
                  filter={`url(#${glowId})`}
                />
                
                {/* End cap dot */}
                <motion.circle
                  cx={size / 2 + radius * Math.cos((Math.PI * (270 * value / 100)) / 180)}
                  cy={size / 2 + radius * Math.sin((Math.PI * (270 * value / 100)) / 180)}
                  r={strokeWidth / 2 + 3}
                  fill={colors.start}
                  className="transform rotate-[135deg] origin-center"
                  initial={{ scale: 0 }}
                  animate={{ scale: mounted ? 1 : 0 }}
                  transition={{ delay: 1, type: 'spring', stiffness: 200 }}
                  style={{
                    filter: `drop-shadow(0 0 6px ${colors.glow})`,
                  }}
                />
              </svg>
              
              {/* Center content */}
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <motion.div
                  className="text-3xl font-bold tabular-nums"
                  style={{ color: colors.start }}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.3 }}
                >
                  <AnimatedNumber
                    value={rawValue}
                    format={(v) => `${v.toFixed(1)}%`}
                    duration={1.5}
                    delay={0.2}
                  />
                </motion.div>
                <motion.span
                  className="text-xs text-gray-500 dark:text-gray-400 mt-1"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.5 }}
                >
                  {currentLabel}
                </motion.span>
              </div>
            </div>
            
            {/* Side info */}
            <div className="flex-1 space-y-4">
              {hasPrior && (
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.6 }}
                >
                  <p className="text-xs text-gray-400 dark:text-gray-500 mb-1">Previous</p>
                  <p className="text-lg font-semibold text-gray-600 dark:text-gray-300 tabular-nums">
                    {formatKpiValue(rawPriorValue!, kpi.unit)}
                  </p>
                  <p className="text-[10px] text-gray-400 dark:text-gray-500">{priorLabel}</p>
                </motion.div>
              )}
              
              {kpi.description && (
                <motion.p
                  className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-3"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.8 }}
                >
                  {kpi.description}
                </motion.p>
              )}
            </div>
          </div>

          {/* Source */}
          {kpi.source_quote && (
            <motion.div
              className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-800"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 1.2 }}
            >
              <p className="text-[10px] text-slate-500 dark:text-gray-400 leading-snug line-clamp-2">
                Source: &ldquo;{kpi.source_quote.slice(0, 120)}...&rdquo;
              </p>
            </motion.div>
          )}
        </div>
      </GlassCard>
    </motion.div>
  )
}

export default RadialGaugeChart
