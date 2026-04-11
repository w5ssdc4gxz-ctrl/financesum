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
import { motion } from 'framer-motion'
import * as d3 from 'd3'
import { cn } from '@/lib/utils'
import { useChartSize } from '@/lib/hooks/useChartSize'
import { formatCurrency } from '@/lib/chart-utils'
import type { CashFlowBridgeStage } from '@/lib/derived-metrics'

// ────────────────────────────────────────────────────────────────
// Props
// ────────────────────────────────────────────────────────────────

interface CashFlowBridgeD3Props {
  stages: CashFlowBridgeStage[]
  currentLabel: string
  priorLabel?: string
}

// ────────────────────────────────────────────────────────────────
// Color palette
// ────────────────────────────────────────────────────────────────

const STAGE_COLORS = {
  positive: {
    gradient: ['#60A5FA', '#3B82F6'],
    light: '#EFF6FF',
    stroke: '#3B82F6',
    text: '#2563EB',
    textDark: '#93C5FD',
    connector: '#93C5FD',
  },
  negative: {
    gradient: ['#FCA5A5', '#EF4444'],
    light: '#FEF2F2',
    stroke: '#EF4444',
    text: '#DC2626',
    textDark: '#FCA5A5',
    connector: '#FCA5A5',
  },
  result: {
    gradient: ['#34D399', '#10B981'],
    light: '#ECFDF5',
    stroke: '#10B981',
    text: '#059669',
    textDark: '#6EE7B7',
    connector: '#6EE7B7',
  },
}

// ────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────

function fmtCompact(v: number): string {
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(1)}T`
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`
  return `${sign}$${abs.toFixed(0)}`
}

