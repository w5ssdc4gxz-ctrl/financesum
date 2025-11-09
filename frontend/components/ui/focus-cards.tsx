'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'

export interface FocusCard {
  title: string
  description: string
  src: string
  tag: string
}

interface FocusCardsProps {
  cards: FocusCard[]
}

export function FocusCards({ cards }: FocusCardsProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)

  return (
    <div className="grid gap-8 grid-cols-1 lg:grid-cols-3">
      {cards.map((card, index) => {
        const isActive = hoveredIndex === index
        return (
          <motion.article
            key={card.title}
            onHoverStart={() => setHoveredIndex(index)}
            onHoverEnd={() => setHoveredIndex(null)}
            className="relative overflow-hidden rounded-[32px] border border-white/10 bg-black/40 shadow-2xl backdrop-blur-md min-h-[20rem] flex flex-col"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-10%' }}
            transition={{ duration: 0.5, delay: index * 0.08 }}
          >
            <motion.img
              src={card.src}
              alt={card.title}
              className="absolute inset-0 h-full w-full object-cover"
              animate={{ scale: isActive ? 1.08 : 1, opacity: isActive ? 0.95 : 0.75 }}
              transition={{ duration: 0.6 }}
            />
            <div className="absolute inset-0 bg-gradient-to-b from-black/85 via-black/20 to-black/85" />
            <div className="relative h-full flex flex-col justify-between p-8 text-white">
              <div>
                <span className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-black/40 px-4 py-1 text-xs uppercase tracking-wide">
                  <span className="h-2 w-2 rounded-full bg-primary-300 animate-ping" />
                  {card.tag}
                </span>
                <h3 className="mt-4 text-2xl font-bold">{card.title}</h3>
                <p className="mt-3 text-base text-gray-200 leading-relaxed">{card.description}</p>
              </div>
              <motion.div
                className="text-sm font-semibold text-primary-200"
                animate={{ opacity: isActive ? 1 : 0.4, x: isActive ? 6 : 0 }}
                transition={{ duration: 0.3 }}
              >
                Hover to feel the shift →
              </motion.div>
            </div>
          </motion.article>
        )
      })}
    </div>
  )
}
