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
        className="flex h-full min-h-[12rem] items-center justify-center rounded-none border-2 border-dashed border-black bg-transparent text-sm text-black dark:border-white dark:text-white"
      >
        <div className="text-center">
          <p className="font-bold uppercase tracking-widest">No analysis data yet</p>
          <p className="mt-1 text-[10px] font-bold uppercase tracking-widest text-zinc-500">Run analyses to see trends here</p>
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
                <div className="rounded-none border border-black bg-white px-3 py-2 shadow-[4px_4px_0_0_#000] dark:border-white dark:bg-zinc-950 dark:shadow-[4px_4px_0_0_#fff]">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">
                    {point.payload.label}
                  </p>
                  <p className="mt-1 text-sm font-black tracking-tighter text-black dark:text-white">
                    {point.value} {(point.value as number) === 1 ? 'analysis' : 'analyses'}
                  </p>
                </div>
              )
            }}
          />
          <Bar
            dataKey="value"
            radius={[0, 0, 0, 0]}
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
