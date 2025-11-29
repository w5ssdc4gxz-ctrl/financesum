"use client"

import { motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { WavyBackground } from '@/components/ui/wavy-background'
import { AuroraBackground } from '@/components/ui/aurora-background'
import { BackgroundGradientAnimation } from '@/components/ui/background-gradient-animation'

const clamp01 = (value: number) => Math.min(Math.max(value, 0), 1)

const smoothStep = (edge0: number, edge1: number, x: number) => {
  if (edge0 === edge1) {
    return x >= edge0 ? 1 : 0
  }
  const t = clamp01((x - edge0) / (edge1 - edge0))
  return t * t * (3 - 2 * t)
}

const fadeRange = (value: number, start: number, peak: number, end: number) => {
  if (value <= start || value >= end) return 0
  if (value <= peak) {
    return smoothStep(start, peak, value)
  }
  return 1 - smoothStep(peak, end, value)
}

export default function LayeredScrollBackground() {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    const calculateProgress = () => {
      if (typeof window === 'undefined') return
      const doc = document.documentElement
      const maxScroll = doc.scrollHeight - doc.clientHeight
      const ratio = maxScroll > 0 ? window.scrollY / maxScroll : 0
      setProgress(clamp01(ratio))
    }

    calculateProgress()
    window.addEventListener('scroll', calculateProgress, { passive: true })
    window.addEventListener('resize', calculateProgress)

    return () => {
      window.removeEventListener('scroll', calculateProgress)
      window.removeEventListener('resize', calculateProgress)
    }
  }, [])

  // Keep wavy background active longer (covering Hero + Journey)
  const wavyOpacity = useMemo(() => clamp01(1 - smoothStep(0.35, 0.55, progress)), [progress])
  const wavyTranslate = useMemo(() => -progress * 120, [progress])

  // Delay aurora entry until after Journey section
  const auroraOpacity = useMemo(() => {
    const fadeIn = smoothStep(0.40, 0.60, progress)
    const sustain = smoothStep(0.60, 0.8, progress)
    return clamp01(Math.max(fadeIn, sustain))
  }, [progress])
  const auroraTranslate = useMemo(() => (progress - 0.28) * 180, [progress])

  const gradientOpacity = useMemo(() => clamp01(smoothStep(0.45, 0.65, progress)), [progress])
  const gradientScale = useMemo(() => 1 + smoothStep(0.45, 1, progress) * 0.18, [progress])

  // Delay darkening tint
  const tintOpacity = useMemo(() => 0.25 + smoothStep(0.4, 1, progress) * 0.35, [progress])

  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      {/* Wavy */}
      <motion.div
        className="absolute inset-0 mix-blend-screen"
        style={{ opacity: wavyOpacity, y: wavyTranslate }}
      >
        <div className="absolute inset-0">
          <WavyBackground
            containerClassName="absolute inset-0 h-full w-full"
            className="opacity-0"
            blur={22}
            waveOpacity={1}
            backgroundFill="rgba(6,0,20,0.25)"
            colors={[
              'rgba(144,85,255,0.95)',
              'rgba(0,221,235,0.8)',
              'rgba(244,114,182,0.85)',
              'rgba(96,165,250,0.9)',
              'rgba(124,58,237,0.95)',
            ]}
            speed="slow"
          >
            <span className="sr-only">Dynamic wave background</span>
          </WavyBackground>
        </div>
        <div className="absolute inset-0 bg-gradient-to-b from-[#12022d]/45 via-transparent to-transparent" />
      </motion.div>

      {/* Aurora */}
      <motion.div
        className="absolute inset-0 mix-blend-lighten"
        style={{ opacity: auroraOpacity, y: auroraTranslate }}
      >
        <div className="absolute inset-0">
          <AuroraBackground
            className="h-screen w-screen pointer-events-none bg-transparent"
            showRadialGradient
          >
            <span className="sr-only">Aurora background</span>
          </AuroraBackground>
        </div>
      </motion.div>

      {/* Gradient Orbs */}
      <motion.div
        className="absolute inset-0 mix-blend-screen"
        style={{ opacity: gradientOpacity, scale: gradientScale }}
      >
        <div className="absolute inset-0">
          <BackgroundGradientAnimation
            containerClassName="h-screen w-screen pointer-events-none"
            className="opacity-0"
            interactive={false}
            size="90%"
            gradientBackgroundStart="rgba(15,0,35,0.75)"
            gradientBackgroundEnd="rgba(5,0,15,0.7)"
            firstColor="168,85,247"
            secondColor="14,165,233"
            thirdColor="236,72,153"
            fourthColor="59,130,246"
            fifthColor="16,185,129"
            pointerColor="255,255,255"
          >
            <span className="sr-only">Gradient animation</span>
          </BackgroundGradientAnimation>
        </div>
      </motion.div>

      {/* Soft tint to avoid harsh dark spots */}
      <motion.div
        className="absolute inset-0 bg-gradient-to-b from-[#080114] via-[#100225] to-[#050015]"
        style={{ opacity: tintOpacity }}
      />
    </div>
  )
}
