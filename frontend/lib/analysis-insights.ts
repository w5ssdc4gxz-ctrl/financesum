export type AnalysisRating = {
  grade: string
  label: string
  description: string
  sentiment: "bullish" | "neutral" | "bearish"
}

const ratingScale: Array<{ min: number; grade: string; label: string; description: string; sentiment: AnalysisRating["sentiment"] }> = [
  {
    min: 85,
    grade: "A",
    label: "Very Healthy",
    description: "Exceptional financial health with strong balance sheet, profitability, and cash flows.",
    sentiment: "bullish",
  },
  {
    min: 70,
    grade: "B",
    label: "Healthy",
    description: "Solid fundamentals with manageable risks and sustainable performance.",
    sentiment: "bullish",
  },
  {
    min: 50,
    grade: "C",
    label: "Watch",
    description: "Mixed financial signals requiring monitoring of key metrics.",
    sentiment: "neutral",
  },
  {
    min: 0,
    grade: "D",
    label: "At Risk",
    description: "Financial stress indicators present; defensive positioning recommended.",
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
