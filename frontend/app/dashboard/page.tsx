'use client'

import { ReactNode, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  IconBrandTabler,
  IconChartHistogram,
  IconCompass,
  IconMap,
  IconNotebook,
  IconSparkles,
  IconUserBolt,
  IconTrendingUp,
  IconBuilding,
  IconFileAnalytics,
  IconWorld,
} from '@tabler/icons-react'
import { Sidebar, SidebarBody, SidebarLink } from '@/components/ui/sidebar'
import { useAuth } from '@/contexts/AuthContext'
import CompanySearch from '@/components/CompanySearch'
import GeoImpactMap from '@/components/dashboard/GeoImpactMap'
import { ProgressBar } from '@/components/dashboard/ui/ProgressBar'
import { ProgressCircle } from '@/components/dashboard/ui/ProgressCircle'
import { StatCard } from '@/components/dashboard/ui/StatCard'
import { BarList, type BarListItem } from '@/components/dashboard/ui/BarList'
import { DonutChart, type DonutDataPoint } from '@/components/dashboard/ui/DonutChart'
import AnalysisTrend from '@/components/dashboard/charts/AnalysisTrend'
import SectorStack from '@/components/dashboard/charts/SectorStack'
import useDashboardData from '@/hooks/useDashboardData'
import { scoreToRating } from '@/lib/analysis-insights'
import { Button } from '@/components/base/buttons/button'
import DashboardContent from './DashboardContent'

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

const motionFade = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

const navLinks = [
  {
    label: 'Home',
    href: '/dashboard',
    icon: <IconBrandTabler className="h-5 w-5 shrink-0 text-slate-600" />,
  },
  {
    label: 'Market Pulse',
    href: '#overview',
    icon: <IconSparkles className="h-5 w-5 shrink-0 text-slate-600" />,
  },
  {
    label: 'Workflow',
    href: '#workflow',
    icon: <IconCompass className="h-5 w-5 shrink-0 text-slate-600" />,
  },
  {
    label: 'Coverage',
    href: '#coverage',
    icon: <IconMap className="h-5 w-5 shrink-0 text-slate-600" />,
  },
  {
    label: 'Personas',
    href: '#personas',
    icon: <IconUserBolt className="h-5 w-5 shrink-0 text-slate-600" />,
  },
]

const PIN_STORAGE_KEY = 'financesum.dashboardPins'

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

