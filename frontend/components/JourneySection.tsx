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
    <section className="relative overflow-hidden py-24 w-full">
      {/* Richer background gradient for distinction */}

      <motion.img
        src="/story/background-summary.png"
        className="absolute inset-0 h-full w-full object-cover opacity-30 [mask-image:radial-gradient(circle,transparent,black_80%)] pointer-events-none mix-blend-screen"
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.3 }}
        transition={{ duration: 1.2 }}
      />



      <div className="mx-auto max-w-7xl px-6 lg:px-8 relative z-10 space-y-16">
        <div className="text-center max-w-4xl mx-auto space-y-6">
          <p className="text-xs sm:text-sm uppercase tracking-[0.5em] text-primary-300 font-semibold">Investor Journey</p>
          <h2 className="text-4xl sm:text-5xl lg:text-6xl font-black text-white leading-tight">
            Before, during, and after discovering <ColourfulText text="FinanceSum" className="inline-block" />.
          </h2>
          <p className="text-gray-300 text-lg md:text-xl max-w-2xl mx-auto leading-relaxed">
            These real workflows show the emotional swing—from frantic manual reviews to effortless, AI-backed clarity once
            our summaries enter the stack.
          </p>
        </div>
        <FocusCards cards={storyCards} />
      </div>
    </section>
  )
}
