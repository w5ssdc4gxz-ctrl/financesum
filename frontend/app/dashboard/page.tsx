'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  IconBrandTabler,
  IconChartHistogram,
  IconCompass,
  IconMap,
  IconNotebook,
  IconSettings,
  IconSparkles,
  IconUserBolt,
} from '@tabler/icons-react'
import { Sidebar, SidebarBody, SidebarLink } from '@/components/ui/sidebar'
import { useAuth } from '@/contexts/AuthContext'
import CompanySearch from '@/components/CompanySearch'
import GeoImpactMap from '@/components/dashboard/GeoImpactMap'
import useDashboardData from '@/hooks/useDashboardData'
import { scoreToRating } from '@/lib/analysis-insights'
import { Button } from '@/components/base/buttons/button'

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
    label: 'Dashboard',
    href: '/dashboard',
    icon: <IconBrandTabler className="h-5 w-5 shrink-0 text-white/80" />,
  },
  {
    label: 'Company Search',
    href: '#search',
    icon: <IconCompass className="h-5 w-5 shrink-0 text-white/80" />,
  },
  {
    label: 'Investor Personas',
    href: '#personas',
    icon: <IconUserBolt className="h-5 w-5 shrink-0 text-white/80" />,
  },
  {
    label: 'Settings',
    href: '#',
    icon: <IconSettings className="h-5 w-5 shrink-0 text-white/80" />,
  },
]

const gradients = [
  'from-primary-500/30 via-primary-500/10 to-transparent',
  'from-indigo-500/30 via-indigo-500/10 to-transparent',
  'from-pink-500/30 via-pink-500/10 to-transparent',
]

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
  if (ticker) {
    params.set('ticker', ticker)
  }
  if (analysisId) {
    params.set('analysis_id', analysisId)
  }
  const query = params.toString()
  return query ? `/company/${companyId}?${query}` : `/company/${companyId}`
}

