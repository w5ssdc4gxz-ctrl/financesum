'use client'

import { memo, useMemo } from 'react'
import { motion } from 'framer-motion'
import { Tooltip } from 'react-tooltip'
import 'react-tooltip/dist/react-tooltip.css'

export interface HeatmapDataPoint {
  date: string // YYYY-MM-DD
  count: number
}

interface ActivityHeatmapProps {
  data: HeatmapDataPoint[]
  weeks?: number
  colorScheme?: 'blue' | 'green' | 'purple' | 'orange'
  showLabels?: boolean
}

const colorSchemes = {
  blue: {
    empty: '#e5e7eb',
    low: '#dbeafe',
    medium: '#93c5fd',
    high: '#3b82f6',
    highest: '#1e40af',
    darkEmpty: '#374151',
    darkLow: '#1e3a8a',
    darkMedium: '#1e40af',
    darkHigh: '#3b82f6',
    darkHighest: '#60a5fa'
  },
  green: {
    empty: '#e5e7eb',
    low: '#d1fae5',
    medium: '#6ee7b7',
    high: '#10b981',
    highest: '#047857',
    darkEmpty: '#374151',
    darkLow: '#064e3b',
    darkMedium: '#047857',
    darkHigh: '#10b981',
    darkHighest: '#34d399'
  },
  purple: {
    empty: '#e5e7eb',
    low: '#e9d5ff',
    medium: '#c084fc',
    high: '#8b5cf6',
    highest: '#6d28d9',
    darkEmpty: '#374151',
    darkLow: '#581c87',
    darkMedium: '#6d28d9',
    darkHigh: '#8b5cf6',
    darkHighest: '#a78bfa'
  },
  orange: {
    empty: '#e5e7eb',
    low: '#fed7aa',
    medium: '#fdba74',
    high: '#f97316',
    highest: '#c2410c',
    darkEmpty: '#374151',
    darkLow: '#7c2d12',
    darkMedium: '#c2410c',
    darkHigh: '#f97316',
    darkHighest: '#fb923c'
  }
}

