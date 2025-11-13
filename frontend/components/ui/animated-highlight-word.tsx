'use client'

import { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface AnimatedHighlightWordProps {
  children: ReactNode
  className?: string
}

export default function AnimatedHighlightWord({ children, className }: AnimatedHighlightWordProps) {
  return (
    <span className={cn('relative inline-flex items-center justify-center rounded-[2.5rem] px-6 py-3', className)}>
      <motion.span
        aria-hidden
        className="absolute inset-0 rounded-[2.5rem] border border-white/70 bg-white/10"
        animate={{
          opacity: [0.6, 0.95, 0.6],
          scale: [0.98, 1.02, 0.98],
          boxShadow: [
            '0 0 30px rgba(255,255,255,0.35)',
            '0 0 60px rgba(168,85,247,0.55)',
            '0 0 30px rgba(255,255,255,0.35)',
          ],
          borderColor: ['rgba(255,255,255,0.65)', 'rgba(168,85,247,0.85)', 'rgba(255,255,255,0.65)'],
          backgroundColor: ['rgba(255,255,255,0.08)', 'rgba(255,255,255,0.18)', 'rgba(255,255,255,0.08)'],
        }}
        transition={{ duration: 3.6, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.span
        className="relative font-black tracking-tight bg-gradient-to-r from-white via-primary-200 to-accent-200 bg-[length:200%_auto] bg-clip-text text-transparent drop-shadow-[0_8px_35px_rgba(168,85,247,0.35)]"
        animate={{ backgroundPositionX: ['0%', '200%'] }}
        transition={{ duration: 5, repeat: Infinity, ease: 'linear' }}
      >
        {children}
      </motion.span>
    </span>
  )
}
