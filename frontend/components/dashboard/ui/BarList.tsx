'use client'

import { cn } from '@/lib/utils'
import { motion } from 'framer-motion'

export type BarListItem = {
  name: string
  value: number
  hint?: string
  color?: string
}

interface BarListProps {
  data: BarListItem[]
  className?: string
  valueFormatter?: (value: number) => string
  showAnimation?: boolean
  onValueChange?: (item: BarListItem) => void
}

const defaultColors = [
  'bg-blue-500',
  'bg-emerald-500',
  'bg-violet-500',
  'bg-amber-500',
  'bg-cyan-500',
  'bg-pink-500',
]

export function BarList({
  data,
  className,
  valueFormatter = (value) => value.toString(),
  showAnimation = true,
  onValueChange
}: BarListProps) {
  const maxValue = Math.max(...data.map((item) => item.value), 1)

  return (
    <div className={cn('space-y-3', className)}>
      {data.map((item, index) => {
        const width = Math.max(2, (item.value / maxValue) * 100)
        const Component = onValueChange ? 'button' : 'div'
        const barColor = item.color || defaultColors[index % defaultColors.length]

        return (
          <Component
            key={item.name}
            onClick={() => onValueChange?.(item)}
            className={cn(
              'group w-full text-left',
              onValueChange && 'cursor-pointer rounded-md p-1 -m-1 hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors'
            )}
          >
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-sm font-medium text-gray-900 dark:text-gray-50">
                {item.name}
              </span>
              <span className="text-sm font-medium tabular-nums text-gray-900 dark:text-gray-50">
                {valueFormatter(item.value)}
              </span>
            </div>
            <div className="relative h-2 w-full rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden">
              {showAnimation ? (
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${width}%` }}
                  transition={{ duration: 0.8, ease: [0.4, 0, 0.2, 1], delay: index * 0.05 }}
                  className={cn('h-full rounded-full', barColor)}
                />
              ) : (
                <div
                  className={cn('h-full rounded-full transition-all duration-300', barColor)}
                  style={{ width: `${width}%` }}
                />
              )}
            </div>
            {item.hint && (
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{item.hint}</p>
            )}
          </Component>
        )
      })}
    </div>
  )
}