export default function DashboardPage() {
  const router = useRouter()
  const { user } = useAuth()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { history, preferences, stats, primaryAnalysis, personaSignals, mapPoints, hasAnalyses } = useDashboardData()
  const rating = scoreToRating(primaryAnalysis?.healthScore)

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
        meta: stats.sectors
          .slice(0, 3)
          .map((item) => item.label)
          .join(', ') || 'Track a few industries',
        icon: IconNotebook,
      },
      {
        label: 'Global spread',
        value: stats.countries.length ? `${stats.countries.length} regions` : '—',
        meta: stats.countries.slice(0, 2).map((item) => item.label).join(', ') || 'Add companies from other markets',
        icon: IconMap,
      },
    ],
    [stats, rating.label, history],
  )

  const handleSelectCompany = (company: Company) => {
    const target = buildCompanyRoute(company.id, company.ticker)
    if (target) {
      router.push(target)
    }
  }

  const latestCompanyId = resolveCompanyId(primaryAnalysis)
  const latestCompanyLink = buildCompanyRoute(latestCompanyId, primaryAnalysis?.ticker, primaryAnalysis?.analysisId)
  const latestPreviewCopy =
    primaryAnalysis?.summaryPreview ||
    primaryAnalysis?.summaryMd ||
    'Run your next analysis to receive an AI-authored executive brief that you can pin to this dashboard.'

  const scrollToSearch = () => {
    if (typeof window === 'undefined') return
    const el = document.getElementById('search')
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const handleReopenLatest = () => {
    const target = buildCompanyRoute(latestCompanyId, primaryAnalysis?.ticker, primaryAnalysis?.analysisId)
    if (target) {
      router.push(target)
    }
  }

  const handleOpenBrief = (snapshot: (typeof history)[number]) => {
    const target = buildCompanyRoute(resolveCompanyId(snapshot), snapshot.ticker, snapshot.analysisId)
    if (target) {
      router.push(target)
    } else {
      alert('Company details are not available for this brief yet.')
    }
  }

  return (
    <div className="relative min-h-screen overscroll-contain overflow-hidden bg-gradient-to-b from-[#04000a] via-[#070017] to-[#0b021f] text-white">
      <div className="pointer-events-none absolute inset-0 opacity-40" aria-hidden>
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(168,85,247,0.35),_transparent_60%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_bottom,_rgba(59,130,246,0.35),_transparent_55%)]" />
      </div>

      <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-col gap-8 px-4 py-10 md:flex-row lg:px-8">
        <Sidebar open={sidebarOpen} setOpen={setSidebarOpen}>
          <SidebarBody className="justify-between gap-10 rounded-2xl border border-white/10 bg-white/5 p-4 text-white backdrop-blur-2xl">
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
                    className="rounded-2xl px-3 py-2 text-sm font-semibold text-white/70 transition hover:bg-white/10 hover:text-white"
                  />
                ))}
              </div>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
              <SidebarLink
                link={{
                  label: user?.user_metadata?.full_name ?? user?.email ?? 'Investor',
                  href: latestCompanyLink ?? '#',
                  icon: (
                    <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-500 to-indigo-500 text-sm font-bold text-white">
                      {(user?.user_metadata?.full_name ?? user?.email ?? 'FS').slice(0, 2).toUpperCase()}
                    </div>
                  ),
                }}
              />
            </div>
          </SidebarBody>
        </Sidebar>

        <main className="flex-1 space-y-8">
          <motion.section
            variants={motionFade}
            initial="hidden"
            animate="visible"
            transition={{ duration: 0.6 }}
            className="relative overflow-hidden rounded-[32px] border border-white/10 bg-gradient-to-br from-[#241247] via-[#120625] to-[#050013] p-8 text-white md:p-12"
          >
            <div className="pointer-events-none absolute inset-y-0 right-0 w-1/2 bg-[radial-gradient(circle_at_top,_rgba(168,85,247,0.25),_transparent_60%)]" aria-hidden />
            <div className="pointer-events-none absolute -bottom-20 -left-10 h-56 w-56 rounded-full bg-primary-500/20 blur-3xl" aria-hidden />
            <div className="relative flex flex-col gap-8">
              <div className="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <p className="text-xs uppercase tracking-[0.5em] text-white/60">FinanceSum Pulse</p>
                  <h1 className="mt-3 text-3xl font-semibold leading-tight md:text-4xl">
                    {hasAnalyses ? 'Here is how your AI sees the market' : 'Run an analysis to light up your dashboard'}
                  </h1>
                  <p className="mt-3 max-w-2xl text-base text-white/75">
                    Track AI summaries of SEC filings, capture your investing preferences, and see where your coverage is concentrated worldwide.
                  </p>
                </div>
                <div className="flex flex-wrap gap-3">
                  <Button color="primary" size="md" onClick={handleReopenLatest} disabled={!latestCompanyLink}>
                    Re-open latest analysis
                  </Button>
                  <Button color="secondary" size="md" onClick={scrollToSearch} className="border border-white/10">
                    Start new summary
                  </Button>
                </div>
              </div>

              <div className="grid gap-6 xl:grid-cols-[1.55fr_1fr]">
                <div className="rounded-[28px] border border-white/10 bg-white/5 p-6 backdrop-blur">
                  <div className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
                    <div className="rounded-2xl border border-white/10 bg-black/30 p-5 text-center">
                      <p className="text-xs uppercase tracking-[0.4em] text-white/60">Grade</p>
                      <p className="mt-2 text-5xl font-black text-white">{rating.grade}</p>
                      <p className="text-sm font-semibold text-primary-200">{rating.label}</p>
                    </div>
                    <div className="space-y-3">
                      <div>
                        <p className="text-xs uppercase tracking-[0.4em] text-white/60">Company</p>
                        <h2 className="mt-2 text-2xl font-semibold text-white">{primaryAnalysis?.name ?? 'Awaiting insights'}</h2>
                        <p className="text-sm text-white/60">{primaryAnalysis?.ticker ?? 'No ticker yet'}</p>
                      </div>
                      <p className="text-sm text-white/70">{rating.description}</p>
                      <div className="rounded-2xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white/70">
                        <p className="text-xs uppercase tracking-[0.4em] text-white/50">Status</p>
                        <p className="mt-1">
                          {hasAnalyses
                            ? 'AI brief refreshed with your latest filing selection.'
                            : 'Generate a summary to populate this space.'}
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="mt-6 rounded-2xl border border-white/10 bg-black/40 p-5">
                    <p className="text-xs uppercase tracking-[0.4em] text-white/60">AI snapshot</p>
                    <p className="mt-2 text-sm text-white/80 line-clamp-4">{latestPreviewCopy}</p>
                  </div>
                </div>

                <div className="rounded-[28px] border border-white/10 bg-black/30 p-6 backdrop-blur">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs uppercase tracking-[0.4em] text-white/60">Your preference DNA</p>
                      <h3 className="mt-2 text-xl font-semibold text-white">Summary style</h3>
                    </div>
                  </div>
                  <dl className="mt-6 space-y-4 text-sm text-white/80">
                    <div className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
                      <dt className="text-white/60">Tone</dt>
                      <dd className="font-semibold text-white">{preferences.tone}</dd>
                    </div>
                    <div className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
                      <dt className="text-white/60">Detail level</dt>
                      <dd className="font-semibold text-white">{preferences.detailLevel}</dd>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
                      <dt className="text-white/60">Focus areas</dt>
                      <dd className="mt-2 flex flex-wrap gap-2">
                        {preferences.focusAreas.length ? (
                          preferences.focusAreas.map((area) => (
                            <span key={area} className="rounded-full border border-white/10 bg-black/40 px-3 py-1 text-xs text-white/80">
                              {area}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs uppercase tracking-[0.4em] text-white/50">None selected</span>
                        )}
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-4 text-xs text-white/60">
                    Target length:{' '}
                    <span className="font-semibold text-white">{preferences.targetLength} words</span>
                  </div>
                </div>
              </div>

              <div
                id="search"
                className="rounded-[28px] border border-dashed border-white/20 bg-gradient-to-r from-[#2f0b4a] to-[#130624] p-6"
              >
                <p className="text-sm font-semibold text-white">Start a new summary</p>
                <p className="text-xs text-white/70">Search any ticker, CIK, or company name.</p>
                <div className="mt-4 rounded-2xl border border-white/10 bg-black/30 p-3">
                  <CompanySearch onSelectCompany={handleSelectCompany} />
                </div>
              </div>
            </div>
          </motion.section>

          <motion.section
            variants={motionFade}
            initial="hidden"
            animate="visible"
            transition={{ delay: 0.1, duration: 0.6 }}
            className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              {statsCards.map((card, idx) => (
                <div
                  key={card.label}
                  className={`rounded-[28px] border border-white/10 bg-gradient-to-br ${gradients[idx % gradients.length]} p-5 backdrop-blur`}
                >
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-white/20 bg-black/30 text-white/80">
                      <card.icon className="h-6 w-6" />
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-[0.3em] text-white/60">{card.label}</p>
                      <p className="text-2xl font-semibold text-white">{card.value}</p>
                      <p className="text-sm text-white/70">{card.meta}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div id="personas" className="rounded-[28px] border border-white/10 bg-white/5 p-6 backdrop-blur">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-white/60">Persona pulse</p>
                  <h3 className="mt-2 text-xl font-semibold text-white">Investor stances</h3>
                </div>
              </div>
              {personaSignals.length ? (
                <ul className="mt-4 space-y-3">
                  {personaSignals.map((persona) => (
                    <li
                      key={persona.personaId}
                      className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-4 py-3"
                    >
                      <div>
                        <p className="text-sm font-semibold text-white">{persona.personaName}</p>
                        <p className="text-xs uppercase tracking-widest text-white/60">{persona.stance}</p>
                      </div>
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-semibold ${
                          persona.stance?.toLowerCase().includes('buy')
                            ? 'bg-emerald-500/20 text-emerald-200'
                            : persona.stance?.toLowerCase().includes('sell')
                              ? 'bg-rose-500/20 text-rose-200'
                              : 'bg-white/10 text-white/70'
                        }`}
                      >
                        {persona.stance}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="mt-6 rounded-2xl border border-dashed border-white/20 bg-white/5 p-6 text-center text-white/60">
                  Run an analysis with personas enabled to see their buy/sell sentiment here.
                </div>
              )}
            </div>
          </motion.section>

          <motion.section
            variants={motionFade}
            initial="hidden"
            animate="visible"
            transition={{ delay: 0.2, duration: 0.6 }}
            className="grid gap-6 lg:grid-cols-[1.6fr_0.9fr]"
          >
            <div className="rounded-[32px] border border-white/10 bg-white/5 p-6 backdrop-blur">
              {mapPoints.length ? (
                <GeoImpactMap points={mapPoints} />
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-4 text-white/60">
                  <IconMap className="h-10 w-10" />
                  <p className="max-w-sm text-center">
                    Once you have summaries across multiple countries, we will spotlight them on a live map with logos and rating callouts.
                  </p>
                </div>
              )}
            </div>
            <div className="rounded-[32px] border border-white/10 bg-white/5 p-6 backdrop-blur">
              <div className="flex items-center justify-between">
                <h3 className="text-xl font-semibold text-white">Recent briefs</h3>
                <span className="text-xs uppercase tracking-[0.3em] text-white/60">Live sync</span>
              </div>
              {history.length ? (
                <ul className="mt-6 space-y-4">
                  {history.slice(0, 5).map((item) => {
                    const itemRating = scoreToRating(item.healthScore)
                    return (
                      <li key={item.analysisId}>
                        <button
                          type="button"
                          onClick={() => handleOpenBrief(item)}
                          className="w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-left transition hover:border-primary-500/50 hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                        >
                          <div className="flex items-center justify-between">
                            <div>
                              <p className="text-sm font-semibold text-white">{item.name}</p>
                              <p className="text-xs uppercase tracking-widest text-white/60">{item.ticker}</p>
                            </div>
                            <span className="text-xs text-white/50">{relativeTime(item.generatedAt)}</span>
                          </div>
                          <div className="mt-2 flex items-center gap-3 text-sm text-white/70">
                            <span className="rounded-full border border-white/20 px-3 py-1 text-xs font-semibold text-white/80">
                              {itemRating.grade} · {itemRating.label}
                            </span>
                            {item.summaryPreview && (
                              <span className="line-clamp-2 text-xs text-white/60">{item.summaryPreview}</span>
                            )}
                          </div>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <div className="mt-6 rounded-2xl border border-dashed border-white/20 bg-white/5 p-6 text-center text-white/60">
                  Your most recent AI briefs will show up here right after you run them.
                </div>
              )}
            </div>
          </motion.section>
        </main>
      </div>
    </div>
  )
}

const SidebarLogo = ({ open }: { open: boolean }) => {
  return (
    <a href="#" className="flex items-center gap-3">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-black text-white">
        <IconBrandTabler className="h-6 w-6" />
      </div>
      {open && (
        <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-lg font-semibold text-white">
          FinanceSum
        </motion.span>
      )}
    </a>
  )
}
