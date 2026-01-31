/**
 * Shared chart utilities, colors, and animation configurations
 */

// ============================================================================
// COLOR SYSTEM
// ============================================================================

export const chartColors = {
  // Semantic colors
  positive: {
    primary: '#10B981',
    secondary: '#34D399',
    glow: 'rgba(16, 185, 129, 0.4)',
    gradient: ['#10B981', '#059669'],
  },
  negative: {
    primary: '#EF4444',
    secondary: '#F87171',
    glow: 'rgba(239, 68, 68, 0.4)',
    gradient: ['#EF4444', '#DC2626'],
  },
  neutral: {
    primary: '#3B82F6',
    secondary: '#60A5FA',
    glow: 'rgba(59, 130, 246, 0.4)',
    gradient: ['#3B82F6', '#2563EB'],
  },
  accent: {
    primary: '#8B5CF6',
    secondary: '#A78BFA',
    glow: 'rgba(139, 92, 246, 0.4)',
    gradient: ['#8B5CF6', '#7C3AED'],
  },
  
  // Segment palette for donuts/breakdowns
  segments: [
    '#0EA5E9', // Sky
    '#22C55E', // Green
    '#F59E0B', // Amber
    '#6366F1', // Indigo
    '#14B8A6', // Teal
    '#EC4899', // Pink
    '#F97316', // Orange
    '#8B5CF6', // Violet
  ],
  
  // Gradient presets
  gradients: {
    premium: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    ocean: 'linear-gradient(135deg, #0EA5E9 0%, #06B6D4 50%, #10B981 100%)',
    sunset: 'linear-gradient(135deg, #F59E0B 0%, #EF4444 100%)',
    aurora: 'linear-gradient(135deg, #a855f7 0%, #3b82f6 50%, #06b6d4 100%)',
    emerald: 'linear-gradient(135deg, #10B981 0%, #059669 100%)',
  },
} as const

// ============================================================================
// ANIMATION CONFIGURATIONS
// ============================================================================

export const chartAnimations = {
  // Container entrance
  container: {
    initial: { opacity: 0, y: 20, scale: 0.98 },
    animate: { opacity: 1, y: 0, scale: 1 },
    exit: { opacity: 0, y: -10, scale: 0.98 },
    transition: { 
      duration: 0.5, 
      ease: [0.4, 0, 0.2, 1] 
    },
  },
  
  // Stagger children
  stagger: {
    animate: { transition: { staggerChildren: 0.08, delayChildren: 0.1 } },
  },
  
  // Number count-up (for GSAP)
  number: {
    duration: 1.2,
    ease: 'power3.out',
  },
  
  // Hover effects
  hover: {
    scale: 1.02,
    y: -4,
    transition: { type: 'spring', stiffness: 400, damping: 25 },
  },
  
  // Path drawing
  pathDraw: {
    initial: { pathLength: 0, opacity: 0 },
    animate: { pathLength: 1, opacity: 1 },
    transition: { duration: 1.5, ease: [0.4, 0, 0.2, 1] },
  },
  
  // Bar growth
  barGrow: {
    initial: { scaleX: 0, originX: 0 },
    animate: { scaleX: 1 },
    transition: { 
      type: 'spring', 
      stiffness: 100, 
      damping: 15,
      mass: 0.5,
    },
  },
  
  // Gauge arc
  gaugeArc: {
    initial: { strokeDashoffset: 1 },
    animate: { strokeDashoffset: 0 },
    transition: { duration: 1.5, ease: [0.4, 0, 0.2, 1] },
  },
  
  // Glow pulse
  glowPulse: {
    animate: {
      boxShadow: [
        '0 0 20px rgba(16, 185, 129, 0.3)',
        '0 0 40px rgba(16, 185, 129, 0.5)',
        '0 0 20px rgba(16, 185, 129, 0.3)',
      ],
    },
    transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
  },
} as const

// ============================================================================
// VALUE FORMATTING
// ============================================================================

/**
 * Format currency values with smart magnitude detection
 */
export function formatCurrency(value: number, symbol: string = '$'): string {
  const s = symbol?.trim() || '$'
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  
  if (abs >= 1_000_000_000_000) return `${sign}${s}${(abs / 1_000_000_000_000).toFixed(2)}T`
  if (abs >= 1_000_000_000) return `${sign}${s}${(abs / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${sign}${s}${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}${s}${(abs / 1_000).toFixed(1)}K`
  return `${sign}${s}${abs.toFixed(0)}`
}

