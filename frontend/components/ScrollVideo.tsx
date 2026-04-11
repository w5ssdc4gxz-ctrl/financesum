'use client'

import { useRef, useEffect, useState } from 'react'
import { motion } from 'framer-motion'

interface ScrollVideoProps {
  src: string
  className?: string
}

export default function ScrollVideo({ src, className }: ScrollVideoProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [isLoaded, setIsLoaded] = useState(false)

  // Play when visible, pause when not
  useEffect(() => {
    const video = videoRef.current
    const container = containerRef.current
    if (!video || !container) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          video.play().catch(() => {})
        } else {
          video.pause()
        }
      },
      { threshold: 0.3 }
    )

    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={containerRef} className={className || ''}>
      <motion.div
        className="relative w-full max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8"
        initial={{ opacity: 0, scale: 0.92, y: 60 }}
        whileInView={{ opacity: 1, scale: 1, y: 0 }}
        viewport={{ once: true, amount: 0.15 }}
        transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        {/* Glow behind video */}
        <div
          className="absolute -inset-4 sm:-inset-8 rounded-3xl pointer-events-none"
          style={{
            background:
              'radial-gradient(ellipse at center, rgba(0, 21, 255, 0.12) 0%, rgba(0, 21, 255, 0.04) 50%, transparent 70%)',
            filter: 'blur(40px)',
          }}
        />

        {/* Browser frame */}
        <div className="relative overflow-hidden rounded-2xl shadow-2xl bg-[#0a0a0a]">
          {/* Chrome bar */}
          <div className="relative z-10 flex items-center gap-2 px-4 py-3 bg-[#1a1a1a] border-b border-white/5">
            <div className="flex gap-1.5">
              <span className="w-3 h-3 rounded-full bg-[#ff5f57]" />
              <span className="w-3 h-3 rounded-full bg-[#febc2e]" />
              <span className="w-3 h-3 rounded-full bg-[#28c840]" />
            </div>
            <div className="flex-1 flex justify-center">
              <div className="flex items-center gap-2 px-4 py-1 rounded-md bg-[#0a0a0a]/60 border border-white/10 text-xs text-white/40 font-mono">
                <svg className="w-3 h-3 text-white/30" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
                </svg>
                financesums.com
              </div>
            </div>
            <div className="w-[52px]" />
          </div>

          {/* Loading skeleton */}
          {!isLoaded && (
            <div className="absolute inset-0 top-[41px] z-20 bg-[#0a0a0a] flex items-center justify-center">
              <div className="flex flex-col items-center gap-4">
                <div className="w-10 h-10 border-2 border-[#0015ff]/30 border-t-[#0015ff] rounded-full animate-spin" />
                <span className="text-white/40 text-sm font-mono">Loading walkthrough...</span>
              </div>
            </div>
          )}

          {/* Video */}
          <video
            ref={videoRef}
            src={src}
            preload="auto"
            muted
            playsInline
            loop
            onLoadedData={() => setIsLoaded(true)}
            className="w-full aspect-video block"
          />
        </div>

        {/* Reflection */}
        <div
          className="mt-2 h-20 w-[90%] mx-auto rounded-b-3xl pointer-events-none"
          style={{
            background: 'linear-gradient(to bottom, rgba(0, 21, 255, 0.06) 0%, transparent 100%)',
            filter: 'blur(20px)',
          }}
        />
      </motion.div>
    </div>
  )
}
