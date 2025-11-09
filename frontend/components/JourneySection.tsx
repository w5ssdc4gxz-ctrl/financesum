'use client'

import { motion } from 'framer-motion'
import ColourfulText from '@/components/ui/colourful-text'
import { FocusCards, FocusCard } from '@/components/ui/focus-cards'

const storyCards: FocusCard[] = [
  {
    title: 'Before FinanceSum · Constant fire drills.',
    description:
      'Late nights, endless 10-Ks, and no time to think. This was our norm—scrolling PDFs, copying tables, and still feeling unprepared for Monday’s investment committee.',
    src: '/story/after.png',
    tag: 'Before · Manual grind',
  },
  {
    title: 'During discovery · First summary hits.',
    description:
      'The first FinanceSum memo reframed an entire filing in clear English. KPIs, risk factors, personas—everything snapped into place and prep time dropped from hours to minutes.',
    src: '/story/before.png',
    tag: 'During · Aha moment',
  },
  {
    title: 'After adoption · Breathe easy.',
    description:
      'Now the platform handles the heavy reading. We skim, stress-test the AI takeaways, and stay fresh for higher-leverage decisions instead of fighting document fatigue.',
    src: '/story/during.png',
    tag: 'After · Always ready',
  },
]

export default function JourneySection() {
  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8">
      <section className="relative overflow-hidden rounded-[48px] border border-white/10 bg-gradient-to-br from-[#170432] via-[#0B0316] to-[#05030D] px-6 sm:px-12 lg:px-20 py-16 min-h-[90vh] w-full">
        <motion.img
          src="/story/background-summary.png"
          className="absolute inset-0 h-full w-full object-cover opacity-50 [mask-image:radial-gradient(circle,transparent,black_80%)] pointer-events-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.5 }}
          transition={{ duration: 1.2 }}
        />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,_rgba(255,255,255,0.1),transparent_55%)]" />
        <div className="relative z-10 space-y-12">
          <div className="text-center max-w-4xl mx-auto space-y-5">
            <p className="text-xs sm:text-sm uppercase tracking-[0.5em] text-primary-300">Investor Journey</p>
            <h2 className="text-4xl sm:text-5xl lg:text-6xl font-black text-white leading-tight">
              Before, during, and after discovering <ColourfulText text="FinanceSum" className="inline-block" />.
            </h2>
            <p className="text-gray-200 text-lg">
              These real workflows show the emotional swing—from frantic manual reviews to effortless, AI-backed clarity once
              our summaries enter the stack.
            </p>
          </div>
          <FocusCards cards={storyCards} />
        </div>
      </section>
    </div>
  )
}
