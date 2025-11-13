"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import DashboardStorage, {
  StoredAnalysisSnapshot,
  StoredCompany,
  StoredSummaryPreferences,
} from "@/lib/dashboard-storage"
import { scoreToRating } from "@/lib/analysis-insights"
import { getCompanyCoordinates } from "@/lib/country-geo"
import { buildEodSymbol } from "@/lib/logo-utils"

const DEFAULT_PREFERENCES: StoredSummaryPreferences = {
  mode: "default",
  investorFocus: "",
  focusAreas: [],
  tone: "objective",
  detailLevel: "balanced",
  outputStyle: "narrative",
  targetLength: 300,
}

export type DashboardMapPoint = {
  id: string
  lat: number
  lng: number
  ticker: string
  name: string
  exchange?: string | null
  score?: number | null
  ratingGrade: string
  ratingLabel: string
  symbol: string | null
  country?: string | null
}

const countBy = (items: Array<string | null | undefined>) => {
  return items.reduce<Record<string, number>>((acc, item) => {
    if (!item) return acc
    const key = item.trim()
    if (!key) return acc
    acc[key] = (acc[key] ?? 0) + 1
    return acc
  }, {})
}

export function useDashboardData() {
  const [history, setHistory] = useState<StoredAnalysisSnapshot[]>([])
  const [companies, setCompanies] = useState<StoredCompany[]>([])
  const [preferences, setPreferences] = useState<StoredSummaryPreferences>(DEFAULT_PREFERENCES)

  const syncFromStorage = useCallback(() => {
    setHistory(DashboardStorage.loadAnalysisHistory())
    setCompanies(DashboardStorage.loadRecentCompanies())
    setPreferences(DashboardStorage.loadSummaryPreferences() ?? DEFAULT_PREFERENCES)
  }, [])

  useEffect(() => {
    if (typeof window === "undefined") return
    syncFromStorage()
    window.addEventListener("storage", syncFromStorage)
    window.addEventListener("focus", syncFromStorage)
    return () => {
      window.removeEventListener("storage", syncFromStorage)
      window.removeEventListener("focus", syncFromStorage)
    }
  }, [syncFromStorage])

  const primaryAnalysis = history.length ? history[0] : null
  const personaSignals = primaryAnalysis?.personaSignals ?? []

  const stats = useMemo(() => {
    if (!history.length) {
      return {
        analysisCount: 0,
        scoredCount: 0,
        avgScore: null as number | null,
        maxScore: null as number | null,
        bestCompany: null as StoredAnalysisSnapshot | null,
        sectors: [] as Array<{ label: string; value: number }>,
        countries: [] as Array<{ label: string; value: number }>,
      }
    }
    let scoredCount = 0
    let scoreSum = 0
    let maxScore = -Infinity
    let bestCompany: StoredAnalysisSnapshot | null = null
    history.forEach((item) => {
      if (typeof item.healthScore === "number") {
        scoredCount += 1
        scoreSum += item.healthScore
        if (item.healthScore > maxScore) {
          maxScore = item.healthScore
          bestCompany = item
        }
      }
    })

    const average = scoredCount ? Math.round((scoreSum / scoredCount) * 10) / 10 : null
    const topSectors = Object.entries(countBy(history.map((item) => item.sector)))
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([label, value]) => ({ label, value }))
    const topCountries = Object.entries(countBy(history.map((item) => item.country)))
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([label, value]) => ({ label, value }))

    return {
      analysisCount: history.length,
      scoredCount,
      avgScore: average,
      maxScore: Number.isFinite(maxScore) ? maxScore : null,
      bestCompany,
      sectors: topSectors,
      countries: topCountries,
    }
  }, [history])

  const mapPoints = useMemo(() => {
    return history
      .map<DashboardMapPoint | null>((item) => {
        const coords = getCompanyCoordinates(item.country, item.exchange)
        if (!coords) return null
        const rating = scoreToRating(item.healthScore)
        return {
          id: item.analysisId,
          lat: coords.lat,
          lng: coords.lng,
          ticker: item.ticker,
          name: item.name,
          exchange: item.exchange,
          score: item.healthScore,
          ratingGrade: rating.grade,
          ratingLabel: rating.label,
          symbol: buildEodSymbol(item.ticker, item.exchange, item.country),
          country: item.country,
        }
      })
      .filter((point): point is DashboardMapPoint => Boolean(point))
  }, [history])

  const hasAnalyses = history.length > 0

  return {
    history,
    companies,
    preferences,
    stats,
    primaryAnalysis,
    personaSignals,
    mapPoints,
    hasAnalyses,
    refresh: syncFromStorage,
  }
}

export default useDashboardData
