/**
 * Derived Financial Metrics Calculator
 *
 * Calculates additional financial metrics from existing chart_data fields
 * on the frontend without requiring backend changes.
 */

import type { ChartData } from '@/components/EnhancedSummary'

interface PeriodData {
  revenue?: number | null
  operating_income?: number | null
  net_income?: number | null
  free_cash_flow?: number | null
  operating_margin?: number | null
  net_margin?: number | null
  gross_margin?: number | null
  gross_profit?: number | null
  ebitda?: number | null
  operating_cash_flow?: number | null
  capex?: number | null
  total_debt?: number | null
  cash_and_equivalents?: number | null
  total_assets?: number | null
  total_equity?: number | null
  eps?: number | null
  eps_diluted?: number | null
  rnd_expense?: number | null
  sga_expense?: number | null
  roe?: number | null
  roa?: number | null
  roic?: number | null
  debt_to_equity?: number | null
  current_ratio?: number | null
  ebitda_margin?: number | null
  fcf_margin?: number | null
  // Derived fields
  net_debt?: number | null
  revenue_growth?: number | null
  [key: string]: number | null | undefined
}

/**
 * Calculate derived metrics for a single period, filling in gaps.
 */
function enrichPeriod(period: PeriodData, priorPeriod?: PeriodData | null): PeriodData {
  const enriched = { ...period }

  // FCF = Operating Cash Flow - |CapEx| (if FCF not already provided)
  if (enriched.free_cash_flow == null && enriched.operating_cash_flow != null && enriched.capex != null) {
    enriched.free_cash_flow = enriched.operating_cash_flow - Math.abs(enriched.capex)
  }

  // FCF Margin = FCF / Revenue * 100
  if (enriched.fcf_margin == null && enriched.free_cash_flow != null && enriched.revenue != null && enriched.revenue !== 0) {
    const margin = (enriched.free_cash_flow / enriched.revenue) * 100
    enriched.fcf_margin = Math.abs(margin) <= 1.5 ? margin * 100 : margin
  }

  // Net Debt = Total Debt - Cash & Equivalents
  if (enriched.net_debt == null && enriched.total_debt != null && enriched.cash_and_equivalents != null) {
    enriched.net_debt = enriched.total_debt - enriched.cash_and_equivalents
  }

  // EBITDA approximation (if not provided, use operating income as floor)
  // Note: This is a rough approximation without D&A data
  if (enriched.ebitda == null && enriched.operating_income != null) {
    enriched.ebitda = enriched.operating_income
  }

  // EBITDA Margin = EBITDA / Revenue * 100
  if (enriched.ebitda_margin == null && enriched.ebitda != null && enriched.revenue != null && enriched.revenue !== 0) {
    const margin = (enriched.ebitda / enriched.revenue) * 100
    enriched.ebitda_margin = Math.abs(margin) <= 1.5 ? margin * 100 : margin
  }

  // Revenue Growth YoY (if prior period available)
  if (enriched.revenue_growth == null && priorPeriod?.revenue != null && enriched.revenue != null && priorPeriod.revenue !== 0) {
    enriched.revenue_growth = ((enriched.revenue - priorPeriod.revenue) / Math.abs(priorPeriod.revenue)) * 100
  }

  // Gross Profit = Revenue * Gross Margin (if gross profit missing but margin available)
  if (enriched.gross_profit == null && enriched.revenue != null && enriched.gross_margin != null) {
    const marginPct = Math.abs(enriched.gross_margin) <= 1.5 ? enriched.gross_margin : enriched.gross_margin / 100
    enriched.gross_profit = enriched.revenue * marginPct
  }

  return enriched
}

/**
 * Enrich ChartData with all derivable metrics.
 * Returns a new ChartData object with filled-in fields.
 */
