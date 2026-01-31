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
  ReferenceLine,
} from 'recharts'

export interface ProfitabilityMetric {
  name: string
  value: number
  benchmark?: number
}

interface ProfitabilityBarChartProps {
  data: ProfitabilityMetric[]
  title?: string
  height?: number
  showBenchmark?: boolean
  animationDelay?: number
}

const formatPercent = (value: number): string => {
  return `${(value * 100).toFixed(1)}%`
}

const getBarColor = (value: number): string => {
  if (value >= 0.2) return '#10b981' // emerald - excellent
  if (value >= 0.1) return '#3b82f6' // blue - good
  if (value >= 0.05) return '#f59e0b' // amber - fair
  if (value >= 0) return '#f97316' // orange - low
  return '#ef4444' // red - negative
}

const ProfitabilityBarChart = memo(function ProfitabilityBarChart({
  data,
  title = 'Profitability Margins',
  height = 200,
  showBenchmark = false,
  animationDelay = 0,
}: ProfitabilityBarChartProps) {
  const chartData = useMemo(() => {
    return data.map((item) => ({
      ...item,
      displayValue: item.value * 100, // Convert to percentage
      benchmarkValue: item.benchmark ? item.benchmark * 100 : undefined,
      fill: getBarColor(item.value),
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
          <span className="w-3 h-3 bg-emerald-500" />
          {title}
        </h4>
      )}

      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 5, right: 30, left: 80, bottom: 5 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#e5e7eb"
            strokeOpacity={0.5}
            horizontal={false}
            className="dark:stroke-gray-700"
          />

          <XAxis
            type="number"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10, fill: '#9ca3af', fontFamily: 'monospace' }}
            tickFormatter={(value) => `${value.toFixed(0)}%`}
            domain={['dataMin - 5', 'dataMax + 5']}
          />

          <YAxis
            type="category"
            dataKey="name"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11, fill: '#6b7280', fontWeight: 600 }}
            width={80}
          />

          <ReferenceLine x={0} stroke="#000" strokeWidth={2} className="dark:stroke-white" />

          <Tooltip
            cursor={{ fill: 'rgba(59, 130, 246, 0.05)' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const item = payload[0].payload as ProfitabilityMetric & { displayValue: number; fill: string }

              return (
                <motion.div
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-3 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]"
                >
                  <p className="font-black uppercase text-xs mb-2">{item.name}</p>
                  <p className="font-mono font-bold text-lg" style={{ color: item.fill }}>
                    {formatPercent(item.value)}
                  </p>
                  {item.benchmark !== undefined && showBenchmark && (
                    <p className="text-xs text-gray-500 mt-1">
                      Benchmark: {formatPercent(item.benchmark)}
                    </p>
                  )}
                </motion.div>
              )
            }}
          />

          <Bar
            dataKey="displayValue"
            radius={[0, 4, 4, 0]}
            animationDuration={800}
            animationEasing="ease-out"
          />
        </BarChart>
      </ResponsiveContainer>

      {/* Legend with color coding */}
      <div className="flex flex-wrap gap-3 mt-4 pt-4 border-t-2 border-gray-100 dark:border-gray-800">
        {[
          { label: 'Excellent (>20%)', color: '#10b981' },
          { label: 'Good (10-20%)', color: '#3b82f6' },
          { label: 'Fair (5-10%)', color: '#f59e0b' },
          { label: 'Low (<5%)', color: '#f97316' },
        ].map((item) => (
          <div key={item.label} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5" style={{ backgroundColor: item.color }} />
            <span className="text-[10px] font-mono text-gray-500">{item.label}</span>
          </div>
        ))}
      </div>
    </motion.div>
  )
})

export default ProfitabilityBarChart