const ActivityHeatmap = memo(function ActivityHeatmap({
  data,
  weeks = 12,
  colorScheme = 'blue',
  showLabels = true
}: ActivityHeatmapProps) {
  const colors = colorSchemes[colorScheme]

  const { heatmapData, maxCount } = useMemo(() => {
    // Generate date range for the past N weeks
    const endDate = new Date()
    const startDate = new Date()
    startDate.setDate(endDate.getDate() - weeks * 7)

    // Create a map of dates to counts
    const dateMap = new Map<string, number>()
    data.forEach(d => {
      dateMap.set(d.date, d.count)
    })

    // Generate grid data
    const gridData: { date: Date; dateStr: string; count: number; dayOfWeek: number }[] = []
    let maxCount = 0

    for (let d = new Date(startDate); d <= endDate; d.setDate(d.getDate() + 1)) {
      const dateStr = d.toISOString().split('T')[0]
      const count = dateMap.get(dateStr) || 0
      if (count > maxCount) maxCount = count

      gridData.push({
        date: new Date(d),
        dateStr,
        count,
        dayOfWeek: d.getDay()
      })
    }

    return { heatmapData: gridData, maxCount }
  }, [data, weeks])

  const getColor = (count: number, isDark: boolean = false) => {
    if (count === 0) return isDark ? colors.darkEmpty : colors.empty

    const ratio = count / maxCount
    if (isDark) {
      if (ratio >= 0.75) return colors.darkHighest
      if (ratio >= 0.5) return colors.darkHigh
      if (ratio >= 0.25) return colors.darkMedium
      return colors.darkLow
    } else {
      if (ratio >= 0.75) return colors.highest
      if (ratio >= 0.5) return colors.high
      if (ratio >= 0.25) return colors.medium
      return colors.low
    }
  }

  // Group by weeks for rendering
  const weekGroups = useMemo(() => {
    type DayData = { date: Date; dateStr: string; count: number; dayOfWeek: number }
    const groups: DayData[][] = []
    let currentWeek: DayData[] = []

    heatmapData.forEach(day => {
      if (day.dayOfWeek === 0 && currentWeek.length > 0) {
        groups.push([...currentWeek])
        currentWeek = []
      }
      currentWeek.push(day)
    })

    if (currentWeek.length > 0) {
      groups.push(currentWeek)
    }

    return groups
  }, [heatmapData])

  const dayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

  if (!data || data.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
        className="flex h-40 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-900/50 dark:text-gray-400"
      >
        <div className="text-center">
          <p className="font-medium">No activity data yet</p>
          <p className="mt-1 text-xs text-gray-400">Run analyses to see activity patterns</p>
        </div>
      </motion.div>
    )
  }

  return (
    <div className="w-full overflow-x-auto">
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        className="flex flex-col gap-1"
      >
        {/* Heatmap grid */}
        <div className="flex flex-col gap-2">
          <div className="flex gap-1">
            <div className="grid grid-rows-7 gap-1">
              {dayLabels.map((label, idx) => (
                <div
                  key={label}
                  className="flex h-3 w-6 items-center text-[9px] font-medium text-gray-500 dark:text-gray-400"
                >
                  {idx % 2 === 1 ? label : ''}
                </div>
              ))}
            </div>
            
            <div className="flex gap-1">
              {weekGroups.map((week, weekIdx) => {
                // Calculate padding for first week
                // The first week might not start on Sunday (index 0), so we need to push content down
                const firstDayOfWeek = week[0]?.dayOfWeek ?? 0
                const paddingCount = weekIdx === 0 ? firstDayOfWeek : 0

                return (
                  <div key={weekIdx} className="flex flex-col gap-1">
                    {/* Add padding cells for first week to align with day rows */}
                    {weekIdx === 0 && Array.from({ length: paddingCount }).map((_, i) => (
                      <div
                        key={`padding-${i}`}
                        className="h-3 w-3"
                      />
                    ))}

                    {/* Render actual day cells */}
                    {week.map((day, dayIdx) => (
                      <motion.div
                        key={day.dateStr}
                        initial={{ scale: 0, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        transition={{
                          duration: 0.2,
                          delay: weekIdx * 0.02 + dayIdx * 0.01,
                          ease: [0.4, 0, 0.2, 1]
                        }}
                        whileHover={{ scale: 1.2, zIndex: 10 }}
                        className="group relative h-3 w-3 cursor-pointer rounded-sm transition-all"
                        style={{
                          backgroundColor: getColor(day.count)
                        }}
                        data-tooltip-id="heatmap-tooltip"
                        data-tooltip-date={day.date.toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric'
                        })}
                        data-tooltip-count={day.count}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Legend */}
          <div className="mt-4 flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
            <span>{weeks} weeks ago</span>
            <div className="flex items-center gap-2">
              <span>Less</span>
              <div className="flex gap-1">
                {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => (
                  <div
                    key={idx}
                    className="h-3 w-3 rounded-sm"
                    style={{
                      backgroundColor: getColor(ratio === 0 ? 0 : Math.max(1, Math.ceil(ratio * maxCount)))
                    }}
                  />
                ))}
              </div>
              <span>More</span>
            </div>
          </div>
        </div>
      </motion.div>

      <Tooltip
        id="heatmap-tooltip"
        className="!z-50 !bg-transparent !p-0 !shadow-none !border-0"
        render={({ activeAnchor }) => {
          if (!activeAnchor) return null
          const date = activeAnchor.getAttribute('data-tooltip-date')
          const countStr = activeAnchor.getAttribute('data-tooltip-count')
          const count = countStr ? parseInt(countStr, 10) : 0
          
          return (
            <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-900 shadow-lg dark:border-gray-800 dark:bg-gray-950 dark:text-gray-50">
              <div className="text-center">
                <div className="text-xs text-gray-500 dark:text-gray-400">
                  {date}
                </div>
                <div className="font-semibold">
                  {count} {count === 1 ? 'analysis' : 'analyses'}
                </div>
              </div>
            </div>
          )
        }}
      />
    </div>
  )
})

export default ActivityHeatmap
