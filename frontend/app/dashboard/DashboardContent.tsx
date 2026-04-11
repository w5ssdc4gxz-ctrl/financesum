'use client'

import { useMemo, type MouseEvent } from 'react'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  IconChartHistogram,
  IconSparkles,
  IconBuilding,
  IconWorld,
  IconFileAnalytics,
  IconMapPin,
  IconChartBar,
  IconActivity,
} from '@tabler/icons-react'
import CompanySearch from '@/components/CompanySearch'
import { StatCard } from '@/components/dashboard/ui/StatCard'
import { BarList, type BarListItem } from '@/components/dashboard/ui/BarList'
import { SectorPerformanceList, type SectorPerformanceItem } from '@/components/dashboard/ui/SectorPerformanceList'
import { DonutChart, type DonutDataPoint } from '@/components/dashboard/ui/DonutChart'
import AnalysisTrend from '@/components/dashboard/charts/AnalysisTrend'
import PerformanceGauge from '@/components/dashboard/charts/PerformanceGauge'
import ActivityHeatmap from '@/components/dashboard/charts/ActivityHeatmap'
import InteractiveWorldMap, { type MapDataPoint } from '@/components/dashboard/charts/InteractiveWorldMap'
import EnhancedBarChart, { type EnhancedBarDataPoint } from '@/components/dashboard/charts/EnhancedBarChart'
import { CompanyLogo } from '@/components/CompanyLogo'
import { scoreToRating } from '@/lib/analysis-insights'
import { getCountryCoordinates } from '@/lib/mapUtils'

interface Company {
  id: string
  ticker: string
  name: string
  exchange?: string
}

type CompanyIdentifier = {
  id?: string | null
  company_id?: string | null
  companyId?: string | null
}

const resolveCompanyId = (entry?: CompanyIdentifier | null) =>
  entry?.id ?? entry?.company_id ?? entry?.companyId ?? null