function calcChange(current: number, prior: number): string | null {
  if (prior === 0) return null
  const pct = ((current - prior) / Math.abs(prior)) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

// ────────────────────────────────────────────────────────────────
// Main Component
// ────────────────────────────────────────────────────────────────

export default function CashFlowBridgeD3({
  stages,
  currentLabel,
  priorLabel,
}: CashFlowBridgeD3Props) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const { ref: containerRef, width } = useChartSize<HTMLDivElement>()
  const svgRef = useRef<SVGSVGElement | null>(null)
  const gradId = useId()

  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [tooltip, setTooltip] = useState<{ x: number; y: number; index: number } | null>(null)

  // Layout calculations (stable ref to avoid re-renders)
  const margin = useMemo(() => ({ top: 40, right: 16, bottom: 40, left: 16 }), [])
  const chartHeight = 300
  const innerW = Math.max(0, width - margin.left - margin.right)
  const innerH = chartHeight - margin.top - margin.bottom

  const revenue = stages.length > 0 ? Math.abs(stages[0].value) : 1

  // Scales
  const maxVal = useMemo(() => {
    const vals = stages.map((s) => Math.abs(s.value))
    return Math.max(1, ...vals)
  }, [stages])

  const xScale = useMemo(
    () =>
      d3
        .scaleBand<number>()
        .domain(stages.map((_, i) => i))
        .range([0, innerW])
        .paddingInner(0.25)
        .paddingOuter(0.1),
    [stages, innerW]
  )

  const yScale = useMemo(
    () =>
      d3
        .scaleLinear()
        .domain([0, maxVal * 1.12])
        .range([innerH, 0]),
    [maxVal, innerH]
  )

  // Palette
  const palette = useMemo(
    () => ({
      text: isDark ? '#E5E7EB' : '#1E293B',
      muted: isDark ? '#6B7280' : '#94A3B8',
      grid: isDark ? 'rgba(148,163,184,0.08)' : 'rgba(148,163,184,0.12)',
      connector: isDark ? 'rgba(148,163,184,0.25)' : 'rgba(148,163,184,0.3)',
      tooltipBg: isDark ? 'rgba(15,23,42,0.96)' : 'rgba(255,255,255,0.97)',
      tooltipBorder: isDark ? '#1E293B' : '#E2E8F0',
      resultGlow: isDark ? 'rgba(16,185,129,0.15)' : 'rgba(16,185,129,0.08)',
    }),
    [isDark]
  )

  // Connector paths between bars
  const connectorPaths = useMemo(() => {
    if (stages.length < 2 || innerW <= 0) return []
    const paths: { d: string; color: string; index: number }[] = []

    for (let i = 0; i < stages.length - 1; i++) {
      const currentStage = stages[i]
      const nextStage = stages[i + 1]

      const barW = xScale.bandwidth()
      const fromX = (xScale(i) ?? 0) + barW
      const toX = xScale(i + 1) ?? 0

      // Horizontal connector at the level of the current bar's top
      const connY = yScale(Math.abs(currentStage.cumulative))
      const midX = fromX + (toX - fromX) / 2

      // Curved connector path
      const d = `M ${margin.left + fromX} ${margin.top + connY}
                 C ${margin.left + midX} ${margin.top + connY},
                   ${margin.left + midX} ${margin.top + connY},
                   ${margin.left + toX} ${margin.top + connY}`

      const colors = STAGE_COLORS[nextStage.type]
      paths.push({ d, color: isDark ? colors.connector : colors.stroke, index: i })
    }
    return paths
  }, [stages, xScale, yScale, innerW, margin, isDark])

  // Mouse handlers
  const handleMouseEnter = useCallback(
    (index: number) => (event: MouseEvent) => {
      setHoveredIndex(index)
      const container = containerRef.current
      if (!container) return
      const rect = container.getBoundingClientRect()
      setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top, index })
    },
    [containerRef]
  )

  const handleMouseMove = useCallback(
    (index: number) => (event: MouseEvent) => {
      const container = containerRef.current
      if (!container) return
      const rect = container.getBoundingClientRect()
      setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top, index })
    },
    [containerRef]
  )

  const handleMouseLeave = useCallback(() => {
    setHoveredIndex(null)
    setTooltip(null)
  }, [])

  // Animate bars on mount (only once)
  const hasAnimated = useRef(false)
  useEffect(() => {
    if (!svgRef.current || innerW <= 0) return
    const svg = d3.select(svgRef.current)

    svg.selectAll<SVGRectElement, unknown>('.bridge-bar').each(function (_, i) {
      const targetH = Number(this.getAttribute('data-h') || 0)
      const targetY = Number(this.getAttribute('data-y') || 0)
      const baseY = margin.top + innerH
      const bar = d3.select(this)
      if (!hasAnimated.current) {
        bar.attr('y', baseY).attr('height', 0).attr('opacity', 0)
      }
      bar
        .transition()
        .duration(600)
        .delay(hasAnimated.current ? 0 : i * 100)
        .ease(d3.easeCubicOut)
        .attr('y', targetY)
        .attr('height', targetH)
        .attr('opacity', 1)
    })

    // Animate connectors
    svg.selectAll<SVGPathElement, unknown>('.bridge-connector').each(function (_, i) {
      const totalLength = this.getTotalLength?.() || 100
      const conn = d3.select(this)
      if (!hasAnimated.current) {
        conn.attr('stroke-dasharray', totalLength).attr('stroke-dashoffset', totalLength)
      }
      conn
        .transition()
        .duration(400)
        .delay(hasAnimated.current ? 0 : i * 100 + 300)
        .ease(d3.easeCubicOut)
        .attr('stroke-dashoffset', 0)
    })

    // Animate value labels
    svg.selectAll<SVGTextElement, unknown>('.bridge-value').each(function (_, i) {
      const label = d3.select(this)
      if (!hasAnimated.current) {
        label.attr('opacity', 0)
      }
      label
        .transition()
        .duration(400)
        .delay(hasAnimated.current ? 0 : i * 100 + 200)
        .ease(d3.easeCubicOut)
        .attr('opacity', 1)
    })

    hasAnimated.current = true
  }, [stages, innerW, innerH, margin])

  if (!stages.length || innerW <= 0) return null

  const tooltipStage = hoveredIndex != null ? stages[hoveredIndex] : null

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
      className="not-prose rounded-2xl border border-slate-200/80 dark:border-gray-800 bg-gradient-to-br from-white via-slate-50/30 to-white dark:from-gray-950 dark:via-gray-950 dark:to-gray-900 shadow-sm overflow-hidden"
    >
      {/* Header */}
      <div className="px-5 py-3.5 border-b border-slate-100 dark:border-gray-800/80 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-1 h-4 rounded-full bg-gradient-to-b from-blue-400 to-emerald-500" />
          <h3 className="text-[13px] font-semibold text-gray-900 dark:text-gray-100">
            Cash Flow Bridge
          </h3>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-gray-400 dark:text-gray-500">
          {currentLabel && (
            <span>{currentLabel}</span>
          )}
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm" style={{ background: STAGE_COLORS.positive.gradient[1] }} />
              Inflow
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm" style={{ background: STAGE_COLORS.negative.gradient[1] }} />
              Outflow
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-sm" style={{ background: STAGE_COLORS.result.gradient[1] }} />
              Result
            </span>
          </div>
        </div>
      </div>

      {/* Chart */}
      <div className="relative px-2 pt-2 pb-1" ref={containerRef}>
        <svg
          ref={svgRef}
          width="100%"
          height={chartHeight}
          role="img"
          aria-label="Cash flow bridge from revenue to free cash flow"
        >
          <defs>
            {stages.map((stage, i) => {
              const colors = STAGE_COLORS[stage.type]
              return (
                <linearGradient key={`grad-${i}`} id={`${gradId}-${i}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors.gradient[0]} stopOpacity={0.9} />
                  <stop offset="100%" stopColor={colors.gradient[1]} />
                </linearGradient>
              )
            })}
            <filter id={`${gradId}-glow`}>
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id={`${gradId}-shadow`}>
              <feDropShadow dx="0" dy="2" stdDeviation="3" floodOpacity="0.1" />
            </filter>
          </defs>

          {/* Subtle horizontal grid */}
          {yScale.ticks(4).map((tick) => (
            <line
              key={tick}
              x1={margin.left}
              x2={margin.left + innerW}
              y1={margin.top + yScale(tick)}
              y2={margin.top + yScale(tick)}
              stroke={palette.grid}
              strokeDasharray="3 6"
            />
          ))}

          {/* Connector lines between bars */}
          {connectorPaths.map((conn, i) => (
            <path
              key={`conn-${i}`}
              className="bridge-connector"
              d={conn.d}
              fill="none"
              stroke={palette.connector}
              strokeWidth={1.5}
              strokeDasharray="4 3"
              opacity={0.6}
            />
          ))}

          {/* Bars */}
          {stages.map((stage, i) => {
            const barW = xScale.bandwidth()
            const barX = margin.left + (xScale(i) ?? 0)
            const absVal = Math.abs(stage.value)
            const barH = Math.max(2, innerH - yScale(absVal))
            const barY = margin.top + yScale(absVal)
            const isActive = i === hoveredIndex
            const colors = STAGE_COLORS[stage.type]
            const isResult = stage.type === 'result'
            const radius = Math.min(6, barW / 4)

            // For negative bars, position them differently (waterfall effect)
            let finalBarY = barY
            let finalBarH = barH
            if (stage.type === 'negative' && i > 0) {
              // Negative bar should hang from the previous cumulative level
              const prevCumulative = stages[i - 1].cumulative
              const topY = margin.top + yScale(Math.abs(prevCumulative))
              finalBarY = topY
              finalBarH = Math.max(2, (margin.top + yScale(Math.abs(stage.cumulative))) - topY)
            }

            return (
              <g key={`stage-${i}`}>
                {/* Hover highlight */}
                {isActive && (
                  <rect
                    x={barX - 4}
                    y={margin.top - 4}
                    width={barW + 8}
                    height={innerH + 8}
                    rx={8}
                    fill={isDark ? 'rgba(99,102,241,0.04)' : 'rgba(99,102,241,0.03)'}
                  />
                )}

                {/* Result glow */}
                {isResult && (
                  <rect
                    x={barX - 2}
                    y={finalBarY - 2}
                    width={barW + 4}
                    height={finalBarH + 4}
                    rx={radius + 2}
                    fill={palette.resultGlow}
                    filter={`url(#${gradId}-glow)`}
                  />
                )}

                {/* Main bar */}
                <rect
                  className="bridge-bar"
                  x={barX}
                  y={finalBarY}
                  width={barW}
                  height={Math.max(0, finalBarH)}
                  rx={radius}
                  fill={`url(#${gradId}-${i})`}
                  filter={isActive ? `url(#${gradId}-shadow)` : undefined}
                  data-h={Math.max(0, finalBarH)}
                  data-y={finalBarY}
                  style={{ cursor: 'pointer' }}
                />

                {/* Value label above bar */}
                <text
                  className="bridge-value"
                  x={barX + barW / 2}
                  y={finalBarY - 8}
                  textAnchor="middle"
                  fontSize={isResult ? '12' : '10'}
                  fontWeight={isResult ? 700 : 600}
                  fill={isDark ? colors.textDark : colors.text}
                >
                  {stage.type === 'negative' ? '-' : ''}{fmtCompact(Math.abs(stage.value))}
                </text>

                {/* Label below */}
                <text
                  x={barX + barW / 2}
                  y={margin.top + innerH + 16}
                  textAnchor="middle"
                  fontSize="10"
                  fontWeight={isResult ? 600 : 500}
                  fill={isResult ? (isDark ? colors.textDark : colors.text) : palette.text}
                >
                  {stage.label}
                </text>

                {/* Change badge below label for result */}
                {isResult && stage.priorValue != null && Math.abs(stage.priorValue) > 0 && (
                  <text
                    x={barX + barW / 2}
                    y={margin.top + innerH + 30}
                    textAnchor="middle"
                    fontSize="9"
                    fontWeight={600}
                    fill={
                      stage.value >= (stage.priorValue ?? 0)
                        ? (isDark ? '#34D399' : '#10B981')
                        : (isDark ? '#F87171' : '#EF4444')
                    }
                  >
                    {calcChange(stage.value, stage.priorValue)}
                  </text>
                )}

                {/* Hit area */}
                <rect
                  x={barX}
                  y={margin.top}
                  width={barW}
                  height={innerH + 30}
                  fill="transparent"
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={handleMouseEnter(i)}
                  onMouseMove={handleMouseMove(i)}
                  onMouseLeave={handleMouseLeave}
                />
              </g>
            )
          })}
        </svg>

        {/* Tooltip */}
        {tooltip && tooltipStage && (
          <div
            className="pointer-events-none absolute z-30 rounded-xl border px-4 py-3 text-xs shadow-xl backdrop-blur-sm"
            style={{
              left: Math.min(Math.max(tooltip.x, 100), width - 180),
              top: Math.max(tooltip.y - 10, 10),
              transform: 'translate(-50%, -110%)',
              background: palette.tooltipBg,
              borderColor: palette.tooltipBorder,
              minWidth: 180,
            }}
          >
            <div className="font-semibold text-[12px] mb-2" style={{ color: palette.text }}>
              {tooltipStage.label}
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-4">
                <span style={{ color: palette.muted }}>{currentLabel}</span>
                <span className="font-semibold tabular-nums" style={{ color: palette.text }}>
                  {formatCurrency(tooltipStage.value)}
                </span>
              </div>
              {tooltipStage.priorValue != null && (
                <div className="flex items-center justify-between gap-4">
                  <span style={{ color: palette.muted }}>{priorLabel || 'Prior'}</span>
                  <span className="font-semibold tabular-nums" style={{ color: palette.muted }}>
                    {formatCurrency(tooltipStage.priorValue)}
                  </span>
                </div>
              )}
              {revenue > 0 && tooltipStage.type !== 'negative' && (
                <div className="flex items-center justify-between gap-4 pt-1 border-t border-slate-100 dark:border-gray-800">
                  <span style={{ color: palette.muted }}>% of Rev.</span>
                  <span className="font-medium tabular-nums" style={{ color: palette.muted }}>
                    {((Math.abs(tooltipStage.value) / revenue) * 100).toFixed(1)}%
                  </span>
                </div>
              )}
              {tooltipStage.type === 'negative' && revenue > 0 && (
                <div className="flex items-center justify-between gap-4 pt-1 border-t border-slate-100 dark:border-gray-800">
                  <span style={{ color: palette.muted }}>% of Rev.</span>
                  <span className="font-medium tabular-nums" style={{ color: palette.muted }}>
                    {((Math.abs(tooltipStage.value) / revenue) * 100).toFixed(1)}%
                  </span>
                </div>
              )}
              {tooltipStage.priorValue != null && Math.abs(tooltipStage.priorValue) > 0 && (() => {
                const change = calcChange(tooltipStage.value, tooltipStage.priorValue)
                if (!change) return null
                const isPositive = tooltipStage.value >= tooltipStage.priorValue
                return (
                  <div className="pt-1">
                    <span
                      className={cn(
                        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold',
                        isPositive
                          ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300'
                          : 'bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-300'
                      )}
                    >
                      {change}
                    </span>
                  </div>
                )
              })()}
            </div>
          </div>
        )}
      </div>
    </motion.div>
  )
}
