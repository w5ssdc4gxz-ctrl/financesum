'use client'

import { memo } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface SummaryChartCardProps {
  title?: string
  subtitle?: string
  icon?: React.ReactNode
  accentColor?: string
  children: React.ReactNode
  className?: string
  animationDelay?: number
}

const SummaryChartCard = memo(function SummaryChartCard({
  title,
  subtitle,
  icon,
  accentColor = 'bg-blue-600',
  children,
  className,
  animationDelay = 0,
}: SummaryChartCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, delay: animationDelay }}
      className={cn(
        'bg-white dark:bg-zinc-900',
        'border-4 border-black dark:border-white',
        'shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)]',
        'hover:shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[8px_8px_0px_0px_rgba(255,255,255,1)]',
        'transition-shadow duration-300',
        'overflow-hidden my-8',
        className
      )}
    >
      {/* Color accent bar */}
      <div className={cn('h-1.5 w-full', accentColor)} />
      
      {/* Header */}
      {(title || subtitle) && (
        <div className="px-6 pt-5 pb-2">
          {title && (
            <h4 className="text-sm font-black uppercase tracking-wider flex items-center gap-2">
              {icon && <span className={cn('w-3 h-3', accentColor)} />}
              {!icon && <span className={cn('w-3 h-3', accentColor)} />}
              {title}
            </h4>
          )}
          {subtitle && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 font-mono">
              {subtitle}
            </p>
          )}
        </div>
      )}
      
      {/* Content */}
      <div className="px-6 pb-6">
        {children}
      </div>
    </motion.div>
  )
})

export default SummaryChartCard
