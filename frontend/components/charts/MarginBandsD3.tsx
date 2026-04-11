'use client'

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { useTheme } from 'next-themes'
import * as d3 from 'd3'
import { cn } from '@/lib/utils'
import { useChartSize } from '@/lib/hooks/useChartSize'

interface MarginInput {
  label: string
  value: number
}

interface MarginBandsD3Props {
  margins: MarginInput[]
  priorMargins?: MarginInput[]
}

const normalizePercent = (value: number) => {
  if (Math.abs(value) <= 1.2 && Math.abs(value) > 0.001) {
    return value * 100
  }
  return value
}

const TONE = {
  high: { gradient: ['#34D399', '#10B981'], dot: '#059669', bg: 'rgba(16,185,129,0.08)' },
  mid: { gradient: ['#60A5FA', '#3B82F6'], dot: '#2563EB', bg: 'rgba(59,130,246,0.08)' },
  low: { gradient: ['#FBBF24', '#F59E0B'], dot: '#D97706', bg: 'rgba(245,158,11,0.08)' },
  negative: { gradient: ['#F87171', '#EF4444'], dot: '#DC2626', bg: 'rgba(239,68,68,0.08)' },
}

function getTone(value: number) {
  if (value < 0) return TONE.negative
  if (value >= 15) return TONE.high
  if (value >= 5) return TONE.mid
  return TONE.low
}

