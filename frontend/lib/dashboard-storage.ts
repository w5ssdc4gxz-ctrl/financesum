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

const buildStorageKey = (baseKey: string, userId?: string | null) =>
  userId ? `${baseKey}.${userId}` : baseKey

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
  loadAnalysisHistory(userId?: string | null): StoredAnalysisSnapshot[] {
    if (!isBrowser()) return []
    const storageKey = buildStorageKey(ANALYSIS_HISTORY_KEY, userId)
    const parsed = safeParse<StoredAnalysisSnapshot[]>(
      window.localStorage.getItem(storageKey),
      [],
    )
    const cleaned = dedupeSnapshots(parsed)
    if (cleaned.length !== parsed.length) {
      writeStorage(storageKey, cleaned)
    }
    return cleaned
  },

  upsertAnalysisSnapshot(snapshot: StoredAnalysisSnapshot, userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(ANALYSIS_HISTORY_KEY, userId)
    const existing = DashboardStorage.loadAnalysisHistory(userId)
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
    writeStorage(storageKey, updated)
  },

  replaceAnalysisHistory(snapshots: StoredAnalysisSnapshot[], userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(ANALYSIS_HISTORY_KEY, userId)
    const limited = dedupeSnapshots(snapshots).slice(0, MAX_ANALYSIS_HISTORY)
    writeStorage(storageKey, limited)
  },

  removeAnalysisSnapshot(analysisId: string, userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(ANALYSIS_HISTORY_KEY, userId)
    const filtered = DashboardStorage.loadAnalysisHistory(userId).filter(
      (item) => item.analysisId !== analysisId
    )
    writeStorage(storageKey, filtered)
  },

  loadRecentCompanies(userId?: string | null): StoredCompany[] {
    if (!isBrowser()) return []
    const storageKey = buildStorageKey(RECENT_COMPANIES_KEY, userId)
    return safeParse<StoredCompany[]>(
      window.localStorage.getItem(storageKey),
      [],
    )
  },

  upsertRecentCompany(company: StoredCompany, userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(RECENT_COMPANIES_KEY, userId)
    const existing = DashboardStorage.loadRecentCompanies(userId)
    const filtered = existing.filter((item) => item.id !== company.id)
    const updated = [company, ...filtered].slice(0, MAX_RECENT_COMPANIES)
    writeStorage(storageKey, updated)
  },

  replaceRecentCompanies(companies: StoredCompany[], userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(RECENT_COMPANIES_KEY, userId)
    const limited = companies.slice(0, MAX_RECENT_COMPANIES)
    writeStorage(storageKey, limited)
  },

  removeRecentCompany(companyId: string, userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(RECENT_COMPANIES_KEY, userId)
    const filtered = DashboardStorage.loadRecentCompanies(userId).filter(
      (item) => item.id !== companyId
    )
    writeStorage(storageKey, filtered)
  },

  loadSummaryPreferences(userId?: string | null): StoredSummaryPreferences | null {
    if (!isBrowser()) return null
    const storageKey = buildStorageKey(SUMMARY_PREFERENCES_KEY, userId)
    return safeParse<StoredSummaryPreferences | null>(
      window.localStorage.getItem(storageKey),
      null,
    )
  },

  saveSummaryPreferences(preferences: StoredSummaryPreferences, userId?: string | null) {
    const storageKey = buildStorageKey(SUMMARY_PREFERENCES_KEY, userId)
    writeStorage(storageKey, preferences)
  },
}

export default DashboardStorage