export default function DashboardPage() {
  const router = useRouter()
  const { user } = useAuth()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [pinnedBriefs, setPinnedBriefs] = useState<Record<string, boolean>>({})
  const {
    history,
    preferences,
    stats,
    primaryAnalysis,
    personaSignals,
    mapPoints,
    hasAnalyses,
    removeHistoryEntry,
  } = useDashboardData()
  const rating = scoreToRating(primaryAnalysis?.healthScore)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const loadPins = () => {
      try {
        const stored = window.localStorage.getItem(PIN_STORAGE_KEY)
        if (stored) {
          setPinnedBriefs(JSON.parse(stored))
        } else {
          setPinnedBriefs({})
        }
      } catch {
        setPinnedBriefs({})
      }
    }
    loadPins()
    const handleStorage = (event: StorageEvent) => {
      if (event.key === PIN_STORAGE_KEY) {
        loadPins()
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  const statsCards = useMemo(
    () => [
      {
        label: 'Analyses completed',
        value: stats.analysisCount,
        meta: stats.analysisCount ? `${relativeTime(history[0]?.generatedAt)} latest` : 'Run your first analysis',
        icon: IconSparkles,
      },
      {
        label: 'Average health score',
        value: typeof stats.avgScore === 'number' ? `${stats.avgScore}` : '—',
        meta: stats.avgScore ? rating.label : 'No score yet',
        icon: IconChartHistogram,
      },
      {
        label: 'Top sectors',
        value: stats.sectors.length ? stats.sectors[0].label : '—',
        meta:
          stats.sectors
            .slice(0, 3)
            .map((item) => item.label)
            .join(', ') || 'Track a few industries',
        icon: IconNotebook,
      },
      {
        label: 'Global spread',
        value: stats.countries.length ? `${stats.countries.length} regions` : '—',
        meta:
          stats.countries.slice(0, 2).map((item) => item.label).join(', ') || 'Add companies from other markets',
        icon: IconMap,
      },
    ],
    [stats, rating.label, history],
  )

  const pinnedCount = useMemo(() => Object.values(pinnedBriefs).filter(Boolean).length, [pinnedBriefs])
  const averageScore = typeof stats.avgScore === 'number' ? stats.avgScore : 0
  const completionTarget = 12
  const topRegions = stats.countries.slice(0, 3)
  const analysisTrendData = useMemo(() => {
    const dailyCounts: Record<string, number> = {}
    history.forEach((item) => {
      if (!item.generatedAt) return
      const date = new Date(item.generatedAt)
      const key = `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`
      dailyCounts[key] = (dailyCounts[key] ?? 0) + 1
    })
    const sorted = Object.entries(dailyCounts)
      .map(([key, value]) => {
        const [year, month, day] = key.split('-').map(Number)
        const date = new Date(year, month - 1, day)
        return {
          date,
          label: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
          value,
        }
      })
      .sort((a, b) => a.date.getTime() - b.date.getTime())
      .slice(-8)
    return sorted
  }, [history])

  const sectorStackData = useMemo(() => {
    const total = stats.sectors.reduce((sum, item) => sum + item.value, 0) || 1
    return stats.sectors.slice(0, 4).map((sector) => ({
      label: sector.label,
      value: sector.value,
      percent: Math.round((sector.value / total) * 100),
    }))
  }, [stats.sectors])

  const handleSelectCompany = (company: Company) => {
    const target = buildCompanyRoute(company.id, company.ticker)
    if (target) router.push(target)
  }

  const latestCompanyId = resolveCompanyId(primaryAnalysis)
  const latestCompanyLink = buildCompanyRoute(latestCompanyId, primaryAnalysis?.ticker, primaryAnalysis?.analysisId)
  const latestPreviewCopy =
    primaryAnalysis?.summaryPreview ||
    primaryAnalysis?.summaryMd ||
    'Run your next analysis to receive an AI-authored executive brief that you can pin to this dashboard.'

  const scrollToSearch = () => {
    if (typeof window === 'undefined') return
    document.getElementById('search')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const handleReopenLatest = () => {
    const target = buildCompanyRoute(latestCompanyId, primaryAnalysis?.ticker, primaryAnalysis?.analysisId)
    if (target) router.push(target)
  }

  const handleOpenBrief = (snapshot: (typeof history)[number]) => {
    const target = buildCompanyRoute(resolveCompanyId(snapshot), snapshot.ticker, snapshot.analysisId)
    if (target) {
      router.push(target)
    } else {
      alert('Company details are not available for this brief yet.')
    }
  }

  const heroStats = [
    {
      label: 'Pinned briefs',
      value: pinnedCount,
      detail: 'Ready for investor updates',
    },
    {
      label: 'Latest',
      value: primaryAnalysis?.ticker ?? '—',
      detail: relativeTime(primaryAnalysis?.generatedAt),
    },
    {
      label: 'Coverage',
      value: stats.countries.length || 0,
      detail: stats.countries.length === 1 ? 'Region tracked' : 'Regions tracked',
    },
  ]

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-50">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-8 md:flex-row lg:px-8">
        <Sidebar open={sidebarOpen} setOpen={setSidebarOpen}>
          <SidebarBody className="justify-between gap-8 rounded-xl border border-gray-200 bg-white p-4 text-gray-900 shadow-sm dark:border-gray-800 dark:bg-gray-950 dark:text-gray-50">
            <div className="flex flex-1 flex-col overflow-hidden">
              <SidebarLogo open={sidebarOpen} />
              <div className="mt-8 flex flex-col gap-2">
                {navLinks.map((link, idx) => (
                  <SidebarLink
                    key={link.label}
                    link={{
                      ...link,
                      icon: (
                        <motion.div
                          initial={{ opacity: 0, x: -4 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: 0.05 * idx }}
                        >
                          {link.icon}
                        </motion.div>
                      ),
                    }}
                    className="rounded-lg px-3 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-900 dark:hover:text-gray-50"
                  />
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-900">
              <SidebarLink
                link={{
                  label: user?.user_metadata?.full_name ?? user?.email ?? 'Investor',
                  href: latestCompanyLink ?? '#',
                  icon: (
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-600 text-sm font-bold text-white">
                      {(user?.user_metadata?.full_name ?? user?.email ?? 'FS').slice(0, 2).toUpperCase()}
                    </div>
                  ),
                }}
              />
            </div>
          </SidebarBody>
        </Sidebar>

                <main className="flex-1">
          <DashboardContent
            dashboardData={{
              history,
              stats,
              primaryAnalysis,
              hasAnalyses,
              mapPoints,
              preferences
            }}
            onRemoveAnalysis={removeHistoryEntry}
          />
        </main>
      </div>
    </div>
  )
}

const SidebarLogo = ({ open }: { open: boolean }) => {
  return (
    <a href="#" className="flex items-center gap-3">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-600 text-white">
        <IconBrandTabler className="h-6 w-6" />
      </div>
      {open && (
        <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-lg font-semibold text-slate-900">
          FinanceSum
        </motion.span>
      )}
    </a>
  )
}

interface SectionHeaderProps {
  eyebrow?: string
  title: string
  description?: string
  action?: ReactNode
}

const SectionHeader = ({ eyebrow, title, description, action }: SectionHeaderProps) => {
  return (
    <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
      <div>
        {eyebrow && <p className="text-xs uppercase tracking-[0.3em] text-slate-400">{eyebrow}</p>}
        <h3 className="text-xl font-semibold text-slate-900">{title}</h3>
        {description && <p className="text-sm text-slate-500">{description}</p>}
      </div>
      {action && <div className="text-sm text-slate-500 md:text-right">{action}</div>}
    </div>
  )
}