export function enrichChartData(chartData: ChartData): ChartData {
  const enrichedCurrent = enrichPeriod(chartData.current_period, chartData.prior_period)
  const enrichedPrior = chartData.prior_period
    ? enrichPeriod(chartData.prior_period)
    : null

  // If we have prior data, re-run current enrichment with prior to get revenue growth
  const finalCurrent = enrichedPrior
    ? enrichPeriod(enrichedCurrent, enrichedPrior)
    : enrichedCurrent

  return {
    ...chartData,
    current_period: finalCurrent,
    prior_period: enrichedPrior,
  }
}

/**
 * Build the cash flow bridge data for the waterfall/bridge chart.
 * Returns an array of stages from Revenue down to FCF.
 */
export interface CashFlowBridgeStage {
  label: string
  value: number
  priorValue?: number | null
  type: 'positive' | 'negative' | 'result'
  /** Running total at this point */
  cumulative: number
  /** The deduction amount (negative) for subtraction stages */
  deduction?: number
}

export function buildCashFlowBridge(chartData: ChartData): CashFlowBridgeStage[] | null {
  const cp = chartData.current_period
  const pp = chartData.prior_period

  // Need at minimum revenue and at least one cash flow metric
  if (cp.revenue == null) return null
  if (cp.operating_cash_flow == null && cp.free_cash_flow == null && cp.operating_income == null) return null

  const stages: CashFlowBridgeStage[] = []

  // Stage 1: Revenue
  stages.push({
    label: 'Revenue',
    value: cp.revenue,
    priorValue: pp?.revenue,
    type: 'positive',
    cumulative: cp.revenue,
  })

  // Stage 2: Gross Profit (if available)
  if (cp.gross_profit != null) {
    const cogs = cp.revenue - cp.gross_profit
    stages.push({
      label: 'Gross Profit',
      value: cp.gross_profit,
      priorValue: pp?.gross_profit,
      type: 'positive',
      cumulative: cp.gross_profit,
      deduction: -cogs,
    })
  }

  // Stage 3: Operating Income (if available)
  if (cp.operating_income != null) {
    const prevCumulative = stages[stages.length - 1].cumulative
    const opex = prevCumulative - cp.operating_income
    stages.push({
      label: 'Operating Income',
      value: cp.operating_income,
      priorValue: pp?.operating_income,
      type: 'positive',
      cumulative: cp.operating_income,
      deduction: -opex,
    })
  }

  // Stage 4: EBITDA (if available and different from operating income)
  if (cp.ebitda != null && cp.operating_income != null && Math.abs(cp.ebitda - cp.operating_income) > 1000) {
    stages.push({
      label: 'EBITDA',
      value: cp.ebitda,
      priorValue: pp?.ebitda,
      type: 'positive',
      cumulative: cp.ebitda,
    })
  }

  // Stage 5: Operating Cash Flow
  if (cp.operating_cash_flow != null) {
    stages.push({
      label: 'Operating CF',
      value: cp.operating_cash_flow,
      priorValue: pp?.operating_cash_flow,
      type: 'positive',
      cumulative: cp.operating_cash_flow,
    })
  }

  // Stage 6: CapEx (subtraction)
  if (cp.capex != null && cp.operating_cash_flow != null) {
    const capexAbs = Math.abs(cp.capex)
    stages.push({
      label: 'CapEx',
      value: -capexAbs,
      priorValue: pp?.capex != null ? -Math.abs(pp.capex) : null,
      type: 'negative',
      cumulative: cp.operating_cash_flow - capexAbs,
      deduction: -capexAbs,
    })
  }

  // Stage 7: Free Cash Flow (result)
  const fcf = cp.free_cash_flow ?? (cp.operating_cash_flow != null && cp.capex != null
    ? cp.operating_cash_flow - Math.abs(cp.capex)
    : null)

  if (fcf != null) {
    stages.push({
      label: 'Free Cash Flow',
      value: fcf,
      priorValue: pp?.free_cash_flow ?? (pp?.operating_cash_flow != null && pp?.capex != null
        ? pp.operating_cash_flow - Math.abs(pp.capex)
        : null),
      type: 'result',
      cumulative: fcf,
    })
  }

  // Need at least 3 stages to make a meaningful bridge
  if (stages.length < 3) return null

  return stages
}
