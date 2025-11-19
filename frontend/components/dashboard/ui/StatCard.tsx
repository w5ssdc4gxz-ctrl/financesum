'use client'

import { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { motion } from 'framer-motion'
import { IconTrendingUp, IconTrendingDown, IconMinus } from '@tabler/icons-react'

type TrendDirection = 'up' | 'down' | 'neutral'

interface StatCardProps {
  title: string
  value: string | number
  icon?: ReactNode
  trend?: {
    value: number
    direction: TrendDirection
    label?: string
  }
  description?: string
  className?: string
  animate?: boolean
}

const trendConfig = {
  up: {
    icon: IconTrendingUp,
    colorClass: 'text-emerald-600 bg-emerald-50 dark:text-emerald-400 dark:bg-emerald-950/50',
    iconColor: 'text-emerald-600 dark:text-emerald-400'
  },
  down: {
    icon: IconTrendingDown,
    colorClass: 'text-rose-600 bg-rose-50 dark:text-rose-400 dark:bg-rose-950/50',
    iconColor: 'text-rose-600 dark:text-rose-400'
  },
  neutral: {
    icon: IconMinus,
    colorClass: 'text-gray-600 bg-gray-50 dark:text-gray-400 dark:bg-gray-900',
    iconColor: 'text-gray-600 dark:text-gray-400'
  }
}

export function StatCard({
  title,
  value,
  icon,
  trend,
  description,
  className,
  animate = true
}: StatCardProps) {
  return (
    <motion.div
      initial={animate ? { opacity: 0, y: 20 } : undefined}
      animate={animate ? { opacity: 1, y: 0 } : undefined}
      transition={animate ? { duration: 0.4, ease: [0.4, 0, 0.2, 1] } : undefined}
      className={cn(
        'relative overflow-hidden rounded-lg border border-gray-200 bg-white p-6 shadow-sm transition-shadow hover:shadow-md dark:border-gray-800 dark:bg-gray-950',
        className
      )}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <p className="text-sm font-medium text-gray-600 dark:text-gray-400">{title}</p>
          <p className="mt-2 text-3xl font-semibold text-gray-900 dark:text-gray-50">
            {value}
          </p>
          {description && (
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-500">{description}</p>
          )}
        </div>
        {icon && (
          <div className="rounded-lg bg-blue-50 p-2.5 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400">
            {icon}
          </div>
        )}
      </div>

      {trend && (
        <div className="mt-4 flex items-center gap-2">
          <div className={cn('inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium', trendConfig[trend.direction].colorClass)}>
            {trend.direction === 'up' && <IconTrendingUp className="h-3.5 w-3.5" />}
            {trend.direction === 'down' && <IconTrendingDown className="h-3.5 w-3.5" />}
            {trend.direction === 'neutral' && <IconMinus className="h-3.5 w-3.5" />}
            <span>{trend.value}%</span>
          </div>
          {trend.label && (
            <span className="text-xs text-gray-500 dark:text-gray-400">{trend.label}</span>
          )}
        </div>
      )}
    </motion.div>
  )
}
