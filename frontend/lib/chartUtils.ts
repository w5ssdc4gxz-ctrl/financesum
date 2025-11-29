// Tremor Chart Utilities [v1.0.0]

export const AvailableChartColors = [
  "blue",
  "emerald",
  "violet",
  "amber",
  "gray",
  "cyan",
  "pink",
  "lime",
  "fuchsia",
  "indigo",
  "rose",
  "orange",
] as const

export type AvailableChartColorsKeys = (typeof AvailableChartColors)[number]

export const constructCategoryColors = (
  categories: string[],
  colors: AvailableChartColorsKeys[],
): Map<string, AvailableChartColorsKeys> => {
  const categoryColors = new Map<string, AvailableChartColorsKeys>()
  categories.forEach((category, index) => {
    categoryColors.set(category, colors[index % colors.length])
  })
  return categoryColors
}

export const getColorClassName = (
  color: AvailableChartColorsKeys,
  type: "bg" | "stroke" | "fill" | "text",
): string => {
  const colorClassNames: {
    [key in AvailableChartColorsKeys]: {
      bg: string
      stroke: string
      fill: string
      text: string
    }
  } = {
    blue: {
      bg: "bg-blue-500",
      stroke: "stroke-blue-500",
      fill: "fill-blue-500",
      text: "text-blue-500",
    },
    emerald: {
      bg: "bg-emerald-500",
      stroke: "stroke-emerald-500",
      fill: "fill-emerald-500",
      text: "text-emerald-500",
    },
    violet: {
      bg: "bg-violet-500",
      stroke: "stroke-violet-500",
      fill: "fill-violet-500",
      text: "text-violet-500",
    },
    amber: {
      bg: "bg-amber-500",
      stroke: "stroke-amber-500",
      fill: "fill-amber-500",
      text: "text-amber-500",
    },
    gray: {
      bg: "bg-gray-500",
      stroke: "stroke-gray-500",
      fill: "fill-gray-500",
      text: "text-gray-500",
    },
    cyan: {
      bg: "bg-cyan-500",
      stroke: "stroke-cyan-500",
      fill: "fill-cyan-500",
      text: "text-cyan-500",
    },
    pink: {
      bg: "bg-pink-500",
      stroke: "stroke-pink-500",
      fill: "fill-pink-500",
      text: "text-pink-500",
    },
    lime: {
      bg: "bg-lime-500",
      stroke: "stroke-lime-500",
      fill: "fill-lime-500",
      text: "text-lime-500",
    },
    fuchsia: {
      bg: "bg-fuchsia-500",
      stroke: "stroke-fuchsia-500",
      fill: "fill-fuchsia-500",
      text: "text-fuchsia-500",
    },
    indigo: {
      bg: "bg-indigo-500",
      stroke: "stroke-indigo-500",
      fill: "fill-indigo-500",
      text: "text-indigo-500",
    },
    rose: {
      bg: "bg-rose-500",
      stroke: "stroke-rose-500",
      fill: "fill-rose-500",
      text: "text-rose-500",
    },
    orange: {
      bg: "bg-orange-500",
      stroke: "stroke-orange-500",
      fill: "fill-orange-500",
      text: "text-orange-500",
    },
  }
  return colorClassNames[color][type]
}

// Helper to format large numbers
export const formatNumber = (value: number): string => {
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}K`
  }
  return value.toString()
}

// Helper to format percentages
export const formatPercent = (value: number): string => {
  return `${value.toFixed(0)}%`
}

// Helper to format currency
export const formatCurrency = (value: number): string => {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
}
