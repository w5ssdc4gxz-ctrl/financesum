"use client"

/**
 * Local storage helpers that capture contextual data for the dashboard.
 * We persist recent analyses, companies, and preference settings so the dashboard
 * can instantly render meaningful insights without waiting on additional backend calls.
 */
const ANALYSIS_HISTORY_KEY = "financesum.analysisHistory"
const RECENT_COMPANIES_KEY = "financesum.recentCompanies"
const SUMMARY_PREFERENCES_KEY = "financesum.summaryPreferences"

const MAX_ANALYSIS_HISTORY = 12
const MAX_RECENT_COMPANIES = 12

const isBrowser = () => typeof window !== "undefined"

const safeParse = <T>(value: string | null, fallback: T): T => {
  if (!value) return fallback
  try {
    return JSON.parse(value) as T
  } catch {
    return fallback
  }
}

const writeStorage = (key: string, value: unknown) => {
  if (!isBrowser()) return
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Ignore quota or access errors; dashboard will simply skip cached data.
  }
}

export type StoredCompany = {
  id: string
  name: string
  ticker: string
  exchange?: string | null
  sector?: string | null
  industry?: string | null
  country?: string | null
}

export type StoredPersonaSignal = {
  personaId: string
  personaName: string
  stance: string
}

export type StoredAnalysisSnapshot = StoredCompany & {
  analysisId: string
  generatedAt: string
  healthScore?: number | null
  scoreBand?: string | null
  ratingLabel?: string | null
  summaryMd?: string | null
  summaryPreview?: string | null
  focusAreas?: string[] | null
  personaSignals?: StoredPersonaSignal[]
  filingId?: string | null
  filingType?: string | null
  filingDate?: string | null
  source?: 'analysis' | 'summary'
}

export type StoredHealthRatingPreferences = {
  enabled: boolean
  framework: string
  weighting: string
  riskTolerance: string
  analysisDepth: string
  displayStyle: string
}

export type StoredSummaryPreferences = {
  mode: "default" | "custom"
  investorFocus: string
  focusAreas: string[]
  tone: string
  detailLevel: string
  outputStyle: string
  targetLength: number
  healthRating?: StoredHealthRatingPreferences
}

const SNAPSHOT_ANALYSIS = "analysis"
const SNAPSHOT_SUMMARY = "summary"

const resolveSnapshotSource = (snapshot?: Partial<StoredAnalysisSnapshot>) =>
  snapshot?.source ?? SNAPSHOT_ANALYSIS

const getSnapshotTimestamp = (value?: string | null) => {
  if (!value) return 0
  const parsed = new Date(value)
  const time = parsed.getTime()
  return Number.isNaN(time) ? 0 : time
}

const sortSnapshots = (snapshots: StoredAnalysisSnapshot[]) => {
  return [...snapshots].sort(
    (a, b) => getSnapshotTimestamp(b.generatedAt) - getSnapshotTimestamp(a.generatedAt)
  )
}

const buildSnapshotKey = (snapshot: StoredAnalysisSnapshot) => {
  const source = resolveSnapshotSource(snapshot)
  if (source === SNAPSHOT_ANALYSIS) {
    const companyKey = snapshot.id || snapshot.ticker
    if (companyKey) {
      return `${source}:${companyKey}`
    }
  }
  return `${source}:${snapshot.analysisId}`
}

const dedupeSnapshots = (snapshots: StoredAnalysisSnapshot[]) => {
  if (!snapshots.length) return snapshots
  const seen = new Set<string>()
  const cleaned: StoredAnalysisSnapshot[] = []
  for (const snapshot of sortSnapshots(snapshots)) {
    if (!snapshot) continue
    const key = buildSnapshotKey(snapshot)
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    cleaned.push(snapshot)
  }
  return cleaned
}

export const DashboardStorage = {
  loadAnalysisHistory(): StoredAnalysisSnapshot[] {
    if (!isBrowser()) return []
    const parsed = safeParse<StoredAnalysisSnapshot[]>(
      window.localStorage.getItem(ANALYSIS_HISTORY_KEY),
      [],
    )
    const cleaned = dedupeSnapshots(parsed)
    if (cleaned.length !== parsed.length) {
      writeStorage(ANALYSIS_HISTORY_KEY, cleaned)
    }
    return cleaned
  },

  upsertAnalysisSnapshot(snapshot: StoredAnalysisSnapshot) {
    if (!isBrowser()) return
    const existing = DashboardStorage.loadAnalysisHistory()
    const snapshotSource = resolveSnapshotSource(snapshot)
    const filtered = existing.filter((item) => {
      if (item.analysisId === snapshot.analysisId) {
        return false
      }
      const itemSource = resolveSnapshotSource(item)
      if (
        snapshotSource === SNAPSHOT_ANALYSIS &&
        itemSource === SNAPSHOT_ANALYSIS &&
        item.id === snapshot.id
      ) {
        return false
      }
      return true
    })
    const updated = dedupeSnapshots([snapshot, ...filtered]).slice(0, MAX_ANALYSIS_HISTORY)
    writeStorage(ANALYSIS_HISTORY_KEY, updated)
  },

  replaceAnalysisHistory(snapshots: StoredAnalysisSnapshot[]) {
    if (!isBrowser()) return
    const limited = dedupeSnapshots(snapshots).slice(0, MAX_ANALYSIS_HISTORY)
    writeStorage(ANALYSIS_HISTORY_KEY, limited)
  },

  removeAnalysisSnapshot(analysisId: string) {
    if (!isBrowser()) return
    const filtered = DashboardStorage.loadAnalysisHistory().filter(
      (item) => item.analysisId !== analysisId
    )
    writeStorage(ANALYSIS_HISTORY_KEY, filtered)
  },

  loadRecentCompanies(): StoredCompany[] {
    if (!isBrowser()) return []
    return safeParse<StoredCompany[]>(
      window.localStorage.getItem(RECENT_COMPANIES_KEY),
      [],
    )
  },

  upsertRecentCompany(company: StoredCompany) {
    if (!isBrowser()) return
    const existing = DashboardStorage.loadRecentCompanies()
    const filtered = existing.filter((item) => item.id !== company.id)
    const updated = [company, ...filtered].slice(0, MAX_RECENT_COMPANIES)
    writeStorage(RECENT_COMPANIES_KEY, updated)
  },

  replaceRecentCompanies(companies: StoredCompany[]) {
    if (!isBrowser()) return
    const limited = companies.slice(0, MAX_RECENT_COMPANIES)
    writeStorage(RECENT_COMPANIES_KEY, limited)
  },

  removeRecentCompany(companyId: string) {
    if (!isBrowser()) return
    const filtered = DashboardStorage.loadRecentCompanies().filter(
      (item) => item.id !== companyId
    )
    writeStorage(RECENT_COMPANIES_KEY, filtered)
  },

  loadSummaryPreferences(): StoredSummaryPreferences | null {
    if (!isBrowser()) return null
    return safeParse<StoredSummaryPreferences | null>(
      window.localStorage.getItem(SUMMARY_PREFERENCES_KEY),
      null,
    )
  },

  saveSummaryPreferences(preferences: StoredSummaryPreferences) {
    writeStorage(SUMMARY_PREFERENCES_KEY, preferences)
  },
}

export default DashboardStorage
