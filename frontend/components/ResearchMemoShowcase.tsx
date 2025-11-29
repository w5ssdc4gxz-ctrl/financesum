'use client'

import { CardBody, CardContainer, CardItem } from '@/components/ui/3d-card'

const memoSections = [
  {
    letter: 'E',
    title: 'Executive Summary',
    body: `Alphabet Inc. (GOOG) delivered stronger than expected 2024 performance with revenue up 14% YoY and operating margin expanding to 32%. Cloud and AI investments continue to compound, supporting durable cash generation and shareholder returns.`,
  },
  {
    letter: 'F',
    title: 'Financial Performance',
    body: `Total revenue: $350B • Operating income: $112B • Diluted EPS: $8.04. Cash flow of $125B fuels $62B in buybacks and ongoing AI infrastructure expansion.`,
  },
  {
    letter: 'M',
    title: 'Management Discussion & Analysis',
    body: `Leadership doubles down on AI-native experiences, privacy-resilient ads, and long-term bets like Waymo. Emphasis on efficiency keeps operating leverage intact.`,
  },
  {
    letter: 'R',
    title: 'Risk Factors',
    body: `Ad-demand cyclicality • Regulatory scrutiny • AI model liability • Shifts in revenue mix.`,
  },
]

export default function ResearchMemoShowcase() {
  return (
    <CardContainer className="max-w-3xl mx-auto">
      <CardBody className="bg-gradient-to-br from-[#130626] via-[#120922] to-[#070312] text-left text-gray-200 overflow-visible border-none">
        <CardItem translateZ={55} className="flex items-center gap-3 text-sm text-primary-200 font-semibold mb-4">
          <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary-500/20 text-primary-100 border border-primary-300/30">
            <span className="w-2 h-2 rounded-full bg-primary-300 animate-pulse" />
            Custom Summary · 10-K
          </span>
          <span className="text-xs text-gray-300">Tailored request · ~550 words</span>
        </CardItem>

        <CardItem translateZ={65} className="mb-6">
          <h3 className="text-2xl sm:text-3xl font-black text-white leading-snug">
            Alphabet Inc. (GOOG) · Equity Research Memo
          </h3>
          <p className="text-sm text-gray-400 mt-1">Generated Feb 02, 2025</p>
        </CardItem>

        <CardItem translateZ={70} className="space-y-4">
          {memoSections.map((section) => (
            <div
              key={section.title}
              className="rounded-2xl bg-white/3 backdrop-blur-sm p-4 shadow-inner shadow-black/20"
            >
              <div className="flex items-center gap-3 mb-2">
                <span className="h-8 w-8 rounded-full bg-primary-500/20 text-primary-100 flex items-center justify-center font-bold">
                  {section.letter}
                </span>
                <p className="text-lg font-semibold text-white">{section.title}</p>
              </div>
              <p className="text-sm text-gray-200 leading-relaxed">{section.body}</p>
            </div>
          ))}
        </CardItem>

        <CardItem
          translateZ={80}
          className="absolute top-6 right-6 bg-black/60 rounded-2xl px-4 py-3 shadow-lg backdrop-blur"
        >
          <p className="text-xs uppercase tracking-wide text-gray-400">Analyst Signals</p>
          <p className="text-2xl font-semibold text-white">Bullish</p>
          <p className="text-xs text-gray-400">Bias score +18.4</p>
        </CardItem>

        <CardItem
          translateZ={100}
          className="absolute -bottom-10 right-8 w-64 bg-gradient-to-br from-primary-500/20 to-accent-500/20 rounded-2xl px-5 py-4 shadow-2xl text-sm text-white backdrop-blur-md"
        >
          <p className="text-xs uppercase tracking-widest text-gray-200/70 mb-2">Key Metrics</p>
          <ul className="space-y-1.5">
            <li className="flex justify-between text-gray-100">
              <span>Revenue</span>
              <strong>$350B</strong>
            </li>
            <li className="flex justify-between text-gray-100/90">
              <span>Operating Margin</span>
              <strong>32%</strong>
            </li>
            <li className="flex justify-between text-gray-100/80">
              <span>Free Cash Flow</span>
              <strong>$72B</strong>
            </li>
          </ul>
        </CardItem>
      </CardBody>
    </CardContainer>
  )
}
