"use client"

/**
 * Local storage helpers that capture contextual data for the dashboard.
 * We persist recent analyses, companies, and preference settings so the dashboard
 * can instantly render meaningful insights without waiting on additional backend calls.
 */
const ANALYSIS_HISTORY_KEY = "financesum.analysisHistory"
const RECENT_COMPANIES_KEY = "financesum.recentCompanies"
const SUMMARY_PREFERENCES_KEY = "financesum.summaryPreferences"
const SUMMARY_EVENTS_KEY = "financesum.summaryEvents"

const MAX_ANALYSIS_HISTORY = 12
const MAX_RECENT_COMPANIES = 12
const MAX_SUMMARY_EVENTS = 2000

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

export type StoredChartDataPeriod = {
  revenue?: number | null
  operating_income?: number | null
  net_income?: number | null
  free_cash_flow?: number | null
  operating_margin?: number | null
  net_margin?: number | null
  gross_margin?: number | null
}

export type StoredChartData = {
  current_period: StoredChartDataPeriod
  prior_period?: StoredChartDataPeriod | null
  period_type?: 'quarterly' | 'annual'
  current_label?: string
  prior_label?: string
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
  selectedPersona?: string | null
  chartData?: StoredChartData | null
  ratios?: Record<string, number | null> | null
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
  sectionInstructions?: Record<string, string>
  focusAreas: string[]
  tone: string
  detailLevel: string
  outputStyle: string
  targetLength: number
  healthRating?: StoredHealthRatingPreferences
}

export type StoredSummaryEvent = {
  eventId: string
  generatedAt: string
  kind: "analysis" | "summary"
  analysisId?: string | null
  filingId?: string | null
  companyId?: string | null
}

const SNAPSHOT_ANALYSIS = "analysis"
const SNAPSHOT_SUMMARY = "summary"

const resolveSnapshotSource = (snapshot?: Partial<StoredAnalysisSnapshot>) => {
  if (snapshot?.source) return snapshot.source
  const analysisId = typeof snapshot?.analysisId === "string" ? snapshot.analysisId : ""
  if (analysisId.startsWith("summary-")) return SNAPSHOT_SUMMARY
  return SNAPSHOT_ANALYSIS
}

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

const getEventTimestamp = (value?: string | null) => {
  if (!value) return 0
  const parsed = new Date(value)
  const time = parsed.getTime()
  return Number.isNaN(time) ? 0 : time
}

const normalizeEventDate = (value: unknown): string | null => {
  if (!value) return null
  const parsed = new Date(value as any)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toISOString()
}

const normalizeSummaryEvent = (
  input?: Partial<StoredSummaryEvent> | null,
): StoredSummaryEvent | null => {
  if (!input || typeof input !== "object") return null

  const kind = input.kind === SNAPSHOT_ANALYSIS ? SNAPSHOT_ANALYSIS : SNAPSHOT_SUMMARY
  const analysisId = input.analysisId ? String(input.analysisId) : null
  const filingId = input.filingId ? String(input.filingId) : null
  const companyId = input.companyId ? String(input.companyId) : null
  const generatedAt = normalizeEventDate(input.generatedAt) ?? new Date().toISOString()
  const providedEventId =
    typeof input.eventId === "string" ? input.eventId.trim() : ""

  const inferredEventId =
    providedEventId ||
    (kind === SNAPSHOT_ANALYSIS && analysisId
      ? `analysis:${analysisId}`
      : kind === SNAPSHOT_SUMMARY && filingId
        ? `summary:${filingId}:${generatedAt}`
        : `${kind}:${analysisId ?? filingId ?? generatedAt}`)

  if (!inferredEventId) return null

  return {
    eventId: inferredEventId,
    generatedAt,
    kind,
    analysisId,
    filingId,
    companyId,
  }
}

const sortSummaryEvents = (events: StoredSummaryEvent[]) =>
  [...events].sort(
    (a, b) => getEventTimestamp(b.generatedAt) - getEventTimestamp(a.generatedAt)
  )

const dedupeSummaryEvents = (events: StoredSummaryEvent[]) => {
  if (!events.length) return events
  const seen = new Set<string>()
  const cleaned: StoredSummaryEvent[] = []
  for (const event of sortSummaryEvents(events)) {
    if (!event?.eventId || seen.has(event.eventId)) continue
    seen.add(event.eventId)
    cleaned.push(event)
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

  loadSummaryEvents(userId?: string | null): StoredSummaryEvent[] {
    if (!isBrowser()) return []
    const storageKey = buildStorageKey(SUMMARY_EVENTS_KEY, userId)
    const parsed = safeParse<StoredSummaryEvent[]>(
      window.localStorage.getItem(storageKey),
      [],
    )
    const normalized = parsed
      .map((entry) => normalizeSummaryEvent(entry))
      .filter((entry): entry is StoredSummaryEvent => Boolean(entry))
    const cleaned = dedupeSummaryEvents(normalized).slice(0, MAX_SUMMARY_EVENTS)
    if (cleaned.length !== parsed.length) {
      writeStorage(storageKey, cleaned)
    }
    return cleaned
  },

  appendSummaryEvent(event: Partial<StoredSummaryEvent>, userId?: string | null) {
    if (!isBrowser()) return
    const storageKey = buildStorageKey(SUMMARY_EVENTS_KEY, userId)
    const normalized = normalizeSummaryEvent(event)
    if (!normalized) return
    const existing = DashboardStorage.loadSummaryEvents(userId)
    const updated = dedupeSummaryEvents([normalized, ...existing]).slice(
      0,
      MAX_SUMMARY_EVENTS
    )
    writeStorage(storageKey, updated)
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