/**
 * Format compact numbers without currency
 */
export function formatCompact(value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  
  if (abs >= 1_000_000_000) return `${sign}${(abs / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}K`
  return `${sign}${abs.toFixed(0)}`
}

/**
 * Format percentage values
 */
export function formatPercent(value: number, decimals: number = 1): string {
  return `${value >= 0 ? '' : ''}${value.toFixed(decimals)}%`
}

/**
 * Smart KPI value formatter based on unit
 */
export function formatKpiValue(value: number, unit?: string): string {
  if (unit === '%') return formatPercent(value)
  if (unit === 'M') return `${value.toFixed(1)}M`
  if (unit === 'B') return `${value.toFixed(2)}B`
  if (unit === '$' || unit === '€' || unit === '£') return formatCurrency(value, unit)
  
  // Auto-detect magnitude
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toFixed(value % 1 === 0 ? 0 : 2)
}

// ============================================================================
// VALUE NORMALIZATION
// ============================================================================

/**
 * Fix the 0.04 -> 4% problem for percentage values
 * If a percentage value is between -1.5 and 1.5 (exclusive of 0), 
 * it's likely a decimal that needs to be multiplied by 100
 */
export function normalizePercentageValue(value: number, unit?: string): number {
  if (unit === '%' && Math.abs(value) < 1.5 && Math.abs(value) > 0.001) {
    return value * 100
  }
  return value
}

/**
 * Normalize KPI unit string to standard format
 */
export function normalizeUnit(unit?: string | null): string | undefined {
  if (!unit) return undefined
  const normalized = unit.trim().toLowerCase()
  
  if (['percent', 'percentage', 'pct', '%'].includes(normalized)) return '%'
  if (['usd', '$', 'dollars', 'us$', 'us dollars'].includes(normalized)) return '$'
  if (['eur', '€', 'euro', 'euros'].includes(normalized)) return '€'
  if (['gbp', '£', 'pound', 'pounds', 'sterling'].includes(normalized)) return '£'
  if (['million', 'millions', 'm'].includes(normalized)) return 'M'
  if (['billion', 'billions', 'b'].includes(normalized)) return 'B'
  
  return unit.trim()
}

// ============================================================================
// CHART TYPE DETECTION
// ============================================================================

export type ChartType = 'gauge' | 'trend' | 'donut' | 'bar' | 'waterfall' | 'metric'

/**
 * Patterns for intelligent chart type detection
 */
const chartTypePatterns = {
  gauge: /margin|rate|efficiency|utilization|occupancy|penetration|share|ratio|conversion|retention|churn|yield|coverage|load factor/i,
  trend: /growth|yoy|qoq|change|increase|decrease|trend|momentum|velocity/i,
  waterfall: /bridge|contribution|breakdown|walk|impact|driver/i,
  donut: /mix|composition|segment|by region|by product|by category|allocation|distribution/i,
}

/**
 * Intelligently determine the best chart type for a KPI
 */
