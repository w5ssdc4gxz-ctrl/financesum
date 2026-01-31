'use client'

import { memo, useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  Legend,
} from 'recharts'

export interface MetricDataPoint {
  name: string
  current: number
  prior: number
  unit?: 'currency' | 'percent' | 'ratio' | 'number'
  currentLabel?: string
  priorLabel?: string
}

interface QuarterComparisonChartProps {
  data: MetricDataPoint[]
  title?: string
  currentPeriodLabel?: string
  priorPeriodLabel?: string
  height?: number
  showLegend?: boolean
  animationDelay?: number
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
    return value.toFixed(2)
  }
  if (Math.abs(value) >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(1)
}

const getChangeIndicator = (current: number, prior: number): { color: string; arrow: string; percent: string } => {
  if (prior === 0) return { color: 'text-gray-500', arrow: '→', percent: 'N/A' }
  const change = ((current - prior) / Math.abs(prior)) * 100
  if (change > 0) {
    return { color: 'text-emerald-500', arrow: '↑', percent: `+${change.toFixed(1)}%` }
  } else if (change < 0) {
    return { color: 'text-rose-500', arrow: '↓', percent: `${change.toFixed(1)}%` }
  }
  return { color: 'text-gray-500', arrow: '→', percent: '0%' }
}

const QuarterComparisonChart = memo(function QuarterComparisonChart({
  data,
  title,
  currentPeriodLabel = 'Current',
  priorPeriodLabel = 'Prior',
  height = 280,
  showLegend = true,
  animationDelay = 0,
}: QuarterComparisonChartProps) {
  const chartData = useMemo(() => {
    return data.map((item) => ({
      ...item,
      name: item.name.length > 12 ? item.name.slice(0, 12) + '...' : item.name,
      fullName: item.name,
    }))
  }, [data])

  if (!data || data.length === 0) {
    return null
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, delay: animationDelay }}
      className="bg-white dark:bg-zinc-900 border-4 border-black dark:border-white p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)] my-8"
    >
      {title && (
        <h4 className="text-sm font-black uppercase tracking-wider mb-4 flex items-center gap-2">
          <span className="w-3 h-3 bg-blue-600" />
          {title}
        </h4>
      )}

      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={chartData}
          margin={{ top: 20, right: 30, left: 0, bottom: 5 }}
          barGap={2}
          barCategoryGap="20%"
        >
          <defs>
            <linearGradient id="currentGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3b82f6" stopOpacity={1} />
              <stop offset="100%" stopColor="#2563eb" stopOpacity={0.9} />
            </linearGradient>
            <linearGradient id="priorGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#9ca3af" stopOpacity={0.7} />
              <stop offset="100%" stopColor="#6b7280" stopOpacity={0.6} />
            </linearGradient>
          </defs>

          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#e5e7eb"
            strokeOpacity={0.5}
            vertical={false}
            className="dark:stroke-gray-700"
          />

          <XAxis
            dataKey="name"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11, fill: '#6b7280', fontWeight: 600 }}
            dy={10}
          />

          <YAxis
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10, fill: '#9ca3af', fontFamily: 'monospace' }}
            tickFormatter={(value) => formatValue(value)}
            width={60}
          />

          <Tooltip
            cursor={{ fill: 'rgba(59, 130, 246, 0.05)' }}
            wrapperStyle={{ zIndex: 100 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const item = payload[0].payload as MetricDataPoint & { fullName: string }
              const change = getChangeIndicator(item.current, item.prior)

              return (
                <motion.div
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-4 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]"
                >
                  <p className="font-black uppercase text-xs mb-3">{item.fullName}</p>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-4">
                      <span className="text-xs text-gray-500">{currentPeriodLabel}:</span>
                      <span className="font-mono font-bold text-blue-600">
                        {formatValue(item.current, item.unit)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-4">
                      <span className="text-xs text-gray-500">{priorPeriodLabel}:</span>
                      <span className="font-mono text-gray-500">
                        {formatValue(item.prior, item.unit)}
                      </span>
                    </div>
                    <div className={`flex items-center justify-end gap-1 pt-2 border-t border-gray-200 dark:border-gray-700 ${change.color}`}>
                      <span className="text-lg font-bold">{change.arrow}</span>
                      <span className="font-mono font-bold">{change.percent}</span>
                    </div>
                  </div>
                </motion.div>
              )
            }}
          />

          {showLegend && (
            <Legend
              verticalAlign="top"
              align="right"
              wrapperStyle={{ paddingBottom: 10 }}
              formatter={(value) => (
                <span className="text-xs font-bold uppercase tracking-wider">{value}</span>
              )}
            />
          )}

          <Bar
            dataKey="prior"
            name={priorPeriodLabel}
            fill="url(#priorGradient)"
            radius={[4, 4, 0, 0]}
            animationDuration={800}
            animationEasing="ease-out"
          />

          <Bar
            dataKey="current"
            name={currentPeriodLabel}
            fill="url(#currentGradient)"
            radius={[4, 4, 0, 0]}
            animationDuration={1000}
            animationEasing="ease-out"
          >
            {chartData.map((entry, index) => {
              const isPositiveChange = entry.current >= entry.prior
              return (
                <Cell
                  key={`cell-${index}`}
                  fill={isPositiveChange ? 'url(#currentGradient)' : '#f87171'}
                />
              )
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Change indicators below chart */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mt-4 pt-4 border-t-2 border-gray-100 dark:border-gray-800">
        {data.slice(0, 4).map((item, index) => {
          const change = getChangeIndicator(item.current, item.prior)
          return (
            <motion.div
              key={item.name}
              initial={{ opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.3, delay: animationDelay + 0.1 * index }}
              className="text-center"
            >
              <p className="text-[10px] font-bold uppercase text-gray-500 mb-1 truncate">
                {item.name}
              </p>
              <p className={`font-mono font-bold ${change.color}`}>
                {change.arrow} {change.percent}
              </p>
            </motion.div>
          )
        })}
      </div>
    </motion.div>
  )
})

export default QuarterComparisonChart
