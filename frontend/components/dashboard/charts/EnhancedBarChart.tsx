'use client'

import { memo } from 'react'
import { motion } from 'framer-motion'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { CompanyLogo } from '@/components/CompanyLogo'

export interface EnhancedBarDataPoint {
  name: string
  value: number
  color?: string
  ticker?: string
}

interface EnhancedBarChartProps {
  data: EnhancedBarDataPoint[]
  title?: string
  valueFormatter?: (value: number) => string
  height?: number
  colorScheme?: string[]
  showGrid?: boolean
}

const defaultColors = [
  '#3b82f6', // blue
  '#10b981', // emerald
  '#8b5cf6', // violet
  '#f59e0b', // amber
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#f97316', // orange
  '#a855f7'  // purple
]

const EnhancedBarChart = memo(function EnhancedBarChart({
  data,
  title,
  valueFormatter = (v) => v.toString(),
  height = 300,
  colorScheme = defaultColors,
  showGrid = true
}: EnhancedBarChartProps) {
  if (!data || data.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
        className="flex items-center justify-center rounded-none border-2 border-dashed border-black bg-transparent text-sm text-black dark:border-white dark:text-white"
        style={{ height }}
      >
        <div className="text-center">
          <p className="font-bold uppercase tracking-widest">No data available</p>
          <p className="mt-1 text-[10px] font-bold uppercase tracking-widest text-zinc-500">Data will appear here once available</p>
        </div>
      </motion.div>
    )
  }

  const maxValue = Math.max(...data.map(d => d.value))

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5 }}
      className="h-full w-full"
    >
      {title && (
        <h4 className="mb-4 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
          {title}
        </h4>
      )}

      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 5, right: 30, left: 0, bottom: 5 }}
        >
          <defs>
            {data.map((entry, index) => {
              const color = entry.color || colorScheme[index % colorScheme.length]
              return (
                <linearGradient
                  key={`gradient-${index}`}
                  id={`barGradient-${index}`}
                  x1="0"
                  y1="0"
                  x2="1"
                  y2="0"
                >
                  <stop offset="0%" stopColor={color} stopOpacity={0.8} />
                  <stop offset="100%" stopColor={color} stopOpacity={1} />
                </linearGradient>
              )
            })}
            <filter id="barShadow">
              <feDropShadow dx="0" dy="2" stdDeviation="4" floodOpacity="0.15" />
            </filter>
          </defs>

          {showGrid && (
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#e5e7eb"
              strokeOpacity={0.5}
              horizontal={false}
              className="dark:stroke-gray-700"
            />
          )}

          <XAxis
            type="number"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={valueFormatter}
          />

          <YAxis
            type="category"
            dataKey="name"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 12, fill: '#4b5563', fontWeight: 500 }}
            width={100}
            className="dark:fill-gray-400"
          />

          <Tooltip
            cursor={{ fill: 'rgba(59, 130, 246, 0.05)' }}
            wrapperStyle={{ zIndex: 100 }}
            allowEscapeViewBox={{ x: true, y: true }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const data = payload[0].payload as EnhancedBarDataPoint

              return (
                <motion.div
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="rounded-none border border-black bg-white px-4 py-3 shadow-[4px_4px_0_0_#000] dark:border-white dark:bg-zinc-950 dark:shadow-[4px_4px_0_0_#fff]"
                >
                  <div className="flex items-center gap-2">
                    {data.ticker && (
                      <div className="h-8 w-8 flex-shrink-0 rounded-none border border-black bg-white p-1 dark:border-white dark:bg-black">
                        <CompanyLogo ticker={data.ticker} className="h-full w-full" />
                      </div>
                    )}
                    <div>
                      <p className="font-black tracking-tight text-black dark:text-white">
                        {data.name}
                      </p>
                      {data.ticker && (
                        <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">
                          {data.ticker}
                        </p>
                      )}
                    </div>
                  </div>
                  <p className="mt-2 text-lg font-black tracking-tighter" style={{ color: data.color || colorScheme[0] }}>
                    {valueFormatter(data.value)}
                  </p>
                </motion.div>
              )
            }}
          />

          <Bar
            dataKey="value"
            radius={[0, 0, 0, 0]}
            filter="url(#barShadow)"
            animationDuration={1000}
            animationEasing="ease-out"
          >
            {data.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={`url(#barGradient-${index})`}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </motion.div>
  )
})

export default EnhancedBarChart
