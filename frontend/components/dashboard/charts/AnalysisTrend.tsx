'use client'

import { memo } from 'react'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, Cell } from 'recharts'
import { motion } from 'framer-motion'

type TrendPoint = {
  label: string
  value: number
}

interface AnalysisTrendProps {
  data: TrendPoint[]
  color?: string
}

const AnalysisTrend = memo(function AnalysisTrend({
  data,
  color = '#10b981', // emerald-500 to match existing app palette
}: AnalysisTrendProps) {
  if (!data.length) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
        className="flex h-full min-h-[12rem] items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-900/50 dark:text-gray-400"
      >
        <div className="text-center">
          <p className="font-medium">No analysis data yet</p>
          <p className="mt-1 text-xs text-gray-400">Run analyses to see trends here</p>
        </div>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5, ease: [0.4, 0, 0.2, 1] }}
      className="h-full w-full"
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 10, right: 0, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            dy={10}
            interval={0}
          />
          <Tooltip
            cursor={{ fill: 'transparent' }}
            wrapperStyle={{ zIndex: 100 }}
            allowEscapeViewBox={{ x: true, y: true }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const point = payload[0]
              return (
                <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-lg dark:border-gray-800 dark:bg-gray-950">
                  <p className="text-xs font-medium text-gray-600 dark:text-gray-400">
                    {point.payload.label}
                  </p>
                  <p className="mt-1 text-sm font-semibold text-gray-900 dark:text-gray-50">
                    {point.value} {(point.value as number) === 1 ? 'analysis' : 'analyses'}
                  </p>
                </div>
              )
            }}
          />
          <Bar
            dataKey="value"
            radius={[4, 4, 0, 0]}
            maxBarSize={40}
          >
             {data.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </motion.div>
  )
})

export default AnalysisTrend
