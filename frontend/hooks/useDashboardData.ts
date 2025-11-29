"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import DashboardStorage, {
  StoredAnalysisSnapshot,
  StoredCompany,
  StoredSummaryPreferences,
} from "@/lib/dashboard-storage"
import { buildSummaryPreview, scoreToRating } from "@/lib/analysis-insights"
import { getCompanyCoordinates } from "@/lib/country-geo"
import { buildEodSymbol } from "@/lib/logo-utils"
import { analysisApi, dashboardApi } from "@/lib/api-client"

const DEFAULT_PREFERENCES: StoredSummaryPreferences = {
  mode: "default",
  investorFocus: "",
  focusAreas: [],
  tone: "objective",
  detailLevel: "balanced",
  outputStyle: "narrative",
  targetLength: 300,
  healthRating: {
    enabled: false,
    framework: "value_investor_default",
    weighting: "profitability_margins",
    riskTolerance: "moderately_conservative",
    analysisDepth: "key_financial_items",
    displayStyle: "score_only",
  },
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

const parseDateValue = (value: unknown): string | null => {
  if (!value) return null
  if (value instanceof Date) return value.toISOString()
  const parsed = new Date(value as any)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toISOString()
}

const coerceNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

const toPersonaSignals = (input: any) => {
  if (!input || typeof input !== "object") return undefined
  return Object.entries(input).map(([personaId, value]: [string, any]) => ({
    personaId,
    personaName: value?.persona_name ?? personaId,
    stance: value?.stance ?? "Neutral",
  }))
}

const normalizeHistoryEntry = (entry: any): StoredAnalysisSnapshot | null => {
  if (!entry) return null
  const analysisId = entry.analysis_id ?? entry.id
  const companyId = entry.company_id ?? entry.companyId ?? entry.company?.id
  if (!analysisId || !companyId) return null
  const rawSummary = entry.summary_md ?? entry.summary ?? null
  const healthScore = coerceNumber(entry.health_score)
  return {
    analysisId: String(analysisId),
    generatedAt:
      parseDateValue(entry.generated_at ?? entry.analysis_date ?? entry.created_at ?? entry.updated_at) ??
      new Date().toISOString(),
    id: String(companyId),
    name: entry.name ?? entry.company_name ?? entry.company?.name ?? entry.ticker ?? "Unknown company",
    ticker: entry.ticker ?? entry.symbol ?? entry.company?.ticker ?? "",
    exchange: entry.exchange ?? entry.company?.exchange ?? null,
    sector: entry.sector ?? entry.company?.sector ?? null,
    industry: entry.industry ?? entry.company?.industry ?? null,
    country: entry.country ?? entry.company?.country ?? null,
    healthScore,
    scoreBand: entry.score_band ?? null,
    ratingLabel: scoreToRating(healthScore).label,
    summaryMd: rawSummary,
    summaryPreview: buildSummaryPreview(rawSummary),
    personaSignals: toPersonaSignals(entry.investor_persona_summaries),
    filingId: entry.filing_id ?? entry.filingId ?? null,
    filingType: entry.filing_type ?? entry.filingType ?? null,
    filingDate: parseDateValue(entry.filing_date ?? entry.filingDate),
    source: entry.source ?? 'analysis',
  }
}

const normalizeCompanyEntry = (entry: any): StoredCompany | null => {
  if (!entry) return null
  const id = entry.id ?? entry.company_id
  const name = entry.name ?? entry.company_name ?? entry.ticker ?? null
  if (!id || !name) return null
  return {
    id: String(id),
    name,
    ticker: entry.ticker ?? entry.symbol ?? "",
    exchange: entry.exchange ?? null,
    sector: entry.sector ?? null,
    industry: entry.industry ?? null,
    country: entry.country ?? null,
  }
}

const getTimestamp = (value?: string | null) => {
  if (!value) return 0
  const parsed = new Date(value)
  const time = parsed.getTime()
  return Number.isNaN(time) ? 0 : time
}

const mergeHistorySnapshots = (
  current: StoredAnalysisSnapshot[],
  incoming: StoredAnalysisSnapshot[],
): StoredAnalysisSnapshot[] => {
  if (!incoming.length) return current
  const byId = new Map<string, StoredAnalysisSnapshot>()
  current.forEach((item) => byId.set(item.analysisId ?? item.id, item))
  incoming.forEach((item) => byId.set(item.analysisId ?? item.id, item))
  return Array.from(byId.values()).sort((a, b) => getTimestamp(b.generatedAt) - getTimestamp(a.generatedAt))
}

const mergeCompanies = (current: StoredCompany[], incoming: StoredCompany[]): StoredCompany[] => {
  if (!incoming.length) return current
  const byId = new Map<string, StoredCompany>()
  current.forEach((company) => byId.set(company.id, company))
  incoming.forEach((company) => byId.set(company.id, company))
  return Array.from(byId.values())
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

  const removeHistoryEntry = useCallback(async (analysisId: string) => {
    DashboardStorage.removeAnalysisSnapshot(analysisId)
    setHistory(DashboardStorage.loadAnalysisHistory())
    try {
      await analysisApi.deleteAnalysis(analysisId)
    } catch (error) {
      console.error("Failed to delete analysis from backend", error)
    }
  }, [])

  useEffect(() => {
    if (typeof window === "undefined") return
    syncFromStorage()
    window.addEventListener("storage", syncFromStorage)
    window.addEventListener("focus", syncFromStorage)
    window.addEventListener("financesum-dashboard-sync", syncFromStorage as EventListener)
    return () => {
      window.removeEventListener("storage", syncFromStorage)
      window.removeEventListener("focus", syncFromStorage)
      window.removeEventListener("financesum-dashboard-sync", syncFromStorage as EventListener)
    }
  }, [syncFromStorage])

  useEffect(() => {
    if (typeof window === "undefined") return
    let cancelled = false
    const fetchOverview = async () => {
      try {
        const response = await dashboardApi.overview()
        if (cancelled) return
        const payload = response.data ?? {}
        const normalizedHistory = Array.isArray(payload.history)
          ? payload.history.map(normalizeHistoryEntry).filter((item): item is StoredAnalysisSnapshot => Boolean(item))
          : []
        if (normalizedHistory.length) {
          setHistory((prev) => {
            const merged = mergeHistorySnapshots(prev, normalizedHistory)
            if (merged === prev) {
              return prev
            }
            DashboardStorage.replaceAnalysisHistory(merged)
            return merged
          })
        }

        const normalizedCompanies = Array.isArray(payload.companies)
          ? payload.companies.map(normalizeCompanyEntry).filter((item): item is StoredCompany => Boolean(item))
          : []
        if (normalizedCompanies.length) {
          setCompanies((prev) => {
            const merged = mergeCompanies(prev, normalizedCompanies)
            if (merged === prev) {
              return prev
            }
            DashboardStorage.replaceRecentCompanies(merged)
            return merged
          })
        }
      } catch (error) {
        if (!cancelled) {
          console.error("Failed to fetch dashboard overview", error)
        }
      }
    }
    fetchOverview()
    return () => {
      cancelled = true
    }
  }, [])

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
    removeHistoryEntry,
  }
}

export default useDashboardData
