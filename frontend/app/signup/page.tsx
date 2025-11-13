'use client'

import { motion } from 'framer-motion'
import Navbar from '@/components/Navbar'
import LayeredScrollBackground from '@/components/LayeredScrollBackground'
import SignupForm from '@/components/SignupForm'

const signupHighlights = [
  'AI summaries tailored to legendary investor lenses.',
  'Instant KPIs, risk factors, and distribution-ready briefs.',
  'Secure Google sign-in with optional demo workspace.',
]

export default function SignupPage() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-[#050015] text-white">
      <LayeredScrollBackground />
      <div className="relative z-10">
        <Navbar />
        <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
          <motion.div
            className="grid gap-10 rounded-[40px] border border-white/10 bg-white/5 p-8 shadow-[0_35px_120px_rgba(5,0,21,0.55)] lg:grid-cols-[1.1fr,0.9fr]"
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            <div className="space-y-6">
              <p className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-4 py-1 text-xs font-semibold uppercase tracking-[0.4em] text-white/70">
                Early Access
              </p>
              <h1 className="text-4xl font-black text-white leading-tight">
                Sign up in minutes and start briefing investors today.
              </h1>
              <p className="text-lg text-gray-200">
                Bring every SEC filing to life with AI-crafted narratives, actionable KPIs, and cinematic workspaces that teams can trust.
              </p>
              <ul className="space-y-4">
                {signupHighlights.map((item) => (
                  <li key={item} className="flex items-start gap-3 text-gray-200">
                    <span className="mt-1 h-2 w-2 rounded-full bg-primary-300" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
            <SignupForm className="!max-w-full lg:max-w-lg" />
          </motion.div>
        </main>
      </div>
    </div>
  )
}
