'use client'

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent,
} from 'react'
import { useTheme } from 'next-themes'
import * as d3 from 'd3'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/chart-utils'

interface RatioBarListItem {
  label: string
  value: number
}

interface RatioBarListD3Props {
  title: string
  items: RatioBarListItem[]
  unit?: 'percent' | 'ratio' | 'currency' | 'number'
  tone?: 'blue' | 'emerald' | 'amber' | 'violet'
}

const normalizePercent = (value: number) => {
  if (Math.abs(value) <= 1.2 && Math.abs(value) > 0.001) {
    return value * 100
  }
  return value
}

const formatValue = (value: number, unit?: RatioBarListD3Props['unit']) => {
  if (unit === 'percent') return `${value.toFixed(1)}%`
  if (unit === 'currency') return formatCurrency(value)
  if (unit === 'ratio') return value.toFixed(2)
  return value.toFixed(2)
}

export default function RatioBarListD3({
  title,
  items,
  unit = 'number',
  tone = 'blue',
}: RatioBarListD3Props) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const listRef = useRef<HTMLDivElement | null>(null)
  const hasAnimated = useRef(false)

  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [pinnedIndex, setPinnedIndex] = useState<number | null>(null)
  const [tooltip, setTooltip] = useState<{ x: number; y: number; index: number } | null>(null)

  const normalizedItems = useMemo(() => {
    return items
      .filter((item) => item.value != null && Number.isFinite(item.value))
      .map((item) => ({
        ...item,
        value: unit === 'percent' ? normalizePercent(item.value) : item.value,
      }))
  }, [items, unit])

  const maxValue = useMemo(() => {
    const values = normalizedItems.map((item) => Math.abs(item.value))
    return Math.max(1, ...values)
  }, [normalizedItems])

  const palette = useMemo(() => {
    const tones = {
      blue: {
        bar: isDark ? '#60A5FA' : '#2563EB',
        barSoft: isDark ? '#93C5FD' : '#60A5FA',
        dot: isDark ? '#0EA5E9' : '#2563EB',
      },
      emerald: {
        bar: isDark ? '#34D399' : '#10B981',
        barSoft: isDark ? '#6EE7B7' : '#34D399',
        dot: isDark ? '#10B981' : '#059669',
      },
      amber: {
        bar: isDark ? '#FBBF24' : '#F59E0B',
        barSoft: isDark ? '#FCD34D' : '#FBBF24',
        dot: isDark ? '#F59E0B' : '#D97706',
      },
      violet: {
        bar: isDark ? '#A78BFA' : '#7C3AED',
        barSoft: isDark ? '#C4B5FD' : '#A78BFA',
        dot: isDark ? '#8B5CF6' : '#6D28D9',
      },
    }
    return tones[tone]
  }, [isDark, tone])

  const updateTooltipFromEvent = useCallback(
    (event: MouseEvent, index: number) => {
      const container = listRef.current
      if (!container) return
      const rect = container.getBoundingClientRect()
      setTooltip({
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
        index,
      })
    },
    []
  )

  const updateTooltipAtRow = useCallback((index: number) => {
    const containerWidth = listRef.current?.getBoundingClientRect().width ?? 0
    const fallbackX = 220
    const x = containerWidth ? Math.min(containerWidth * 0.65, containerWidth - 40) : fallbackX
    setTooltip({
      x,
      y: 36 + index * 54,
      index,
    })
  }, [])

  const handleMouseEnter = useCallback(
    (index: number) => (event: MouseEvent) => {
      if (pinnedIndex !== null && pinnedIndex !== index) return
      setHoveredIndex(index)
      updateTooltipFromEvent(event, index)
    },
    [pinnedIndex, updateTooltipFromEvent]
  )

  const handleMouseMove = useCallback(
    (index: number) => (event: MouseEvent) => {
      if (pinnedIndex !== null && pinnedIndex !== index) return
      updateTooltipFromEvent(event, index)
    },
    [pinnedIndex, updateTooltipFromEvent]
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
      updateTooltipAtRow(index)
    },
    [pinnedIndex, updateTooltipAtRow]
  )

  const handleRowFocus = useCallback(
    (index: number) => () => {
      if (pinnedIndex !== null && pinnedIndex !== index) return
      setHoveredIndex(index)
      updateTooltipAtRow(index)
    },
    [pinnedIndex, updateTooltipAtRow]
  )

  const handleRowBlur = useCallback(
    (index: number) => () => {
      if (pinnedIndex === index) return
      setHoveredIndex(null)
      setTooltip(null)
    },
    [pinnedIndex]
  )

  const handleRowKeyDown = useCallback(
    (index: number) => (event: ReactKeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        handleTogglePin(index)()
      }
    },
    [handleTogglePin]
  )

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPinnedIndex(null)
        setTooltip(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    if (!listRef.current) return
    const selection = d3.select(listRef.current).selectAll<HTMLDivElement, unknown>('.ratio-bar')

    selection.each(function () {
      const target = this.getAttribute('data-target') || '0%'
      const bar = d3.select(this)
      if (!hasAnimated.current) {
        bar.style('width', '0%')
      }
      bar
        .transition()
        .duration(700)
        .ease(d3.easeCubicOut)
        .style('width', target)
    })

    hasAnimated.current = true
  }, [normalizedItems, palette, maxValue])

  if (!normalizedItems.length) return null

  const activeIndex = pinnedIndex ?? hoveredIndex
  const tooltipItem = activeIndex != null ? normalizedItems[activeIndex] : null

  return (
    <div className="not-prose rounded-xl border border-slate-200/80 dark:border-gray-800 bg-gradient-to-br from-white via-slate-50 to-white dark:from-gray-950 dark:via-gray-950 dark:to-gray-900 shadow-sm p-5 relative">
      <div className="flex items-center gap-2 mb-4">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: palette.dot }} />
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
      </div>

      <div ref={listRef} className="space-y-4">
        {normalizedItems.map((item, index) => {
          const value = item.value
          const widthPct = Math.min(100, (Math.abs(value) / maxValue) * 100)
          const isActive = index === activeIndex

          return (
            <div
              key={item.label}
              className={cn(
                'grid grid-cols-[140px_1fr_80px] items-center gap-3 rounded-xl px-3 py-3',
                isActive ? 'bg-slate-50/80 dark:bg-gray-900/60' : 'hover:bg-slate-50/70 dark:hover:bg-gray-900/50'
              )}
              tabIndex={0}
              role="button"
              aria-label={`${item.label} ${formatValue(value, unit)}`}
              onMouseEnter={handleMouseEnter(index)}
              onMouseMove={handleMouseMove(index)}
              onMouseLeave={handleMouseLeave(index)}
              onClick={handleTogglePin(index)}
              onFocus={handleRowFocus(index)}
              onBlur={handleRowBlur(index)}
              onKeyDown={handleRowKeyDown(index)}
            >
              <span className="text-[11px] font-semibold text-slate-600 dark:text-gray-300">
                {item.label}
              </span>
              <div className="relative h-3 rounded-full bg-slate-100 dark:bg-gray-900 overflow-hidden shadow-inner">
                <div
                  className="ratio-bar h-full w-0 rounded-full"
                  data-target={`${widthPct}%`}
                  style={{
                    background: `linear-gradient(90deg, ${palette.barSoft}, ${palette.bar})`,
                  }}
                />
              </div>
              <span className="text-[10px] font-semibold text-slate-700 dark:text-gray-200 text-right tabular-nums">
                <span className="inline-flex items-center justify-center rounded-full px-2.5 py-1 bg-slate-100 text-slate-600 dark:bg-gray-900 dark:text-gray-200">
                  {formatValue(value, unit)}
                </span>
              </span>
            </div>
          )
        })}
      </div>

      {tooltip && tooltipItem && (
        <div
          className="pointer-events-none absolute z-20 rounded-lg border border-slate-200/80 dark:border-gray-800 bg-white/95 dark:bg-gray-950/95 px-3 py-2 text-xs shadow-lg"
          style={{ left: tooltip.x, top: tooltip.y, transform: 'translate(-20%, -100%)' }}
        >
          <div className="text-[11px] font-semibold text-slate-700 dark:text-gray-200 mb-1">
            {tooltipItem.label}
          </div>
          <div className="text-[11px] text-slate-500 dark:text-gray-400">
            {formatValue(tooltipItem.value, unit)}
          </div>
          {pinnedIndex === tooltip.index && (
            <div className="mt-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold bg-slate-100 text-slate-600 dark:bg-gray-900 dark:text-gray-300">
              Pinned
            </div>
          )}
        </div>
      )}
    </div>
  )
}
