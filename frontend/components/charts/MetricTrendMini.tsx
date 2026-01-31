'use client'

import { memo, useMemo } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface MetricTrendMiniProps {
  label: string
  currentValue: number
  priorValue?: number
  unit?: 'currency' | 'percent' | 'ratio' | 'number'
  showSparkline?: boolean
  size?: 'sm' | 'md'
  className?: string
}

const formatValue = (value: number, unit?: string): string => {
  if (unit === 'percent') {
    return `${(value * 100).toFixed(1)}%`
  }
  if (unit === 'currency') {
    const abs = Math.abs(value)
    if (abs >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`
    if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`
    if (abs >= 1_000) return `$${(value / 1_000).toFixed(1)}K`
    return `$${value.toFixed(0)}`
  }
  if (unit === 'ratio') {
    return value.toFixed(2) + 'x'
  }
  if (Math.abs(value) >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  return value.toFixed(1)
}

const MetricTrendMini = memo(function MetricTrendMini({
  label,
  currentValue,
  priorValue,
  unit,
  showSparkline = true,
  size = 'md',
  className,
}: MetricTrendMiniProps) {
  const change = useMemo(() => {
    if (priorValue === undefined || priorValue === 0) return null
    return ((currentValue - priorValue) / Math.abs(priorValue)) * 100
  }, [currentValue, priorValue])

  const trend = useMemo(() => {
    if (change === null) return { arrow: '→', color: 'text-gray-500', bgColor: 'bg-gray-100 dark:bg-gray-800' }
    if (change > 0) return { arrow: '↑', color: 'text-emerald-600', bgColor: 'bg-emerald-50 dark:bg-emerald-900/20' }
    if (change < 0) return { arrow: '↓', color: 'text-rose-600', bgColor: 'bg-rose-50 dark:bg-rose-900/20' }
    return { arrow: '→', color: 'text-gray-500', bgColor: 'bg-gray-100 dark:bg-gray-800' }
  }, [change])

  const sizeStyles = {
    sm: {
      container: 'px-2 py-1',
      label: 'text-[9px]',
      value: 'text-sm',
      change: 'text-[10px]',
    },
    md: {
      container: 'px-3 py-2',
      label: 'text-[10px]',
      value: 'text-base',
      change: 'text-xs',
    },
  }

  const styles = sizeStyles[size]

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.3 }}
      className={cn(
        'inline-flex items-center gap-2 border-2 border-black dark:border-white',
        'shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)]',
        'bg-white dark:bg-zinc-900 hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)]',
        'transition-shadow',
        styles.container,
        className
      )}
    >
      <div className="flex flex-col">
        <span className={cn('font-bold uppercase text-gray-500', styles.label)}>
          {label}
        </span>
        <span className={cn('font-mono font-black', styles.value)}>
          {formatValue(currentValue, unit)}
        </span>
      </div>

      {change !== null && (
        <div className={cn('flex items-center gap-1 px-2 py-1 rounded', trend.bgColor)}>
          <motion.span
            initial={{ scale: 0 }}
            whileInView={{ scale: 1 }}
            viewport={{ once: true }}
            transition={{ type: 'spring', stiffness: 500, delay: 0.2 }}
            className={cn('font-bold', trend.color, styles.change)}
          >
            {trend.arrow}
          </motion.span>
          <span className={cn('font-mono font-bold', trend.color, styles.change)}>
            {change > 0 ? '+' : ''}{change.toFixed(1)}%
          </span>
        </div>
      )}

      {/* Mini sparkline visualization */}
      {showSparkline && priorValue !== undefined && (
        <div className="flex items-end gap-0.5 h-6">
          <motion.div
            initial={{ height: 0 }}
            whileInView={{ height: '60%' }}
            viewport={{ once: true }}
            transition={{ duration: 0.3, delay: 0.1 }}
            className="w-2 bg-gray-300 dark:bg-gray-600 rounded-t"
            title={`Prior: ${formatValue(priorValue, unit)}`}
          />
          <motion.div
            initial={{ height: 0 }}
            whileInView={{ 
              height: priorValue !== 0 
                ? `${Math.min(100, Math.max(20, (currentValue / priorValue) * 60))}%` 
                : '60%' 
            }}
            viewport={{ once: true }}
            transition={{ duration: 0.4, delay: 0.2 }}
            className={cn(
              'w-2 rounded-t',
              change && change > 0 ? 'bg-emerald-500' : change && change < 0 ? 'bg-rose-500' : 'bg-blue-500'
            )}
            title={`Current: ${formatValue(currentValue, unit)}`}
          />
        </div>
      )}
    </motion.div>
  )
})

export default MetricTrendMini