export default function MarginBandsD3({ margins, priorMargins = [] }: MarginBandsD3Props) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const { ref: containerRef, width } = useChartSize<HTMLDivElement>()
  const svgRef = useRef<SVGSVGElement | null>(null)
  const gradientId = useId()

  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [pinnedIndex, setPinnedIndex] = useState<number | null>(null)
  const [tooltip, setTooltip] = useState<{ x: number; y: number; index: number } | null>(null)

  const normalizedMargins = useMemo(
    () => margins.map((m) => ({ ...m, value: normalizePercent(m.value) })),
    [margins]
  )

  const priorMap = useMemo(
    () => new Map(priorMargins.map((m) => [m.label, normalizePercent(m.value)])),
    [priorMargins]
  )

  const rowHeight = 72
  const topPad = 12
  const bottomPad = 12
  const chartHeight = topPad + normalizedMargins.length * rowHeight + bottomPad

  const barLeft = 100
  const barRight = 80
  const barAreaW = Math.max(0, width - barLeft - barRight)
  const barThickness = 20
  const barRadius = 10

  const maxScale = useMemo(() => {
    const vals = normalizedMargins.map((m) => Math.abs(m.value))
    const allPriors = Array.from(priorMap.values()).map(Math.abs)
    const maxV = Math.max(20, ...vals, ...allPriors)
    return Math.min(100, Math.ceil(maxV / 10) * 10)
  }, [normalizedMargins, priorMap])

  const xScale = useMemo(
    () => d3.scaleLinear().domain([0, maxScale]).range([0, barAreaW]),
    [maxScale, barAreaW]
  )

  const palette = useMemo(
    () => ({
      text: isDark ? '#E5E7EB' : '#1E293B',
      muted: isDark ? '#6B7280' : '#94A3B8',
      track: isDark ? '#111827' : '#F1F5F9',
      trackBorder: isDark ? '#1F2937' : '#E2E8F0',
      grid: isDark ? 'rgba(148,163,184,0.08)' : 'rgba(148,163,184,0.15)',
    }),
    [isDark]
  )

  const updateTooltipFromEvent = useCallback(
    (event: MouseEvent, index: number) => {
      const c = containerRef.current
      if (!c) return
      const r = c.getBoundingClientRect()
      setTooltip({ x: event.clientX - r.left, y: event.clientY - r.top, index })
    },
    [containerRef]
  )

  const updateTooltipAtRow = useCallback(
    (index: number) => {
      setTooltip({ x: barLeft + barAreaW * 0.5, y: topPad + index * rowHeight, index })
    },
    [barAreaW, rowHeight]
  )

  const handleMouseEnter = useCallback(
    (i: number) => (e: MouseEvent) => {
      if (pinnedIndex !== null && pinnedIndex !== i) return
      setHoveredIndex(i)
      updateTooltipFromEvent(e, i)
    },
    [pinnedIndex, updateTooltipFromEvent]
  )

  const handleMouseMove = useCallback(
    (i: number) => (e: MouseEvent) => {
      if (pinnedIndex !== null && pinnedIndex !== i) return
      updateTooltipFromEvent(e, i)
    },
    [pinnedIndex, updateTooltipFromEvent]
  )

  const handleMouseLeave = useCallback(
    (i: number) => () => {
      if (pinnedIndex === i) return
      setHoveredIndex(null)
      setTooltip(null)
    },
    [pinnedIndex]
  )

  const handleTogglePin = useCallback(
    (i: number) => () => {
      if (pinnedIndex === i) {
        setPinnedIndex(null)
        setTooltip(null)
        return
      }
      setPinnedIndex(i)
      updateTooltipAtRow(i)
    },
    [pinnedIndex, updateTooltipAtRow]
  )

  const handleRowKeyDown = useCallback(
    (i: number) => (e: ReactKeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        handleTogglePin(i)()
      }
    },
    [handleTogglePin]
  )

  useEffect(() => {
    const fn = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        setPinnedIndex(null)
        setTooltip(null)
      }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [])

  useEffect(() => {
    if (!svgRef.current || barAreaW <= 0) return
    const svg = d3.select(svgRef.current)

    svg.selectAll<SVGRectElement, unknown>('.mb-bar').each(function () {
      const targetW = Number(this.getAttribute('data-w') || 0)
      d3.select(this)
        .attr('width', 0)
        .transition()
        .duration(700)
        .ease(d3.easeCubicOut)
        .attr('width', targetW)
    })

    svg.selectAll<SVGLineElement, unknown>('.mb-prior').each(function () {
      const targetX = Number(this.getAttribute('data-x') || 0)
      d3.select(this)
        .attr('x1', barLeft)
        .attr('x2', barLeft)
        .transition()
        .duration(700)
        .delay(200)
        .ease(d3.easeCubicOut)
        .attr('x1', targetX)
        .attr('x2', targetX)
    })
  }, [barAreaW, normalizedMargins])

  if (!normalizedMargins.length) return null

  const activeIndex = pinnedIndex ?? hoveredIndex
  const tooltipItem = activeIndex != null ? normalizedMargins[activeIndex] : null
  const tooltipPrior = tooltipItem ? priorMap.get(tooltipItem.label) : null
  const tooltipDelta =
    tooltipItem && tooltipPrior != null && Math.abs(tooltipPrior) > 0.001
      ? ((tooltipItem.value - tooltipPrior) / Math.abs(tooltipPrior)) * 100
      : null

  return (
    <div className="not-prose rounded-2xl border border-slate-200/80 dark:border-gray-800 bg-gradient-to-br from-white via-slate-50/50 to-white dark:from-gray-950 dark:via-gray-950 dark:to-gray-900 shadow-sm overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-100 dark:border-gray-800/80 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-1 h-4 rounded-full bg-gradient-to-b from-emerald-400 to-emerald-600" />
          <h4 className="text-[13px] font-semibold text-gray-900 dark:text-gray-100">Profitability Margins</h4>
        </div>
        {priorMap.size > 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
            <span className="h-[2px] w-3 rounded bg-gray-400 dark:bg-gray-500 inline-block" />
            Prior period
          </div>
        )}
      </div>

      <div className="relative" ref={containerRef} style={{ height: chartHeight }}>
        <svg ref={svgRef} width="100%" height={chartHeight} role="img" aria-label="Profitability margins chart">
          <defs>
            {normalizedMargins.map((item, i) => {
              const tone = getTone(item.value)
              return (
                <linearGradient key={i} id={`${gradientId}-${i}`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor={tone.gradient[0]} stopOpacity={0.85} />
                  <stop offset="100%" stopColor={tone.gradient[1]} />
                </linearGradient>
              )
            })}
          </defs>

          {[0, maxScale / 2, maxScale].map((tick) => {
            const x = barLeft + xScale(tick)
            return (
              <g key={tick}>
                <line
                  x1={x} x2={x}
                  y1={topPad + 4}
                  y2={chartHeight - bottomPad}
                  stroke={palette.grid}
                  strokeDasharray="2 4"
                />
              </g>
            )
          })}

          {normalizedMargins.map((item, index) => {
            const rowY = topPad + index * rowHeight
            const barY = rowY + (rowHeight - barThickness) / 2
            const clamped = Math.max(0, Math.min(maxScale, Math.abs(item.value)))
            const barW = xScale(clamped)
            const isActive = index === activeIndex
            const priorValue = priorMap.get(item.label)
            const tone = getTone(item.value)

            return (
              <g key={item.label}>
                {isActive && (
                  <rect
                    x={0} y={rowY + 4}
                    width={width} height={rowHeight - 8}
                    rx={12}
                    fill={isDark ? 'rgba(99,102,241,0.05)' : 'rgba(99,102,241,0.03)'}
                  />
                )}

                <text
                  x={barLeft - 12}
                  y={rowY + rowHeight / 2 + 1}
                  textAnchor="end"
                  dominantBaseline="middle"
                  fontSize="12"
                  fontWeight={600}
                  fill={palette.text}
                >
                  {item.label}
                </text>

                <rect
                  x={barLeft}
                  y={barY}
                  width={barAreaW}
                  height={barThickness}
                  rx={barRadius}
                  fill={palette.track}
                  stroke={palette.trackBorder}
                  strokeWidth={0.5}
                />

                <rect
                  className="mb-bar"
                  x={barLeft}
                  y={barY}
                  width={barW}
                  height={barThickness}
                  rx={barRadius}
                  fill={`url(#${gradientId}-${index})`}
                  data-w={barW}
                />

                {priorValue != null && (
                  <line
                    className="mb-prior"
                    x1={barLeft + xScale(Math.max(0, Math.min(maxScale, Math.abs(priorValue))))}
                    x2={barLeft + xScale(Math.max(0, Math.min(maxScale, Math.abs(priorValue))))}
                    y1={barY - 3}
                    y2={barY + barThickness + 3}
                    stroke={isDark ? '#6B7280' : '#94A3B8'}
                    strokeWidth={2}
                    strokeLinecap="round"
                    data-x={barLeft + xScale(Math.max(0, Math.min(maxScale, Math.abs(priorValue))))}
                  />
                )}

                <text
                  x={barLeft + barAreaW + 12}
                  y={rowY + rowHeight / 2 + 1}
                  dominantBaseline="middle"
                  fontSize="13"
                  fontWeight={700}
                  fill={tone.gradient[1]}
                >
                  {item.value.toFixed(1)}%
                </text>

                <rect
                  x={0} y={rowY}
                  width={width} height={rowHeight}
                  fill="transparent"
                  style={{ cursor: 'pointer' }}
                  tabIndex={0}
                  role="button"
                  aria-label={`${item.label} margin ${item.value.toFixed(1)} percent`}
                  onMouseEnter={handleMouseEnter(index)}
                  onMouseMove={handleMouseMove(index)}
                  onMouseLeave={handleMouseLeave(index)}
                  onClick={handleTogglePin(index)}
                  onKeyDown={handleRowKeyDown(index)}
                />
              </g>
            )
          })}
        </svg>

        {tooltip && tooltipItem && (
          <div
            className="pointer-events-none absolute z-30 rounded-xl border border-slate-200/80 dark:border-gray-800 bg-white/97 dark:bg-gray-950/97 px-4 py-3 text-xs shadow-xl backdrop-blur-sm"
            style={{ left: Math.min(tooltip.x, width - 160), top: tooltip.y, transform: 'translate(-50%, -110%)' }}
          >
            <div className="font-semibold text-[12px] text-slate-700 dark:text-gray-200 mb-2">
              {tooltipItem.label} Margin
            </div>
            <div className="flex items-center justify-between gap-6 text-[11px]">
              <span className="text-slate-500 dark:text-gray-400">Current</span>
              <span className="font-bold tabular-nums" style={{ color: getTone(tooltipItem.value).gradient[1] }}>
                {tooltipItem.value.toFixed(1)}%
              </span>
            </div>
            {tooltipPrior != null && (
              <div className="flex items-center justify-between gap-6 text-[11px] mt-0.5">
                <span className="text-slate-500 dark:text-gray-400">Prior</span>
                <span className="font-semibold tabular-nums text-slate-500 dark:text-gray-400">
                  {tooltipPrior.toFixed(1)}%
                </span>
              </div>
            )}
            {tooltipDelta != null && (
              <div
                className={cn(
                  'mt-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold',
                  tooltipDelta >= 0
                    ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                    : 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300'
                )}
              >
                {tooltipDelta >= 0 ? '+' : ''}
                {tooltipDelta.toFixed(1)}%
              </div>
            )}
            {pinnedIndex === tooltip.index && (
              <span className="ml-1.5 text-[10px] font-medium text-slate-400 dark:text-gray-500">
                Pinned
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
