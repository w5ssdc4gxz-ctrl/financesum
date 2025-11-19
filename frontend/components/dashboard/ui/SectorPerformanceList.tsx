'use client'

import { cn } from '@/lib/utils'
import { motion } from 'framer-motion'
import { CompanyLogo } from '@/components/CompanyLogo'

export type SectorPerformanceItem = {
  name: string
  count: number
  avgScore: number
  topTickers: string[]
  color?: string
}

interface SectorPerformanceListProps {
  data: SectorPerformanceItem[]
  className?: string
  showAnimation?: boolean
}

const getHealthColor = (score: number): string => {
  if (score >= 70) return '#10b981' // Emerald
  if (score >= 40) return '#f59e0b' // Amber
  return '#ef4444' // Red
}

export function SectorPerformanceList({
  data,
  className,
  showAnimation = true,
}: SectorPerformanceListProps) {
  // Sort by avgScore desc (Highest health first) or count? 
  // Let's keep the order passed in (which is likely count) but visualize score clearly.
  
  return (
    <div className={cn('space-y-4', className)}>
      {data.map((item, index) => {
        const healthColor = getHealthColor(item.avgScore)
        
        return (
          <motion.div
            key={item.name}
            initial={showAnimation ? { opacity: 0, y: 10 } : undefined}
            animate={showAnimation ? { opacity: 1, y: 0 } : undefined}
            transition={{ delay: index * 0.05, duration: 0.4 }}
            className="rounded-lg border border-gray-100 bg-gray-50/50 p-3 transition-colors hover:border-blue-200 hover:bg-blue-50/30 dark:border-gray-800 dark:bg-gray-900/50 dark:hover:border-blue-900 dark:hover:bg-blue-900/10"
          >
            {/* Header: Name and Count */}
            <div className="flex items-start justify-between mb-3">
              <div>
                <h4 className="text-sm font-bold text-gray-900 dark:text-gray-50 leading-tight">
                  {item.name}
                </h4>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  {item.count} {item.count === 1 ? 'company' : 'companies'}
                </p>
              </div>
              
              {/* Health Score Badge */}
              <div className="flex flex-col items-end">
                <div 
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold"
                  style={{ 
                    backgroundColor: `${healthColor}20`, 
                    color: healthColor 
                  }}
                >
                  <span>{item.avgScore}</span>
                  <span className="text-[10px] opacity-80">/ 100</span>
                </div>
                <span className="text-[10px] font-medium text-gray-400 mt-0.5">Avg. Health</span>
              </div>
            </div>

            {/* Health Bar */}
            <div className="mb-3 relative h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-800 overflow-hidden">
               <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${item.avgScore}%` }}
                  transition={{ duration: 1, ease: "easeOut", delay: index * 0.1 }}
                  className="h-full rounded-full"
                  style={{ backgroundColor: healthColor }}
                />
            </div>

            {/* Top Companies Logos */}
            {item.topTickers.length > 0 && (
              <div className="flex items-center justify-between">
                <div className="flex -space-x-2 overflow-hidden">
                  {item.topTickers.slice(0, 5).map((ticker, idx) => (
                    <motion.div
                      key={ticker}
                      initial={{ opacity: 0, scale: 0.8 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ delay: 0.2 + idx * 0.05 }}
                      className="inline-block h-6 w-6 rounded-full ring-2 ring-white dark:ring-gray-950 bg-white dark:bg-gray-900 overflow-hidden relative z-0 hover:z-10 hover:scale-110 transition-transform duration-200"
                      title={ticker}
                    >
                      <CompanyLogo ticker={ticker} className="h-full w-full object-cover" />
                    </motion.div>
                  ))}
                  {item.topTickers.length > 5 && (
                     <div className="flex h-6 w-6 items-center justify-center rounded-full ring-2 ring-white dark:ring-gray-950 bg-gray-100 dark:bg-gray-800 text-[8px] font-bold text-gray-500 z-0">
                        +{item.topTickers.length - 5}
                     </div>
                  )}
                </div>
              </div>
            )}
          </motion.div>
        )
      })}
    </div>
  )
}
