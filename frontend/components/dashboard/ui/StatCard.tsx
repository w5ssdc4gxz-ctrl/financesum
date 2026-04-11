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
        'relative overflow-hidden rounded-none border border-black bg-white p-6 transition-all hover:bg-black hover:text-white dark:border-white dark:bg-zinc-950 dark:hover:bg-white dark:hover:text-black group',
        className
      )}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <p className="text-xs font-bold uppercase tracking-widest text-zinc-500 group-hover:text-zinc-400 dark:group-hover:text-zinc-500">{title}</p>
          <p className="mt-2 text-4xl font-black tracking-tighter text-black group-hover:text-white dark:text-white dark:group-hover:text-black">
            {value}
          </p>
          {description && (
            <p className="mt-1 text-xs font-bold uppercase tracking-widest text-zinc-400 group-hover:text-zinc-500">{description}</p>
          )}
        </div>
        {icon && (
          <div className="rounded-none border border-black bg-transparent p-2.5 text-black group-hover:border-white group-hover:text-white dark:border-white dark:text-white dark:group-hover:border-black dark:group-hover:text-black">
            {icon}
          </div>
        )}
      </div>

      {trend && (
        <div className="mt-4 flex items-center gap-2">
          <div className={cn('inline-flex items-center gap-1 rounded-none px-2 py-1 text-xs font-bold uppercase tracking-widest border border-black bg-transparent text-black group-hover:border-white group-hover:text-white dark:border-white dark:text-white dark:group-hover:border-black dark:group-hover:text-black')}>
            {trend.direction === 'up' && <IconTrendingUp className="h-3.5 w-3.5" />}
            {trend.direction === 'down' && <IconTrendingDown className="h-3.5 w-3.5" />}
            {trend.direction === 'neutral' && <IconMinus className="h-3.5 w-3.5" />}
            <span>{trend.value}%</span>
          </div>
          {trend.label && (
            <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 group-hover:text-zinc-400 dark:group-hover:text-zinc-500">{trend.label}</span>
          )}
        </div>
      )}
    </motion.div>
  )
}
