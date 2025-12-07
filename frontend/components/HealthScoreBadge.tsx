'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

interface ComponentScores {
  financial_performance?: number
  profitability?: number
  leverage?: number
  liquidity?: number
  cash_flow?: number
  governance?: number
  growth?: number
}

type ComponentWeights = Partial<Record<keyof ComponentScores, number>>
type ComponentDescriptions = Partial<Record<keyof ComponentScores, string>>

interface HealthScoreBadgeProps {
  score: number
  band: string
  size?: 'sm' | 'md' | 'lg'
  componentScores?: ComponentScores
  componentWeights?: ComponentWeights  // Dynamic weights from user settings
  componentDescriptions?: ComponentDescriptions  // Dynamic descriptions based on weighting preference
  componentMetrics?: Partial<Record<keyof ComponentScores, string>>  // Actual metric values for display
  showBreakdown?: boolean
}

const COMPONENT_LABELS: Record<keyof ComponentScores, string> = {
  financial_performance: 'Financial Performance',
  profitability: 'Profitability',
  leverage: 'Leverage',
  liquidity: 'Liquidity',
  cash_flow: 'Cash Flow',
  governance: 'Governance',
  growth: 'Growth',
}

// Default weights (used when no custom weights provided)
const DEFAULT_WEIGHTS: Record<keyof ComponentScores, number> = {
  financial_performance: 25,
  profitability: 18,
  leverage: 15,
  liquidity: 12,
  cash_flow: 12,
  governance: 10,
  growth: 8,
}

// Default descriptions (used when no custom descriptions provided)
const DEFAULT_DESCRIPTIONS: Record<keyof ComponentScores, string> = {
  financial_performance: 'Revenue growth and operating efficiency',
  profitability: 'Margins, ROE, and ROA quality',
  leverage: 'Debt levels and interest coverage',
  liquidity: 'Short-term cash and working capital',
  cash_flow: 'Free cash flow generation strength',
  governance: 'Earnings quality and capital discipline',
  growth: 'Revenue expansion and margin trends',
}

function getComponentColor(score: number): string {
  if (score >= 85) return 'bg-green-400'
  if (score >= 70) return 'bg-blue-400'
  if (score >= 50) return 'bg-yellow-400'
  return 'bg-red-400'
}

export default function HealthScoreBadge({
  score,
  band,
  size = 'md',
  componentScores,
  componentWeights,  // Accept dynamic weights from API
  componentDescriptions,  // Accept dynamic descriptions based on weighting
  componentMetrics,  // Accept actual metric values for display
  showBreakdown = true
}: HealthScoreBadgeProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  // Use provided weights or fall back to defaults
  const weights = componentWeights && Object.keys(componentWeights).length > 0
    ? componentWeights
    : DEFAULT_WEIGHTS

  // Use provided descriptions or fall back to defaults
  const descriptions = componentDescriptions && Object.keys(componentDescriptions).length > 0
    ? componentDescriptions
    : DEFAULT_DESCRIPTIONS

  const getColorClass = () => {
    if (score >= 85) return 'bg-green-400 text-black'
    if (score >= 70) return 'bg-blue-400 text-black'
    if (score >= 50) return 'bg-yellow-400 text-black'
    return 'bg-red-400 text-black'
  }

  const sizeClasses = {
    sm: 'px-3 py-1 text-xs min-h-[32px]',
    md: 'px-4 py-2 text-sm min-h-[40px]',
    lg: 'px-6 py-3 text-base min-h-[48px]'
  }

  const scoreSizeClasses = {
    sm: 'text-base',
    md: 'text-xl',
    lg: 'text-3xl'
  }

  const hasComponents = componentScores && Object.keys(componentScores).length > 0

  return (
    <div className="inline-block">
      <motion.div
        className={`inline-flex items-center border-2 border-black dark:border-white shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] ${getColorClass()} ${sizeClasses[size]} ${hasComponents && showBreakdown ? 'cursor-pointer hover:translate-y-[-1px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)]' : ''} transition-all duration-150`}
        onClick={() => hasComponents && showBreakdown && setIsExpanded(!isExpanded)}
        whileTap={hasComponents && showBreakdown ? { scale: 0.98 } : undefined}
      >
        <div className="flex items-center space-x-2">
          <div className={`font-black ${scoreSizeClasses[size]}`}>
            {score.toFixed(1)}
          </div>
          <div className="border-l-2 border-black pl-2">
            <div className="font-bold uppercase leading-none text-[10px]">Health Score</div>
            <div className="font-mono text-[10px] font-bold uppercase">{band}</div>
          </div>
          {hasComponents && showBreakdown && (
            <div className="border-l-2 border-black pl-2">
              <motion.svg
                animate={{ rotate: isExpanded ? 180 : 0 }}
                transition={{ duration: 0.2, ease: 'easeInOut' }}
                className="w-3 h-3"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </motion.svg>
            </div>
          )}
        </div>
      </motion.div>

      <AnimatePresence mode="wait">
        {isExpanded && hasComponents && (
          <motion.div
            initial={{ opacity: 0, height: 0, scale: 0.95 }}
            animate={{ opacity: 1, height: 'auto', scale: 1 }}
            exit={{ opacity: 0, height: 0, scale: 0.95 }}
            transition={{
              duration: 0.25,
              ease: [0.4, 0, 0.2, 1],
              height: { duration: 0.2 },
              opacity: { duration: 0.15 }
            }}
            className="mt-2 bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-4 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] dark:shadow-[2px_2px_0px_0px_rgba(255,255,255,1)] origin-top"
          >
            <div className="text-xs font-bold uppercase mb-3 text-gray-600 dark:text-gray-400">
              Score Breakdown
            </div>
            <div className="space-y-3">
              {(Object.keys(COMPONENT_LABELS) as Array<keyof ComponentScores>).map((key, index) => {
                const componentScore = componentScores?.[key]
                if (componentScore === undefined) return null

                const weight = weights[key] ?? DEFAULT_WEIGHTS[key]
                const label = COMPONENT_LABELS[key]

                return (
                  <motion.div
                    key={key}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.05, duration: 0.2 }}
                  >
                    <div className="flex justify-between text-xs font-bold mb-0.5">
                      <span className="uppercase">{label}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-gray-500 font-normal">({weight}%)</span>
                        <span>{componentScore.toFixed(0)}</span>
                      </span>
                    </div>
                    <div className="text-[10px] text-gray-500 dark:text-gray-400 mb-0.5">
                      {componentMetrics?.[key]
                        ? componentMetrics[key]
                        : (descriptions[key] ?? DEFAULT_DESCRIPTIONS[key])}
                    </div>
                    <div className="h-2 bg-gray-200 dark:bg-gray-700 border border-black dark:border-white overflow-hidden">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.min(componentScore, 100)}%` }}
                        transition={{ duration: 0.4, delay: 0.1 + index * 0.05, ease: 'easeOut' }}
                        className={`h-full ${getComponentColor(componentScore)}`}
                      />
                    </div>
                  </motion.div>
                )
              })}
            </div>
            <motion.div
              className="mt-4 pt-3 border-t border-gray-300 dark:border-gray-600"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.3 }}
            >
              <div className="text-xs text-gray-500 dark:text-gray-400">
                <span className="font-bold">How it's calculated:</span> Weighted average of component scores.
                Higher scores indicate stronger financial health in each category.
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
