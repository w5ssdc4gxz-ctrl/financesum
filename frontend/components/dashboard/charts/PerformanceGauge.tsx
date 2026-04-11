'use client'

import { memo, useId, useMemo } from 'react'
import { motion } from 'framer-motion'
import { IconTrendingUp, IconTrendingDown, IconMinus } from '@tabler/icons-react'

interface PerformanceGaugeProps {
  value: number // 0-100
  label?: string
  subtitle?: string
  size?: number
  showTrend?: boolean
  trendValue?: number
  trendDirection?: 'up' | 'down' | 'neutral'
  colorScheme?: 'default' | 'success' | 'warning' | 'danger'
}

const colorSchemes = {
  default: {
    gradient: ['#3b82f6', '#8b5cf6'],
    bgTrack: '#e5e7eb',
    text: '#3b82f6'
  },
  success: {
    gradient: ['#10b981', '#34d399'],
    bgTrack: '#d1fae5',
    text: '#10b981'
  },
  warning: {
    gradient: ['#f59e0b', '#fbbf24'],
    bgTrack: '#fed7aa',
    text: '#f59e0b'
  },
  danger: {
    gradient: ['#ef4444', '#f87171'],
    bgTrack: '#fecaca',
    text: '#ef4444'
  }
}

const getColorSchemeFromValue = (value: number): keyof typeof colorSchemes => {
  if (value >= 75) return 'success'
  if (value >= 50) return 'default'
  if (value >= 25) return 'warning'
  return 'danger'
}

const PerformanceGauge = memo(function PerformanceGauge({
  value,
  label = 'Performance',
  subtitle,
  size = 280,
  showTrend = false,
  trendValue,
  trendDirection = 'neutral',
  colorScheme
}: PerformanceGaugeProps) {
  const svgId = useId().replace(/:/g, '')
  const actualColorScheme = colorScheme || getColorSchemeFromValue(value)
  const colors = colorSchemes[actualColorScheme]

  const strokeWidth = size * 0.08 // Thinner stroke for elegance
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const gaugeStart = -220 // Slightly more open
  const gaugeEnd = 40
  const gaugeRange = gaugeEnd - gaugeStart
  const gaugeCircumference = (gaugeRange / 360) * circumference

  const progress = Math.min(Math.max(value, 0), 100)
  const offset = gaugeCircumference - (progress / 100) * gaugeCircumference

  const center = size / 2
  const gradientId = `gauge-gradient-${svgId}`

  const trendIcon = {
    up: IconTrendingUp,
    down: IconTrendingDown,
    neutral: IconMinus
  }[trendDirection]

  const TrendIcon = trendIcon

  return (
    <div className="flex flex-col items-center">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5, ease: [0.4, 0, 0.2, 1] }}
        className="relative"
        style={{ width: size, height: size }}
      >
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="overflow-visible"
        >
          <defs>
            <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor={colors.gradient[0]} />
              <stop offset="100%" stopColor={colors.gradient[1]} />
            </linearGradient>
            <filter id="glow">
              <feGaussianBlur stdDeviation="2" result="coloredBlur" />
              <feMerge>
                <feMergeNode in="coloredBlur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="shadow">
              <feDropShadow dx="0" dy="2" stdDeviation="3" floodOpacity="0.1" />
            </filter>
          </defs>

          {/* Background track */}
          <motion.circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth={strokeWidth}
            strokeOpacity={0.1}
            strokeDasharray={`${gaugeCircumference} ${circumference}`}
            strokeDashoffset={0}
            strokeLinecap="square"
            transform={`rotate(${gaugeStart} ${center} ${center})`}
            className="text-black dark:text-white"
          />



          {/* Progress arc */}
          <motion.circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth={strokeWidth}
            strokeLinecap="square"
            transform={`rotate(${gaugeStart} ${center} ${center})`}
            className="text-black dark:text-white"
            initial={{ strokeDasharray: `0 ${circumference}` }}
            animate={{ strokeDasharray: `${(progress / 100) * gaugeCircumference} ${circumference}` }}
            transition={{ duration: 1.5, delay: 0.3, ease: [0.4, 0, 0.2, 1] }}
          />

          {/* Removed Outer glow ring */}
        </svg>

        {/* Center content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.8 }}
            className="text-center"
          >
            <motion.div
              className="text-7xl font-black tracking-tighter text-black dark:text-white"
              initial={{ scale: 0.5, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ duration: 0.5, delay: 0.2 }}
            >
              {Math.round(progress)}
            </motion.div>
            <div className="mt-1 text-sm font-bold uppercase tracking-widest text-zinc-500">
              {label}
            </div>
            {subtitle && (
              <div className="mt-0.5 text-[10px] font-bold uppercase tracking-widest text-zinc-400">
                {subtitle}
              </div>
            )}
          </motion.div>
        </div>
      </motion.div>

      {/* Trend indicator */}
      {showTrend && trendValue !== undefined && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 1.2 }}
          className={`mt-4 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium ${trendDirection === 'up'
            ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400'
            : trendDirection === 'down'
              ? 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400'
              : 'bg-gray-50 text-gray-700 dark:bg-gray-900 dark:text-gray-400'
            }`}
        >
          <TrendIcon className="h-4 w-4" />
          <span>{trendValue > 0 ? '+' : ''}{trendValue}%</span>
          <span className="text-xs opacity-75">vs last period</span>
        </motion.div>
      )}
    </div>
  )
})

export default PerformanceGauge
