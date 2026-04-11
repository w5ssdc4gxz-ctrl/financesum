'use client'

import { useRef } from 'react'
import Link from 'next/link'

import { useRouter } from 'next/navigation'
import { motion, useScroll, useTransform } from 'framer-motion'
import { useAuth } from '@/contexts/AuthContext'
import Navbar from '@/components/Navbar'
import MinimalFooter from '@/components/MinimalFooter'
import BottomPopup from '@/components/BottomPopup'
import { FAQChat } from '@/components/FAQChat'
import PricingSection from '@/components/PricingSection'
import { LogoLoop } from '@/components/fancy'
import FloatingObjectsHero from '@/components/FloatingObjectsHero'
import ScrollVideo from '@/components/ScrollVideo'

// Rotating words for the hero
const rotatingWords = ['reimagined.', 'simplified.', 'automated.', 'transformed.']

// Enterprise customer logos
const enterpriseLogos = [
  { node: <span className="font-bold tracking-tight">BlackRock</span>, title: 'BlackRock' },
  { node: <span className="font-bold tracking-tight">JP Morgan</span>, title: 'JP Morgan' },
  { node: <span className="font-bold tracking-tight">Goldman Sachs</span>, title: 'Goldman Sachs' },
  { node: <span className="font-bold tracking-tight">Morgan Stanley</span>, title: 'Morgan Stanley' },
  { node: <span className="font-bold tracking-tight">Citadel</span>, title: 'Citadel' },
  { node: <span className="font-bold tracking-tight">Bridgewater</span>, title: 'Bridgewater' },
  { node: <span className="font-bold tracking-tight">Fidelity</span>, title: 'Fidelity' },
  { node: <span className="font-bold tracking-tight">Vanguard</span>, title: 'Vanguard' },
]

export default function Home() {
  const { user } = useAuth()
  const router = useRouter()
  const walkthroughRef = useRef<HTMLDivElement>(null)

  return (
    <main className="relative min-h-screen bg-white">
      <Navbar />

      {/* Floating Assembler-Inspired Hero */}
      <FloatingObjectsHero />

      {/* Trusted By Section */}
      <section className="py-16 md:py-20 bg-white dark:bg-zinc-950 border-b border-zinc-200 dark:border-zinc-800">
        <div className="container-wide">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, ease: [0.25, 1, 0.5, 1] }}
            className="text-center mb-10"
          >
            <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest">
              Firms who need this tool
            </span>
          </motion.div>
          <motion.div
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8, delay: 0.2 }}
            className="h-12 overflow-hidden"
          >
            <LogoLoop
              logos={enterpriseLogos}
              speed={60}
              direction="left"
              logoHeight={24}
              gap={80}
              hoverSpeed={0}
              fadeOut
              fadeOutColor="transparent"
              ariaLabel="Trusted enterprise customers"
              className="text-zinc-800 dark:text-zinc-200 grayscale opacity-80"
            />
          </motion.div>
        </div>
      </section>

      {/* Walkthrough Section */}
      <motion.section
        ref={walkthroughRef}
        className="bg-zinc-50 dark:bg-zinc-900 scroll-mt-24 relative z-20 pt-24 md:pt-32 border-b border-zinc-200 dark:border-zinc-800"
        initial={{ y: 0 }}
        whileInView={{ y: -40 }}
        viewport={{ margin: '-10% 0px -10% 0px', once: false }}
        transition={{ duration: 0.8, ease: [0.25, 1, 0.5, 1] }}
      >
        {/* Section Header */}
        <div className="container-wide max-w-[90rem]">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8, ease: [0.25, 1, 0.5, 1] }}
            className="text-center mb-16"
          >
            <span className="text-xs font-bold tracking-widest text-zinc-400 uppercase mb-4 block">
              How It Works
            </span>
            <h2 className="text-5xl md:text-7xl font-black tracking-tighter text-black dark:text-white mb-6 uppercase">
              See it in action
            </h2>
            <p className="text-lg text-zinc-500 font-medium max-w-2xl mx-auto">
              From company search to actionable insights in minutes. Scroll down to watch the full walkthrough.
            </p>
          </motion.div>
        </div>

        {/* Scroll-driven walkthrough video */}
        <ScrollVideo src="/walkthrough.mp4" />
      </motion.section>

      {/* FAQ Chat Section */}
      <FAQChat />

      {/* Pricing Section */}
      <PricingSection />

      {/* CTA Section */}
      <motion.section
        className="py-24 md:py-32 bg-white dark:bg-zinc-950 overflow-hidden"
        initial={{ opacity: 0 }}
        whileInView={{ opacity: 1 }}
        viewport={{ amount: 0.3 }}
        transition={{ duration: 0.6 }}
      >
        <div className="container-tight text-center">
          <motion.h2
            className="text-5xl md:text-7xl font-black tracking-tighter text-black dark:text-white mb-6 uppercase"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ amount: 0.5 }}
            transition={{ duration: 0.7, ease: [0.25, 1, 0.5, 1] }}
          >
            Ready to start?
          </motion.h2>
          <motion.p
            className="text-lg text-zinc-500 font-medium max-w-xl mx-auto mb-12"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ amount: 0.5 }}
            transition={{ duration: 0.7, delay: 0.15, ease: [0.25, 1, 0.5, 1] }}
          >
            Join thousands of investors who save hours every week with AI-powered financial analysis.
          </motion.p>
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ amount: 0.5 }}
            transition={{
              duration: 0.5,
              delay: 0.3,
              ease: [0.25, 1, 0.5, 1]
            }}
          >
            <Link
              href={user ? '/dashboard' : '/signup'}
              className="bg-black text-white hover:bg-zinc-800 dark:bg-white dark:text-black dark:hover:bg-zinc-200 border border-black dark:border-white text-sm font-bold tracking-widest uppercase px-12 py-5 inline-flex transition-colors"
            >
              Get Started Free
            </Link>
          </motion.div>
        </div>
      </motion.section>

      {/* Footer with reveal animation */}
      <motion.div
        initial={{ opacity: 0, y: 40 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ amount: 0.2 }}
        transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <MinimalFooter />
      </motion.div>

      {/* Scroll-triggered popup */}
      <BottomPopup threshold={80} />
    </main>
  )
}
