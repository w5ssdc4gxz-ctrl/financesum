'use client'

import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Sector } from 'recharts'
import { cn } from '@/lib/utils'
import { useState, useRef, useEffect } from 'react'
import { motion } from 'framer-motion'
import { CompanyLogo } from '@/components/CompanyLogo'

export type DonutDataPoint = {
  label: string
  value: number
  color: string
  tickers?: string[]
}

interface DonutChartProps {
  data: DonutDataPoint[]
  centerLabel?: string
  centerValue?: string
  className?: string
  valueFormatter?: (value: number) => string
  showAnimation?: boolean
}

const renderActiveShape = (props: any) => {
  const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill } = props

  return (
    <g>
      <Sector
        cx={cx}
        cy={cy}
        innerRadius={innerRadius}
        outerRadius={outerRadius + 6}
        startAngle={startAngle}
        endAngle={endAngle}
        fill={fill}
        style={{ filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.15))' }}
      />
    </g>
  )
}

export function DonutChart({
  data,
  centerLabel,
  centerValue,
  className,
  valueFormatter = (value) => value.toLocaleString(),
  showAnimation = true
}: DonutChartProps) {
  const [activeIndex, setActiveIndex] = useState<number | undefined>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
  const [tooltipPosition, setTooltipPosition] = useState<{ x: number; y: number } | undefined>(undefined)

  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        const { width, height } = containerRef.current.getBoundingClientRect()
        setDimensions({ width, height })
      }
    }

    updateDimensions()
    window.addEventListener('resize', updateDimensions)
    return () => window.removeEventListener('resize', updateDimensions)
  }, [])

  const total = data.reduce((acc, item) => acc + (item.value || 0), 0)

  const handleMouseMove = (e: any) => {
    if (!containerRef.current || !dimensions.width) return

    // Get mouse position relative to container
    const rect = containerRef.current.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const cx = dimensions.width / 2
    const cy = dimensions.height / 2

    // Calculate angle from center to mouse
    const angle = Math.atan2(my - cy, mx - cx)

    // Calculate constrained radius (outer radius + offset)
    // Outer radius is 90% of half the min dimension
    const outerRadius = (Math.min(dimensions.width, dimensions.height) / 2) * 0.9
    const tooltipRadius = outerRadius + 20 // 20px offset

    // Calculate constrained coordinates
    const x = cx + tooltipRadius * Math.cos(angle)
    const y = cy + tooltipRadius * Math.sin(angle)

    setTooltipPosition({ x, y })
  }

  return (
    <div
      ref={containerRef}
      className={cn('relative h-56 w-full', className)}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setTooltipPosition(undefined)}
    >
      <ResponsiveContainer>
        <PieChart>
          <Pie
            data={data}
            innerRadius="65%"
            outerRadius="90%"
            dataKey="value"
            nameKey="label"
            startAngle={90}
            endAngle={-270}
            stroke="white"
            strokeWidth={2}
            activeIndex={activeIndex}
            activeShape={renderActiveShape}
            onMouseEnter={(_, index) => setActiveIndex(index)}
            onMouseLeave={() => setActiveIndex(undefined)}
            animationDuration={showAnimation ? 800 : 0}
            animationEasing="ease-out"
          >
            {data.map((item) => (
              <Cell
                key={item.label}
                fill={item.color}
                className="transition-all duration-200 cursor-pointer"
              />
            ))}
          </Pie>
          <Tooltip
            position={tooltipPosition}
            cursor={{ fill: 'transparent' }}
            wrapperStyle={{ outline: 'none', zIndex: 100, pointerEvents: 'none' }}
            isAnimationActive={false}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const data = payload[0].payload as DonutDataPoint
              const value = data.value

              return (
                <motion.div
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.15 }}
                  className="rounded-lg border border-gray-200 bg-white p-2 shadow-xl dark:border-gray-800 dark:bg-gray-950 min-w-[120px] max-w-[200px] z-50"
                >
                  <div className="flex items-start gap-1.5 mb-1.5">
                    <div
                      className="mt-1 h-2 w-2 flex-shrink-0 rounded-full ring-2 ring-white dark:ring-gray-900"
                      style={{ backgroundColor: data.color }}
                    />
                    <p className="font-bold text-gray-900 dark:text-gray-50 text-xs leading-normal break-words">{data.label}</p>
                  </div>

                  <div className="mb-1.5 text-[10px] text-gray-600 dark:text-gray-400 leading-none pl-3.5">
                    <span className="font-semibold text-gray-900 dark:text-gray-50 text-xs">{valueFormatter(value)}</span>
                    <span className="ml-1 text-gray-400">({Math.round((value / total) * 100)}%)</span>
                  </div>

                  {data.tickers && data.tickers.length > 0 && (
                    <div className="mt-1.5 pt-1.5 border-t border-gray-100 dark:border-gray-800">
                      <motion.div
                        className="flex flex-wrap gap-0.5"
                        initial="hidden"
                        animate="visible"
                        variants={{
                          hidden: { opacity: 0 },
                          visible: {
                            opacity: 1,
                            transition: { staggerChildren: 0.03 }
                          }
                        }}
                      >
                        {data.tickers.slice(0, 12).map((ticker) => (
                          <motion.div
                            key={ticker}
                            className="h-4 w-4 rounded-full bg-white p-[1px] shadow-sm overflow-hidden border border-gray-100 dark:border-gray-800"
                            variants={{
                              hidden: { opacity: 0, scale: 0.5 },
                              visible: { opacity: 1, scale: 1 }
                            }}
                          >
                            <CompanyLogo ticker={ticker} className="h-full w-full" />
                          </motion.div>
                        ))}
                        {data.tickers.length > 12 && (
                          <div className="flex h-4 w-4 items-center justify-center rounded-full bg-gray-100 text-[7px] font-bold text-gray-500 dark:bg-gray-800">
                            +{data.tickers.length - 12}
                          </div>
                        )}
                      </motion.div>
                    </div>
                  )}
                </motion.div>
              )
            }}
          />
        </PieChart>
      </ResponsiveContainer>

      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        {centerLabel && (
          <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
            {centerLabel}
          </p>
        )}
        <p className="text-2xl font-semibold text-gray-900 dark:text-gray-50">
          {centerValue ?? total.toLocaleString()}
        </p>
      </div>
    </div>
  )
}
