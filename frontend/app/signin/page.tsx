'use client'

import { motion } from 'framer-motion'
import { useTheme } from 'next-themes'
import Navbar from '@/components/Navbar'
import LayeredScrollBackground from '@/components/LayeredScrollBackground'
import SigninForm from '@/components/SigninForm'

const signinHighlights = [
  'Pick up where you left off across your investor memos.',
  'Secure auth with Supabase + Stripe subscriptions.',
  'Switch themes anytime in settings.',
]

export default function SigninPage() {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  return (
    <div
      className={`relative min-h-screen overflow-hidden ${
        isDark ? 'bg-[#050015] text-white' : 'bg-white text-slate-900'
      }`}
    >
      {isDark && <LayeredScrollBackground />}
      <div className="relative z-10">
        <Navbar variant={isDark ? 'dark' : 'light'} />
        <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
          <motion.div
            className={`grid gap-10 rounded-[40px] border p-8 lg:grid-cols-[1.1fr,0.9fr] ${
              isDark
                ? 'border-white/10 bg-white/5 shadow-[0_35px_120px_rgba(5,0,21,0.55)]'
                : 'border-border bg-white shadow-[0_25px_80px_rgba(15,23,42,0.12)]'
            }`}
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            <div className="space-y-6">
              <p
                className={`inline-flex items-center gap-2 rounded-full border px-4 py-1 text-xs font-semibold uppercase tracking-[0.4em] ${
                  isDark
                    ? 'border-white/20 bg-white/10 text-white/70'
                    : 'border-slate-200 bg-slate-50 text-slate-500'
                }`}
              >
                Account
              </p>
              <h1 className={`text-4xl font-black leading-tight ${isDark ? 'text-white' : 'text-slate-900'}`}>
                Sign in to FinanceSum.
              </h1>
              <p className={`text-lg ${isDark ? 'text-gray-200' : 'text-slate-600'}`}>
                Manage your filings, summaries, and billing in one place.
              </p>
              <ul className="space-y-4">
                {signinHighlights.map((item) => (
                  <li key={item} className={`flex items-start gap-3 ${isDark ? 'text-gray-200' : 'text-slate-600'}`}>
                    <span className={`mt-1 h-2 w-2 rounded-full ${isDark ? 'bg-primary-300' : 'bg-primary'}`} />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
            <SigninForm className="!max-w-full lg:max-w-lg" />
          </motion.div>
        </main>
      </div>
    </div>
  )
}