export function resolveChartType(kpi: {
  name: string
  value: number
  unit?: string
  prior_value?: number | null
  segments?: { label: string; value: number }[]
  history?: { period_label: string; value: number }[]
  chart_type?: string
}): ChartType {
  const name = kpi.name.toLowerCase()
  const unit = kpi.unit
  const hasSegments = kpi.segments && kpi.segments.length >= 2
  const hasHistory = kpi.history && kpi.history.length >= 3
  const hasPrior = kpi.prior_value != null && kpi.prior_value !== 0
  const value = normalizePercentageValue(kpi.value, unit)
  
  // Explicit chart type from backend takes precedence (if valid)
  if (kpi.chart_type) {
    const explicit = kpi.chart_type.toLowerCase().trim()

    // Backend hints are suggestions; validate so we don't render misleading charts.
    // Example: "comparison" without a prior_value will render a full-width bar, which looks like a progress bar.
    if (['donut', 'doughnut', 'pie', 'breakdown'].includes(explicit) && hasSegments) return 'donut'
    if (['trend', 'sparkline', 'series', 'line'].includes(explicit) && hasHistory) return 'trend'
    if (['waterfall'].includes(explicit)) return 'waterfall'
    if (['metric', 'number', 'card'].includes(explicit)) return 'metric'

    if (['gauge', 'progress', 'radial', 'meter'].includes(explicit)) {
      if (unit === '%' && value >= 0 && value <= 100) return 'gauge'
      // If the backend asks for a gauge but unit/value don't fit, fall through to heuristics.
    }

    if (['bar', 'comparison', 'compare'].includes(explicit)) {
      if (hasPrior) return 'bar'
      // If there's no prior value, fall through to heuristics (often 'metric' or 'gauge').
    }
  }
  
  // Segment data -> donut chart
  if (hasSegments) return 'donut'
  
  // History data -> trend chart
  if (hasHistory) return 'trend'
  
  // Pattern matching for chart type
  if (chartTypePatterns.donut.test(name) && hasSegments) return 'donut'
  if (chartTypePatterns.waterfall.test(name)) return 'waterfall'
  if (chartTypePatterns.trend.test(name)) return hasPrior ? 'bar' : 'metric'
  
  // Percentage values -> gauge ONLY when the KPI is a true "rate" style metric
  // (margins, utilization, retention, etc.). Pure % changes (growth/up/down) should be metric/bar, not a gauge.
  if (chartTypePatterns.gauge.test(name) && unit === '%' && value >= 0 && value <= 100) return 'gauge'
  
  // Has prior value -> comparison bar
  if (hasPrior) return 'bar'
  
  // Fallback to metric card for single values
  return 'metric'
}

// ============================================================================
// GEOMETRY HELPERS
// ============================================================================

/**
 * Generate SVG arc path for gauges
 */
export function generateArcPath(
  percent: number,
  radius: number = 40,
  strokeWidth: number = 8,
  startAngle: number = -135,
  endAngle: number = 135
): string {
  const totalAngle = endAngle - startAngle
  const angle = startAngle + (totalAngle * Math.min(100, Math.max(0, percent))) / 100
  
  const startRad = (startAngle * Math.PI) / 180
  const endRad = (angle * Math.PI) / 180
  
  const cx = 50
  const cy = 50
  
  const x1 = cx + radius * Math.cos(startRad)
  const y1 = cy + radius * Math.sin(startRad)
  const x2 = cx + radius * Math.cos(endRad)
  const y2 = cy + radius * Math.sin(endRad)
  
  const largeArc = Math.abs(angle - startAngle) > 180 ? 1 : 0
  
  return `M ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`
}

/**
 * Calculate change between two values
 */
export function calculateChange(current: number, prior: number): { 
  value: number
  display: string
  isPositive: boolean 
} | null {
  if (prior === 0) return null
  const change = ((current - prior) / Math.abs(prior)) * 100
  return {
    value: change,
    display: `${change >= 0 ? '+' : ''}${change.toFixed(1)}%`,
    isPositive: change >= 0,
  }
}

// ============================================================================
// CSS CLASS HELPERS
// ============================================================================

/**
 * Get color classes based on value sentiment
 */
export function getValueColorClasses(value: number, type: 'text' | 'bg' | 'border' = 'text'): string {
  if (value > 0) {
    switch (type) {
      case 'text': return 'text-emerald-600 dark:text-emerald-400'
      case 'bg': return 'bg-emerald-50 dark:bg-emerald-900/30'
      case 'border': return 'border-emerald-200 dark:border-emerald-800'
    }
  }
  if (value < 0) {
    switch (type) {
      case 'text': return 'text-red-600 dark:text-red-400'
      case 'bg': return 'bg-red-50 dark:bg-red-900/30'
      case 'border': return 'border-red-200 dark:border-red-800'
    }
  }
  switch (type) {
    case 'text': return 'text-gray-600 dark:text-gray-400'
    case 'bg': return 'bg-gray-50 dark:bg-gray-900/30'
    case 'border': return 'border-gray-200 dark:border-gray-800'
  }
}

/**
 * Get gradient class based on value range
 */
export function getGaugeGradient(value: number): string {
  if (value >= 70) return 'from-emerald-500 to-teal-400'
  if (value >= 40) return 'from-sky-500 to-blue-400'
  if (value >= 20) return 'from-amber-500 to-orange-400'
  return 'from-red-500 to-rose-400'
}
