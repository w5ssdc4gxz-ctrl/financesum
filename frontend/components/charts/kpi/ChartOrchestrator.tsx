'use client'

import React, { useMemo } from 'react'
import { BarKPIChart, MetricCard, type KPIData } from './BarKPIChart'
import { RadialGaugeChart } from './RadialGaugeChart'
import { TrendKPIChart } from './TrendKPIChart'
import { DonutKPIChart } from './DonutKPIChart'
import { 
  resolveChartType, 
  normalizePercentageValue,
  type ChartType,
} from '@/lib/chart-utils'

export { type KPIData } from './BarKPIChart'

interface ChartOrchestratorProps {
  kpi: KPIData
  currentLabel: string
  priorLabel: string
  forceChartType?: ChartType
  use3D?: boolean
}

/**
 * ChartOrchestrator - Smart chart type picker
 * 
 * Automatically selects the best visualization based on:
 * - KPI data structure (segments, history, prior_value)
 * - KPI name patterns (margin, rate, growth, etc.)
 * - Unit type (percentage, currency, etc.)
 * - Explicit chart_type from backend
 */
export function ChartOrchestrator({
  kpi,
  currentLabel,
  priorLabel,
  forceChartType,
  use3D = true,
}: ChartOrchestratorProps) {
  // Normalize the KPI value for proper display
  const normalizedKpi = useMemo(() => ({
    ...kpi,
    value: normalizePercentageValue(kpi.value, kpi.unit),
    prior_value: kpi.prior_value != null 
      ? normalizePercentageValue(kpi.prior_value, kpi.unit)
      : null,
  }), [kpi])

  // Determine the best chart type
  const chartType = forceChartType || resolveChartType(normalizedKpi)

  switch (chartType) {
    case 'donut':
      // Verify we have segments
      if (kpi.segments && kpi.segments.length >= 2) {
        return (
          <DonutKPIChart
            kpi={normalizedKpi}
            currentLabel={currentLabel}
          />
        )
      }
      // Fallback to bar if no segments
      return (
        <BarKPIChart
          kpi={normalizedKpi}
          currentLabel={currentLabel}
          priorLabel={priorLabel}
          use3D={use3D}
        />
      )

    case 'trend':
      // Verify we have history
      if (kpi.history && kpi.history.length >= 2) {
        return (
          <TrendKPIChart
            kpi={normalizedKpi}
            currentLabel={currentLabel}
          />
        )
      }
      // Fallback to bar if no history
      return (
        <BarKPIChart
          kpi={normalizedKpi}
          currentLabel={currentLabel}
          priorLabel={priorLabel}
          use3D={use3D}
        />
      )

    case 'gauge':
      // Gauge works best for 0-100 percentage values
      const value = normalizedKpi.value
      if (kpi.unit === '%' && value >= 0 && value <= 100) {
        return (
          <RadialGaugeChart
            kpi={normalizedKpi}
            currentLabel={currentLabel}
            priorLabel={priorLabel}
          />
        )
      }
      // Fallback to bar for non-percentage or out-of-range values
      return (
        <BarKPIChart
          kpi={normalizedKpi}
          currentLabel={currentLabel}
          priorLabel={priorLabel}
          use3D={use3D}
        />
      )

    case 'metric':
      return (
        <MetricCard
          kpi={normalizedKpi}
          currentLabel={currentLabel}
        />
      )

    case 'waterfall':
      // TODO: Implement waterfall chart
      // For now, fallback to bar
      return (
        <BarKPIChart
          kpi={normalizedKpi}
          currentLabel={currentLabel}
          priorLabel={priorLabel}
          use3D={use3D}
        />
      )

    case 'bar':
    default:
      return (
        <BarKPIChart
          kpi={normalizedKpi}
          currentLabel={currentLabel}
          priorLabel={priorLabel}
          use3D={use3D}
        />
      )
  }
}

/**
 * Batch render multiple KPIs with appropriate chart types
 */
export function KPIChartGrid({
  kpis,
  currentLabel,
  priorLabel,
  columns = 2,
  use3D = true,
}: {
  kpis: KPIData[]
  currentLabel: string
  priorLabel: string
  columns?: 1 | 2 | 3
  use3D?: boolean
}) {
  const gridCols = {
    1: 'grid-cols-1',
    2: 'grid-cols-1 md:grid-cols-2',
    3: 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
  }

  return (
    <div className={`grid gap-4 ${gridCols[columns]}`}>
      {kpis.map((kpi, idx) => (
        <div
          key={`${kpi.name}-${idx}`}
          className={kpis.length > 2 && idx === 0 ? 'lg:col-span-2' : ''}
        >
          <ChartOrchestrator
            kpi={kpi}
            currentLabel={currentLabel}
            priorLabel={priorLabel}
            use3D={use3D}
          />
        </div>
      ))}
    </div>
  )
}

export default ChartOrchestrator
