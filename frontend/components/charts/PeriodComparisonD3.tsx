'use client'

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from 'react'
import { useTheme } from 'next-themes'
import * as d3 from 'd3'
import { cn } from '@/lib/utils'
import { useChartSize } from '@/lib/hooks/useChartSize'
import { formatCurrency } from '@/lib/chart-utils'

export interface PeriodComparisonDatum {
  label: string
  current: number
  prior: number
  currentRaw?: number | null
  priorRaw?: number | null
}

interface PeriodComparisonD3Props {
  data: PeriodComparisonDatum[]
  currentLabel: string
  priorLabel: string
}

export default function PeriodComparisonD3({
  data,
  currentLabel,
  priorLabel,
}: PeriodComparisonD3Props) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const { ref: containerRef, width } = useChartSize<HTMLDivElement>()
  const svgRef = useRef<SVGSVGElement | null>(null)
  const gradId = useId()

  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [pinnedIndex, setPinnedIndex] = useState<number | null>(null)
  const [tooltip, setTooltip] = useState<{
    x: number
    y: number
    index: number
  } | null>(null)

  const margin = useMemo(() => ({ top: 32, right: 20, bottom: 40, left: 20 }), [])
  const barGroupPad = 0.35
  const barInnerPad = 0.15
  const chartHeight = 240

  const innerW = Math.max(0, width - margin.left - margin.right)
  const innerH = chartHeight - margin.top - margin.bottom

  const labels = useMemo(() => data.map((d) => d.label.replace('Operating Cash Flow', 'Op. Cash Flow').replace('Capital Expenditure', 'CapEx').replace('Gross Profit', 'Gross Profit')), [data])

  const maxVal = useMemo(() => {
    const vals = data.flatMap((d) => [Math.abs(d.current), Math.abs(d.prior)])
    return Math.max(1, ...vals)
  }, [data])

  const x0 = useMemo(
    () => d3.scaleBand().domain(labels).range([0, innerW]).paddingInner(barGroupPad).paddingOuter(0.2),
    [labels, innerW]
  )

  const x1 = useMemo(
    () =>
      d3
        .scaleBand()
        .domain([currentLabel, priorLabel])
        .range([0, x0.bandwidth()])
        .paddingInner(barInnerPad),
    [currentLabel, priorLabel, x0]
  )

  const y = useMemo(
    () =>
      d3
        .scaleLinear()
        .domain([0, maxVal])
        .nice(4)
        .range([innerH, 0]),
    [maxVal, innerH]
  )

  const yTicks = useMemo(() => y.ticks(4), [y])

  const palette = useMemo(
    () => ({
      text: isDark ? '#E5E7EB' : '#1E293B',
      muted: isDark ? '#6B7280' : '#94A3B8',
      grid: isDark ? 'rgba(148,163,184,0.1)' : 'rgba(148,163,184,0.2)',
      current: isDark ? '#818CF8' : '#6366F1',
      currentSoft: isDark ? '#A5B4FC' : '#818CF8',
      prior: isDark ? '#374151' : '#E2E8F0',
      priorHover: isDark ? '#4B5563' : '#CBD5E1',
      positive: isDark ? '#34D399' : '#10B981',
      negative: isDark ? '#F87171' : '#EF4444',
      tooltipBg: isDark ? 'rgba(15,23,42,0.96)' : 'rgba(255,255,255,0.97)',
      tooltipBorder: isDark ? '#1E293B' : '#E2E8F0',
    }),
    [isDark]
  )

  const getDelta = useCallback((item: PeriodComparisonDatum) => {
    const cur = item.currentRaw ?? item.current
    const pri = item.priorRaw ?? item.prior
    if (!pri) return null
    return ((cur - pri) / Math.abs(pri)) * 100
  }, [])

  const fmtCompact = useCallback((v: number) => {
    const abs = Math.abs(v)
    if (abs >= 1e12) return `${(v / 1e12).toFixed(1)}T`
    if (abs >= 1e9) return `${(v / 1e9).toFixed(1)}B`
    if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`
    if (abs >= 1e3) return `${(v / 1e3).toFixed(0)}K`
    return v.toFixed(0)
  }, [])

  const handleMouseEnter = useCallback(
    (index: number) => (event: MouseEvent) => {
      if (pinnedIndex !== null && pinnedIndex !== index) return
      setHoveredIndex(index)
      const container = containerRef.current
      if (!container) return
      const rect = container.getBoundingClientRect()
      setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top, index })
    },
    [pinnedIndex, containerRef]
  )

  const handleMouseMove = useCallback(
    (index: number) => (event: MouseEvent) => {
      if (pinnedIndex !== null && pinnedIndex !== index) return
      const container = containerRef.current
      if (!container) return
      const rect = container.getBoundingClientRect()
      setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top, index })
    },
    [pinnedIndex, containerRef]
  )

  const handleMouseLeave = useCallback(
    (index: number) => () => {
      if (pinnedIndex === index) return
      setHoveredIndex(null)
      setTooltip(null)
    },
    [pinnedIndex]
  )

  const handleTogglePin = useCallback(
    (index: number) => () => {
      if (pinnedIndex === index) {
        setPinnedIndex(null)
        setTooltip(null)
        return
      }
      setPinnedIndex(index)
      const groupX = x0(labels[index]) ?? 0
      setTooltip({
        x: margin.left + groupX + x0.bandwidth() / 2,
        y: margin.top + 20,
        index,
      })
    },
    [pinnedIndex, x0, labels, margin]
  )

  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        setPinnedIndex(null)
        setTooltip(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const hasAnimated = useRef(false)
  useEffect(() => {
    if (!svgRef.current || innerW <= 0) return
    const svg = d3.select(svgRef.current)

    svg.selectAll<SVGRectElement, unknown>('.vert-bar').each(function () {
      const targetH = Number(this.getAttribute('data-h') || 0)
      const targetY = Number(this.getAttribute('data-y') || 0)
      const baseY = margin.top + innerH
      const bar = d3.select(this)
      if (!hasAnimated.current) {
        bar.attr('y', baseY).attr('height', 0)
      }
      bar
        .transition()
        .duration(hasAnimated.current ? 300 : 650)
        .ease(d3.easeCubicOut)
        .attr('y', targetY)
        .attr('height', targetH)
    })

    hasAnimated.current = true
  }, [data, innerW, innerH, margin])

  if (!data.length || innerW <= 0) return null

  const activeIndex = pinnedIndex ?? hoveredIndex
  const activeItem = activeIndex != null ? data[activeIndex] : null

  return (
    <div className="not-prose rounded-2xl border border-slate-200/80 dark:border-gray-800 bg-gradient-to-br from-white via-slate-50/50 to-white dark:from-gray-950 dark:via-gray-950 dark:to-gray-900 shadow-sm overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-100 dark:border-gray-800/80 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-1 h-4 rounded-full" style={{ background: `linear-gradient(to bottom, ${palette.current}, ${palette.currentSoft})` }} />
          <h3 className="text-[13px] font-semibold text-gray-900 dark:text-gray-100">Period Comparison</h3>
        </div>
        <div className="flex items-center gap-4 text-[11px] text-gray-500 dark:text-gray-400">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: palette.current }} />
            {currentLabel}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: palette.prior }} />
            {priorLabel}
          </span>
        </div>
      </div>

      <div className="relative px-2 pt-2 pb-1" ref={containerRef}>
        <svg ref={svgRef} width="100%" height={chartHeight} role="img" aria-label="Period comparison vertical bar chart">
          <defs>
            <linearGradient id={`${gradId}-cur`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={palette.currentSoft} />
              <stop offset="100%" stopColor={palette.current} />
            </linearGradient>
            <filter id={`${gradId}-shadow`}>
              <feDropShadow dx="0" dy="1" stdDeviation="2" floodOpacity="0.08" />
            </filter>
          </defs>

          {yTicks.map((tick) => {
            const ty = margin.top + y(tick)
            return (
              <g key={tick}>
                <line x1={margin.left} x2={margin.left + innerW} y1={ty} y2={ty} stroke={palette.grid} />
                <text x={margin.left - 4} y={ty + 3} textAnchor="end" fontSize="10" fill={palette.muted}>
                  {fmtCompact(tick)}
                </text>
              </g>
            )
          })}

          <line
            x1={margin.left}
            x2={margin.left + innerW}
            y1={margin.top + innerH}
            y2={margin.top + innerH}
            stroke={palette.grid}
          />

          {data.map((item, i) => {
            const groupX = x0(labels[i]) ?? 0
            const curBarX = margin.left + groupX + (x1(currentLabel) ?? 0)
            const priBarX = margin.left + groupX + (x1(priorLabel) ?? 0)
            const barW = x1.bandwidth()
            const curH = innerH - y(Math.abs(item.current))
            const priH = innerH - y(Math.abs(item.prior))
            const curY = margin.top + y(Math.abs(item.current))
            const priY = margin.top + y(Math.abs(item.prior))
            const isActive = i === activeIndex
            const delta = getDelta(item)
            const radius = Math.min(4, barW / 3)

            return (
              <g key={item.label}>
                {isActive && (
                  <rect
                    x={margin.left + groupX - 4}
                    y={margin.top}
                    width={x0.bandwidth() + 8}
                    height={innerH}
                    rx={8}
                    fill={isDark ? 'rgba(99,102,241,0.06)' : 'rgba(99,102,241,0.04)'}
                  />
                )}

                <rect
                  className="vert-bar"
                  x={curBarX}
                  y={curY}
                  width={barW}
                  height={Math.max(0, curH)}
                  rx={radius}
                  fill={`url(#${gradId}-cur)`}
                  filter={isActive ? `url(#${gradId}-shadow)` : undefined}
                  data-h={Math.max(0, curH)}
                  data-y={curY}
                />
                <rect
                  className="vert-bar"
                  x={priBarX}
                  y={priY}
                  width={barW}
                  height={Math.max(0, priH)}
                  rx={radius}
                  fill={isActive ? palette.priorHover : palette.prior}
                  data-h={Math.max(0, priH)}
                  data-y={priY}
                />

                {curH > 18 && (
                  <text
                    x={curBarX + barW / 2}
                    y={curY + 14}
                    textAnchor="middle"
                    fontSize="9"
                    fontWeight={600}
                    fill="white"
                  >
                    {fmtCompact(item.currentRaw ?? item.current)}
                  </text>
                )}

                <text
                  x={margin.left + groupX + x0.bandwidth() / 2}
                  y={margin.top + innerH + 16}
                  textAnchor="middle"
                  fontSize="11"
                  fontWeight={500}
                  fill={palette.text}
                >
                  {labels[i]}
                </text>

                {delta != null && (
                  <text
                    x={margin.left + groupX + x0.bandwidth() / 2}
                    y={margin.top + innerH + 30}
                    textAnchor="middle"
                    fontSize="10"
                    fontWeight={600}
                    fill={delta >= 0 ? palette.positive : palette.negative}
                  >
                    {delta >= 0 ? '+' : ''}{delta.toFixed(1)}%
                  </text>
                )}

                <rect
                  x={margin.left + groupX}
                  y={margin.top}
                  width={x0.bandwidth()}
                  height={innerH + 36}
                  fill="transparent"
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={handleMouseEnter(i)}
                  onMouseMove={handleMouseMove(i)}
                  onMouseLeave={handleMouseLeave(i)}
                  onClick={handleTogglePin(i)}
                />
              </g>
            )
          })}
        </svg>

        {tooltip && activeItem && (
          <div
            className="pointer-events-none absolute z-30 rounded-xl border px-4 py-3 text-xs shadow-xl backdrop-blur-sm"
            style={{
              left: Math.min(tooltip.x, width - 180),
              top: tooltip.y,
              transform: 'translate(-50%, -110%)',
              background: palette.tooltipBg,
              borderColor: palette.tooltipBorder,
            }}
          >
            <div className="font-semibold text-[12px] mb-2" style={{ color: palette.text }}>
              {activeItem.label}
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between gap-6">
                <span className="flex items-center gap-1.5" style={{ color: palette.muted }}>
                  <span className="h-2 w-2 rounded-sm" style={{ background: palette.current }} />
                  {currentLabel}
                </span>
                <span className="font-semibold tabular-nums" style={{ color: palette.text }}>
                  {formatCurrency(activeItem.currentRaw ?? activeItem.current)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-6">
                <span className="flex items-center gap-1.5" style={{ color: palette.muted }}>
                  <span className="h-2 w-2 rounded-sm" style={{ background: palette.prior }} />
                  {priorLabel}
                </span>
                <span className="font-semibold tabular-nums" style={{ color: palette.muted }}>
                  {formatCurrency(activeItem.priorRaw ?? activeItem.prior)}
                </span>
              </div>
            </div>
            {(() => {
              const d = getDelta(activeItem)
              if (d == null) return null
              return (
                <div
                  className={cn(
                    'mt-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold',
                    d >= 0
                      ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                      : 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300'
                  )}
                >
                  {d >= 0 ? '+' : ''}{d.toFixed(1)}%
                </div>
              )
            })()}
            {pinnedIndex === tooltip.index && (
              <span className="ml-1.5 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-slate-100 text-slate-500 dark:bg-gray-800 dark:text-gray-400">
                Pinned
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
