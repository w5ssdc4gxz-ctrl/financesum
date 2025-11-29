export type AnalysisRating = {
  grade: string
  label: string
  description: string
  sentiment: "bullish" | "neutral" | "bearish"
}

const ratingScale: Array<{ min: number; grade: string; label: string; description: string; sentiment: AnalysisRating["sentiment"] }> = [
  {
    min: 90,
    grade: "A+",
    label: "Exceptional",
    description: "Balance sheet, growth, and profitability all screen in the top decile.",
    sentiment: "bullish",
  },
  {
    min: 80,
    grade: "A",
    label: "Strong Buy",
    description: "Robust fundamentals with manageable risks and clear catalysts.",
    sentiment: "bullish",
  },
  {
    min: 70,
    grade: "A-",
    label: "Outperform",
    description: "Well-managed company with improving metrics worth accumulating.",
    sentiment: "bullish",
  },
  {
    min: 60,
    grade: "B+",
    label: "Accumulate",
    description: "Solid core performance, but monitor execution and margins.",
    sentiment: "neutral",
  },
  {
    min: 50,
    grade: "B",
    label: "Market Perform",
    description: "Balanced risk/reward profile; catalysts needed for upside.",
    sentiment: "neutral",
  },
  {
    min: 40,
    grade: "C+",
    label: "Hold",
    description: "Mixed signals—stabilize cash flow or leverage before scaling exposure.",
    sentiment: "neutral",
  },
  {
    min: 30,
    grade: "C",
    label: "Watchlist",
    description: "Pressure on growth or liquidity warrants selective positioning.",
    sentiment: "bearish",
  },
  {
    min: 0,
    grade: "D",
    label: "High Risk",
    description: "Defensive posture recommended until fundamentals improve.",
    sentiment: "bearish",
  },
]

export function scoreToRating(score?: number | null): AnalysisRating {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return {
      grade: "NR",
      label: "Not Rated",
      description: "Run an analysis to receive an AI-powered rating.",
      sentiment: "neutral",
    }
  }

  const normalized = Math.max(0, Math.min(100, score))
  const rating = ratingScale.find((band) => normalized >= band.min)
  if (!rating) {
    return {
      grade: "NR",
      label: "Not Rated",
      description: "Run an analysis to receive an AI-powered rating.",
      sentiment: "neutral",
    }
  }
  return rating
}

export function buildSummaryPreview(content?: string | null, maxLength = 420): string | null {
  if (!content) return null
  const text = content
    .replace(/[#*_`>~-]/g, "")
    .replace(/\s+/g, " ")
    .trim()
  if (!text) return null
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength).trim()}…`
}