const relativeTime = (value?: string | null) => {
  if (!value) return '—'
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return '—'
  const delta = Date.now() - timestamp
  const minutes = Math.floor(delta / 60000)
  if (minutes < 60) return `${minutes || 1}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  const weeks = Math.floor(days / 7)
  return `${weeks}w ago`
}

const buildCompanyRoute = (companyId?: string | null, ticker?: string | null, analysisId?: string | null) => {
  if (!companyId) return null
  const params = new URLSearchParams()
  if (ticker) params.set('ticker', ticker)
  if (analysisId) params.set('analysis_id', analysisId)
  const query = params.toString()
  return query ? `/company/${companyId}?${query}` : `/company/${companyId}`
}

const sectorColors = ['#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#ec4899', '#06b6d4', '#f97316', '#a855f7']

const getSentimentColor = (sentiment: 'bullish' | 'neutral' | 'bearish'): string => {
  switch (sentiment) {
    case 'bullish':
      return '#10b981'
    case 'bearish':
      return '#ef4444'
    default:
      return '#6b7280'
  }
}

export default function DashboardContent({
  dashboardData,
  onRemoveAnalysis,
}: {
  dashboardData: any
  onRemoveAnalysis?: (analysisId: string) => Promise<void> | void
}) {
  const router = useRouter()
  const { history, stats, primaryAnalysis, hasAnalyses } = dashboardData
  const rating = scoreToRating(primaryAnalysis?.healthScore)

  const handleSelectCompany = (company: Company) => {
    const target = buildCompanyRoute(company.id, company.ticker)
    if (target) router.push(target)
  }

  const handleOpenBrief = (snapshot: any) => {
    const target = buildCompanyRoute(resolveCompanyId(snapshot), snapshot.ticker, snapshot.analysisId)
    if (target) router.push(target)
  }

  const handleAnalyzeAgain = async (snapshot: any, event?: MouseEvent) => {
    event?.stopPropagation()

    const identifier = snapshot.analysisId ?? snapshot.analysis_id ?? snapshot.id
    if (identifier && typeof onRemoveAnalysis === 'function') {
      await onRemoveAnalysis(identifier)
    }

    const companyId = resolveCompanyId(snapshot)
    if (!companyId) return
    const target = buildCompanyRoute(companyId, snapshot.ticker)
    if (target) router.push(target)
  }

  const handleRemoveSnapshot = (snapshot: any, event?: MouseEvent) => {
    event?.stopPropagation()
    const identifier = snapshot.analysisId ?? snapshot.analysis_id ?? snapshot.id
    if (identifier && typeof onRemoveAnalysis === 'function') {
      onRemoveAnalysis(identifier)
    }
  }

  const formatFilingMeta = (snapshot: any) => {
    const type = snapshot.filingType ?? snapshot.filing_type
    const rawDate = snapshot.filingDate ?? snapshot.filing_date
    let formattedDate: string | null = null
    if (rawDate) {
      const parsed = new Date(rawDate)
      formattedDate = Number.isNaN(parsed.getTime())
        ? null
        : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    }
    if (!type && !formattedDate) return null
    if (type && formattedDate) {
      return `${type} • ${formattedDate}`
    }
    return type ?? formattedDate
  }

  // Calculate overall health score (average)
  const overallHealthScore = useMemo(() => {
    if (!history.length) return 0
    const validScores = history
      .map((item: any) => item.healthScore ?? item.health_score)
      .filter((score: any) => typeof score === 'number' && score > 0)
    if (validScores.length === 0) return 0
    return Math.round(validScores.reduce((sum: number, score: number) => sum + score, 0) / validScores.length)
  }, [history])

  // Prepare analysis trend data
  // Prefer backend-provided summary activity so removals from dashboard don't reduce the counts.
  const summaryActivity = stats?.summary_activity
  const analysisTrendData = useMemo(() => {
    const activity = Array.isArray(summaryActivity) ? summaryActivity : null
    if (activity && activity.length) {
      const parseActivityDate = (value: any) => {
        if (typeof value === 'string') {
          const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
          if (match) {
            const [, year, month, day] = match
            return new Date(Number(year), Number(month) - 1, Number(day))
          }
        }
        return new Date(value)
      }

      return activity.map((row: any) => {
        const parsed = parseActivityDate(row.date)
        const date = Number.isNaN(parsed.getTime()) ? new Date() : parsed
        return {
          date,
          label: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
          value: Number(row.count) || 0,
        }
      })
    }

    // Fallback: count from current history ONLY if backend activity data is completely missing
    const dailyCounts: Record<string, number> = {}
    history.forEach((item: any) => {
      const dateStr = item.generatedAt ?? item.generated_at
      if (!dateStr) return

      let date = new Date(dateStr)
      if (Number.isNaN(date.getTime())) {
        // If it's a relative time like "48m ago" or unparseable, just use today
        date = new Date()
      }

      const key = `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
      dailyCounts[key] = (dailyCounts[key] ?? 0) + 1
    })

    // Fill in last 8 days to ensure the chart always looks populated
    const fallbackSorted: { date: Date; label: string; value: number }[] = []
    const today = new Date()
    for (let i = 7; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(today.getDate() - i)
      const key = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
      fallbackSorted.push({
        date: d,
        label: d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
        value: dailyCounts[key] || 0,
      })
    }
    return fallbackSorted
  }, [history, summaryActivity])

  // Prepare sector data
  const { sectorBarData, sectorDonutData, sectorPerformanceData } = useMemo(() => {
    // Build a map of sector -> { tickers, healthScores }
    const sectorStatsMap = new Map<string, { tickers: Set<string>, scores: number[] }>()

    history.forEach((item: any) => {
      const sector = item.sector || item.companySector
      const score = item.healthScore ?? item.health_score

      if (sector) {
        if (!sectorStatsMap.has(sector)) {
          sectorStatsMap.set(sector, { tickers: new Set(), scores: [] })
        }
        const entry = sectorStatsMap.get(sector)!
        if (item.ticker) entry.tickers.add(item.ticker)
        if (typeof score === 'number') entry.scores.push(score)
      }
    })

    // Helper to get avg score
    const getAvgScore = (scores: number[]) =>
      scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0

    const barData: BarListItem[] = stats.sectors.slice(0, 5).map((sector: any, idx: number) => ({
      name: sector.label,
      value: sector.value,
      color: sectorColors[idx % sectorColors.length],
    }))

    const donutData: DonutDataPoint[] = stats.sectors.slice(0, 6).map((sector: any, idx: number) => ({
      label: sector.label,
      value: sector.value,
      color: sectorColors[idx % sectorColors.length],
      tickers: Array.from(sectorStatsMap.get(sector.label)?.tickers || [])
    }))

    const performanceData: SectorPerformanceItem[] = stats.sectors.slice(0, 5).map((sector: any, idx: number) => {
      const stats = sectorStatsMap.get(sector.label)
      return {
        name: sector.label,
        count: sector.value,
        avgScore: stats ? getAvgScore(stats.scores) : 0,
        topTickers: Array.from(stats?.tickers || []),
        color: sectorColors[idx % sectorColors.length]
      }
    })

    return { sectorBarData: barData, sectorDonutData: donutData, sectorPerformanceData: performanceData }
  }, [stats.sectors, history])

  // Prepare map data
  const mapData = useMemo(() => {
    const countryMap = new Map<string, { name: string; count: number; tickers: Set<string> }>()

    history.forEach((item: any) => {
      const country = item.country || item.companyCountry
      if (country) {
        const existing = countryMap.get(country)
        if (existing) {
          existing.count++
          if (item.ticker) existing.tickers.add(item.ticker)
        } else {
          countryMap.set(country, {
            name: country,
            count: 1,
            tickers: new Set(item.ticker ? [item.ticker] : [])
          })
        }
      }
    })

    const mapPoints: MapDataPoint[] = []
    countryMap.forEach((data, country) => {
      const coords = getCountryCoordinates(country)
      if (coords) {
        mapPoints.push({
          name: data.name,
          coordinates: coords,
          value: data.count,
          tickers: Array.from(data.tickers),
          country
        })
      }
    })

    return mapPoints
  }, [history])

  // Prepare heatmap data
  const heatmapData = useMemo(() => {
    const dateMap = new Map<string, number>()
    history.forEach((item: any) => {
      const dateStr = item.generatedAt ?? item.generated_at
      if (!dateStr) return
      const isoDate = new Date(dateStr).toISOString().split('T')[0]
      dateMap.set(isoDate, (dateMap.get(isoDate) || 0) + 1)
    })

    return Array.from(dateMap.entries()).map(([date, count]) => ({
      date,
      count
    }))
  }, [history])

  // Prepare top companies data
  const topCompaniesData = useMemo(() => {
    const companyMap = new Map<string, { name: string; ticker: string; score: number }>()
    history.forEach((item: any) => {
      const healthScore = item.healthScore ?? item.health_score
      if (item.ticker && healthScore) {
        const existing = companyMap.get(item.ticker)
        if (!existing || healthScore > existing.score) {
          companyMap.set(item.ticker, {
            name: item.companyName || item.company_name || item.name || item.ticker,
            ticker: item.ticker,
            score: healthScore
          })
        }
      }
    })

    return Array.from(companyMap.values())
      .sort((a, b) => b.score - a.score)
      .slice(0, 5)
      .map((company, idx) => ({
        name: company.name,
        ticker: company.ticker,
        value: company.score,
        color: sectorColors[idx % sectorColors.length]
      }))
  }, [history])

  // Recent analyses
  const recentAnalyses = useMemo(() => {
    if (!Array.isArray(history) || history.length === 0) return []
    const seenCompanies = new Set<string>()
    const deduped: any[] = []
    for (const entry of history) {
      if (!entry) continue
      const snapshotSource = entry.source ?? (typeof entry.analysisId === 'string' && entry.analysisId.startsWith('summary-') ? 'summary' : 'analysis')
      if (snapshotSource !== 'summary') {
        const dedupeKeyRaw = resolveCompanyId(entry) ?? entry.ticker ?? entry.analysisId ?? entry.analysis_id
        const dedupeKey = dedupeKeyRaw != null ? String(dedupeKeyRaw) : null
        if (dedupeKey) {
          if (seenCompanies.has(dedupeKey)) {
            continue
          }
          seenCompanies.add(dedupeKey)
        }
      }
      deduped.push(entry)
      if (deduped.length >= 6) break
    }
    return deduped
  }, [history])

  const uniqueCompaniesCount = new Set(history.map((h: any) => h.ticker)).size

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-10 p-8 lg:p-10 bg-white dark:bg-zinc-950 border border-black dark:border-white">
        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-4xl lg:text-5xl font-black uppercase tracking-tighter text-black dark:text-white"
        >
          Dashboard
        </motion.h1>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
          className="mt-4 text-sm font-bold tracking-widest uppercase text-zinc-500 dark:text-zinc-400"
        >
          {hasAnalyses ? 'Track your portfolio insights and analysis trends' : 'Get started by analyzing your first company'}
        </motion.p>
      </div>

      {hasAnalyses ? (
        <div className="space-y-10">
          {/* Company Search - Always Visible */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="relative z-20 rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8 lg:p-10"
          >
            <h3 className="mb-6 flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
              <IconSparkles className="h-5 w-5 text-black dark:text-white" />
              Analyze New Company
            </h3>
            <CompanySearch onSelectCompany={handleSelectCompany} />
            <p className="mt-5 text-xs font-bold tracking-widest uppercase text-zinc-500 dark:text-zinc-400">
              Search by company name or ticker symbol to create a new analysis
            </p>
          </motion.div>

          {/* Overview Stats */}
          <motion.div
            id="overview"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-50px" }}
            transition={{ delay: 0.05, duration: 0.5 }}
            className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4 scroll-mt-8"
          >
            <StatCard
              title="Total Summaries"
              value={Math.max(stats?.total_summaries || 0, history.length) || 0}
              icon={<IconSparkles className="h-5 w-5" />}
              description={
                history[0] && (history[0].generatedAt ?? history[0].generated_at)
                  ? `Latest ${relativeTime(history[0].generatedAt ?? history[0].generated_at)}`
                  : 'No summaries yet'
              }
            />
            <StatCard
              title="Average Health"
              value={overallHealthScore || 0}
              icon={<IconChartHistogram className="h-5 w-5" />}
              description={rating.label}
            />
            <StatCard
              title="Companies"
              value={uniqueCompaniesCount || 0}
              icon={<IconBuilding className="h-5 w-5" />}
              description={`${(stats?.sectors && stats.sectors.length) || 0} sectors`}
            />
            <StatCard
              title="Regions"
              value={(stats?.countries && stats.countries.length) || 0}
              icon={<IconWorld className="h-5 w-5" />}
              description="Countries tracked"
            />
          </motion.div>

          {/* Performance & Map Row */}
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
            {/* Performance Gauge */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8"
            >
              <h3 className="mb-4 text-center text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                Portfolio Health
              </h3>
              <PerformanceGauge
                value={overallHealthScore}
                label="Overall"
                subtitle={`${history.length} ${history.length === 1 ? 'analysis' : 'analyses'}`}
                size={200}
              />
            </motion.div>

            {/* World Map */}
            <motion.div
              id="coverage"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ delay: 0.1, duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8 lg:col-span-2 scroll-mt-8"
            >
              <h3 className="mb-4 flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                <IconMapPin className="h-4 w-4 text-black dark:text-white" />
                Global Coverage
              </h3>
              <InteractiveWorldMap data={mapData} height={280} />
            </motion.div>
          </div>

          {/* Charts Row - Activity, Heatmap */}
          <div id="activity" className="grid grid-cols-1 gap-8 lg:grid-cols-2 scroll-mt-8">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8"
            >
              <h3 className="mb-2 flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                <IconActivity className="h-4 w-4 text-black dark:text-white" />
                Analysis Activity
              </h3>
              <p className="mb-4 text-xs font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">Last 8 days</p>
              <div className="h-[300px]">
                <AnalysisTrend data={analysisTrendData} />
              </div>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ delay: 0.05, duration: 0.5 }}
              className="flex flex-col justify-between rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8"
            >
              <div>
                <h3 className="mb-2 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                  Activity Calendar
                </h3>
                <p className="mb-4 text-xs font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400">
                  12-week analysis frequency
                </p>
              </div>
              <ActivityHeatmap data={heatmapData} weeks={12} colorScheme="blue" />
            </motion.div>
          </div>

          {/* Sectors & Companies Row */}
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
            {/* Sector Distribution */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8"
            >
              <h3 className="mb-4 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                Sector Distribution
              </h3>
              <div className="flex justify-center">
                <DonutChart data={sectorDonutData} />
              </div>
            </motion.div>

            {/* Top Sectors */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ delay: 0.05, duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8"
            >
              <h3 className="mb-4 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                Sector Insights
              </h3>
              {sectorPerformanceData.length > 0 ? (
                <SectorPerformanceList data={sectorPerformanceData} showAnimation={true} />
              ) : (
                <div className="flex h-40 items-center justify-center text-sm text-gray-500 dark:text-gray-400">
                  Analyze companies to see sector breakdown
                </div>
              )}
            </motion.div>

            {/* Top Companies */}
            <motion.div
              id="companies"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ delay: 0.1, duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8 scroll-mt-8"
            >
              <h3 className="mb-4 flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                <IconChartBar className="h-4 w-4 text-black dark:text-white" />
                Top Companies
              </h3>
              {topCompaniesData.length > 0 ? (
                <EnhancedBarChart
                  data={topCompaniesData}
                  height={220}
                  valueFormatter={(v) => `${Math.round(v)}`}
                />
              ) : (
                <div className="flex h-40 items-center justify-center text-sm text-gray-500 dark:text-gray-400">
                  Your top companies will appear here
                </div>
              )}
            </motion.div>
          </div>

          {/* Recent Analyses */}
          {recentAnalyses.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.5 }}
              className="rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 p-8"
            >
              <h3 className="mb-6 text-sm font-bold uppercase tracking-widest text-black dark:text-white">Recent Analyses</h3>
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {recentAnalyses.map((analysis: any, idx: number) => {
                  const healthScore = analysis.healthScore ?? analysis.health_score
                  const analysisRating = scoreToRating(healthScore)
                  const generatedAt = analysis.generatedAt ?? analysis.generated_at
                  return (
                    <motion.div
                      key={analysis.analysisId || analysis.analysis_id || idx}
                      initial={{ opacity: 0, x: -10 }}
                      whileInView={{ opacity: 1, x: 0 }}
                      viewport={{ once: true, margin: "-20px" }}
                      transition={{ delay: idx * 0.05, duration: 0.4 }}
                      onClick={() => handleOpenBrief(analysis)}
                      className="group flex flex-col cursor-pointer rounded-none border border-black dark:border-white bg-white p-4 transition-all hover:bg-black hover:text-white dark:bg-zinc-950 dark:hover:bg-white dark:hover:text-black"
                    >
                      <div className="flex items-start justify-between">
                        {/* Company Logo */}
                        <div className="mr-3 h-10 w-10 flex-shrink-0 overflow-hidden rounded-none border border-black dark:border-white bg-white p-1">
                          <CompanyLogo ticker={analysis.ticker} className="h-full w-full" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="truncate text-sm font-black uppercase tracking-tight group-hover:text-white dark:group-hover:text-black">
                            {analysis.ticker}
                          </p>
                          <p className="mt-0.5 truncate text-xs font-medium uppercase tracking-widest text-zinc-500 group-hover:text-zinc-400 dark:group-hover:text-zinc-500">
                            {analysis.companyName || analysis.company_name || analysis.name}
                          </p>
                          {formatFilingMeta(analysis) && (
                            <p className="mt-2 text-[10px] font-bold uppercase tracking-widest text-zinc-400 group-hover:text-zinc-300 dark:group-hover:text-zinc-600">
                              {formatFilingMeta(analysis)}
                            </p>
                          )}
                          <p className="mt-1 text-[10px] font-bold uppercase tracking-widest text-zinc-400 group-hover:text-zinc-300 dark:group-hover:text-zinc-600">
                            {relativeTime(generatedAt)}
                          </p>
                        </div>
                        <div className="ml-3 flex-shrink-0">
                          <div className="flex flex-col items-center gap-1 border border-black dark:border-white p-1">
                            <div className="flex h-8 w-8 items-center justify-center bg-transparent group-hover:bg-white dark:group-hover:bg-black">
                              <span className="text-sm font-black tracking-tighter" style={{ color: getSentimentColor(analysisRating.sentiment) }}>
                                {typeof healthScore === 'number' ? Math.round(healthScore) : '—'}
                              </span>
                            </div>
                            <span className="text-[9px] font-bold uppercase tracking-widest" style={{ color: getSentimentColor(analysisRating.sentiment) }}>
                              {analysisRating.label}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="mt-auto pt-6 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={(event) => {
                            event?.stopPropagation()
                            const companyId = resolveCompanyId(analysis)
                            const target = buildCompanyRoute(companyId, analysis.ticker, analysis.analysisId ?? analysis.analysis_id)
                            if (target) router.push(target)
                          }}
                          className="rounded-none border border-black dark:border-white px-3 py-2 text-[10px] font-bold uppercase tracking-widest transition group-hover:bg-white group-hover:text-black dark:group-hover:bg-black dark:group-hover:text-white hover:opacity-75"
                        >
                          Open analysis
                        </button>
                        <button
                          type="button"
                          onClick={(event) => handleAnalyzeAgain(analysis, event)}
                          className="rounded-none border border-black dark:border-white px-3 py-2 text-[10px] font-bold uppercase tracking-widest transition group-hover:bg-white group-hover:text-black dark:group-hover:bg-black dark:group-hover:text-white hover:opacity-75"
                        >
                          Run again
                        </button>
                        <button
                          type="button"
                          onClick={(event) => handleRemoveSnapshot(analysis, event)}
                          className="rounded-none border border-red-500 px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-red-500 transition hover:bg-red-500 hover:text-white dark:hover:bg-red-500 dark:hover:text-white"
                        >
                          Remove
                        </button>
                      </div>
                    </motion.div>
                  )
                })}
              </div>
            </motion.div>
          )}
        </div>
      ) : (
        /* Empty State - No Analyses Yet */
        <div className="flex min-h-[60vh] items-center justify-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="max-w-md text-center"
          >
            <div className="mx-auto mb-6 flex h-24 w-24 items-center justify-center rounded-none border-2 border-dashed border-black bg-white shadow-[4px_4px_0_0_#000] dark:border-white dark:bg-black dark:shadow-[4px_4px_0_0_#fff]">
              <IconFileAnalytics className="h-12 w-12 text-black dark:text-white" strokeWidth={1} />
            </div>
            <p className="mb-8 text-sm font-bold uppercase tracking-widest text-zinc-500">
              Start analyzing companies to see your portfolio insights, trends, and global coverage here.
            </p>
            <div className="rounded-none border border-black dark:border-white bg-white p-8 dark:bg-zinc-950">
              <h3 className="mb-6 text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                Analyze Your First Company
              </h3>
              <CompanySearch onSelectCompany={handleSelectCompany} />
              <p className="mt-5 text-xs font-bold tracking-widest uppercase text-zinc-500 dark:text-zinc-400">
                Search by company name or ticker symbol
              </p>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  )
}
