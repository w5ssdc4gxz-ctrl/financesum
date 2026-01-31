'use client'

import { memo, useMemo } from 'react'
import { motion } from 'framer-motion'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts'

export interface HealthComponent {
  name: string
  score: number
  weight?: number
  description?: string
  metric?: string
}

interface HealthScoreDonutProps {
  overallScore: number
  components: HealthComponent[]
  title?: string
  size?: 'sm' | 'md' | 'lg'
  animationDelay?: number
  showLabels?: boolean
}

const COLORS = {
  profitability: '#10b981', // emerald
  leverage: '#f59e0b', // amber
  liquidity: '#3b82f6', // blue
  cash_flow: '#8b5cf6', // violet
  growth: '#ec4899', // pink
  governance: '#06b6d4', // cyan
  financial_performance: '#10b981', // emerald
  default: '#6b7280', // gray
}

const getColorForComponent = (name: string): string => {
  const key = name.toLowerCase().replace(/\s+/g, '_')
  return COLORS[key as keyof typeof COLORS] || COLORS.default
}

const getScoreLabel = (score: number): { label: string; color: string } => {
  if (score >= 80) return { label: 'Excellent', color: 'text-emerald-500' }
  if (score >= 65) return { label: 'Good', color: 'text-blue-500' }
  if (score >= 50) return { label: 'Fair', color: 'text-amber-500' }
  if (score >= 35) return { label: 'Watch', color: 'text-orange-500' }
  return { label: 'At Risk', color: 'text-rose-500' }
}

const HealthScoreDonut = memo(function HealthScoreDonut({
  overallScore,
  components,
  title = 'Health Score Breakdown',
  size = 'md',
  animationDelay = 0,
  showLabels = true,
}: HealthScoreDonutProps) {
  const sizeConfig = {
    sm: { outer: 80, inner: 50, container: 160, fontSize: 'text-xl' },
    md: { outer: 100, inner: 65, container: 200, fontSize: 'text-2xl' },
    lg: { outer: 120, inner: 80, container: 240, fontSize: 'text-3xl' },
  }

  const config = sizeConfig[size]
  const scoreInfo = getScoreLabel(overallScore)

  const chartData = useMemo(() => {
    return components.map((comp) => ({
      ...comp,
      value: comp.weight ?? 100 / components.length,
      fill: getColorForComponent(comp.name),
    }))
  }, [components])

  if (!components || components.length === 0) {
    return null
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      whileInView={{ opacity: 1, scale: 1 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, delay: animationDelay }}
      className="bg-white dark:bg-zinc-900 border-4 border-black dark:border-white p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] my-8"
    >
      {title && (
        <h4 className="text-sm font-black uppercase tracking-wider mb-4 flex items-center gap-2">
          <span className="w-3 h-3 bg-emerald-500" />
          {title}
        </h4>
      )}

      <div className="flex flex-col md:flex-row items-center gap-6">
        {/* Donut Chart */}
        <div className="relative" style={{ width: config.container, height: config.container }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={config.inner}
                outerRadius={config.outer}
                paddingAngle={2}
                dataKey="value"
                animationDuration={1000}
                animationEasing="ease-out"
                startAngle={90}
                endAngle={-270}
              >
                {chartData.map((entry, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={entry.fill}
                    stroke="#000"
                    strokeWidth={2}
                    className="dark:stroke-white"
                  />
                ))}
              </Pie>
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const data = payload[0].payload as HealthComponent & { fill: string }
                  return (
                    <motion.div
                      initial={{ opacity: 0, scale: 0.9 }}
                      animate={{ opacity: 1, scale: 1 }}
                      className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-3 shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)]"
                    >
                      <p className="font-black uppercase text-xs mb-1">{data.name}</p>
                      <p className="font-mono font-bold" style={{ color: data.fill }}>
                        {data.score.toFixed(0)}/100
                      </p>
                      {data.description && (
                        <p className="text-[10px] text-gray-500 mt-1 max-w-[150px]">
                          {data.description}
                        </p>
                      )}
                    </motion.div>
                  )
                }}
              />
            </PieChart>
          </ResponsiveContainer>

          {/* Center Score */}
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <motion.span
              initial={{ opacity: 0, scale: 0.5 }}
              whileInView={{ opacity: 1, scale: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: animationDelay + 0.3 }}
              className={`font-black ${config.fontSize}`}
            >
              {overallScore.toFixed(0)}
            </motion.span>
            <motion.span
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.3, delay: animationDelay + 0.5 }}
              className={`text-[10px] font-bold uppercase ${scoreInfo.color}`}
            >
              {scoreInfo.label}
            </motion.span>
          </div>
        </div>

        {/* Component Breakdown */}
        {showLabels && (
          <div className="flex-1 grid grid-cols-2 gap-3 w-full md:w-auto">
            {components.map((comp, index) => {
              const color = getColorForComponent(comp.name)
              const compScoreInfo = getScoreLabel(comp.score)
              return (
                <motion.div
                  key={comp.name}
                  initial={{ opacity: 0, x: -10 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.3, delay: animationDelay + 0.1 * index }}
                  className="flex items-start gap-2 p-2 border-2 border-gray-100 dark:border-gray-800 hover:border-black dark:hover:border-white transition-colors"
                >
                  <div
                    className="w-3 h-3 mt-0.5 flex-shrink-0"
                    style={{ backgroundColor: color }}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] font-bold uppercase text-gray-500 truncate">
                      {comp.name.replace(/_/g, ' ')}
                    </p>
                    <p className="font-mono font-bold text-sm" style={{ color }}>
                      {comp.score.toFixed(0)}
                    </p>
                    {comp.metric && (
                      <p className="text-[9px] text-gray-400 truncate">{comp.metric}</p>
                    )}
                  </div>
                </motion.div>
              )
            })}
          </div>
        )}
      </div>
    </motion.div>
  )
})

export default HealthScoreDonut
