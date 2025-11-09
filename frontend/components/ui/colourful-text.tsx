'use client'

import { motion } from 'framer-motion'

const cn = (...classes: Array<string | undefined>) => classes.filter(Boolean).join(' ')

interface ColourfulTextProps {
  text: string
  className?: string
}

export default function ColourfulText({ text, className }: ColourfulTextProps) {
  return (
    <motion.span
      initial={{ backgroundPositionX: '0%' }}
      animate={{ backgroundPositionX: '200%' }}
      transition={{ repeat: Infinity, duration: 6, ease: 'linear' }}
      className={cn(
        'bg-gradient-to-r from-[#38bdf8] via-[#a855f7] to-[#f472b6] bg-[length:200%_auto] font-black text-transparent bg-clip-text drop-shadow-[0_0_25px_rgba(168,85,247,0.3)]',
        className,
      )}
    >
      {text}
    </motion.span>
  )
}
