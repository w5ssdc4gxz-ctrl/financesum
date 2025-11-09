'use client'

import { motion } from 'framer-motion'

export default function AnimatedBackground() {
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <motion.div
        className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary-500/15 rounded-full blur-3xl"
        animate={{
          x: [0, 80, 0],
          y: [0, -80, 0],
          scale: [1, 1.15, 1],
        }}
        transition={{
          duration: 25,
          repeat: Infinity,
          ease: "linear"
        }}
        style={{ willChange: 'transform' }}
      />
      
      <motion.div
        className="absolute top-1/2 right-1/4 w-96 h-96 bg-accent-500/15 rounded-full blur-3xl"
        animate={{
          x: [0, -80, 0],
          y: [0, 80, 0],
          scale: [1, 1.2, 1],
        }}
        transition={{
          duration: 30,
          repeat: Infinity,
          ease: "linear"
        }}
        style={{ willChange: 'transform' }}
      />
      
      <motion.div
        className="absolute bottom-1/4 left-1/3 w-80 h-80 bg-purple-500/10 rounded-full blur-3xl"
        animate={{
          x: [0, 60, 0],
          y: [0, -60, 0],
          scale: [1, 1.1, 1],
        }}
        transition={{
          duration: 28,
          repeat: Infinity,
          ease: "linear"
        }}
        style={{ willChange: 'transform' }}
      />

      <motion.div
        className="absolute top-3/4 right-1/3 w-72 h-72 bg-blue-500/10 rounded-full blur-3xl"
        animate={{
          x: [0, -50, 0],
          y: [0, 50, 0],
          scale: [1, 1.1, 1],
        }}
        transition={{
          duration: 32,
          repeat: Infinity,
          ease: "linear"
        }}
        style={{ willChange: 'transform' }}
      />

      <div className="absolute inset-0 bg-gradient-to-b from-dark-900/50 via-transparent to-dark-900/80" />
    </div>
  )
}
