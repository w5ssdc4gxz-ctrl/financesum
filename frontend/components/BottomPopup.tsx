'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'

interface BottomPopupProps {
  threshold?: number // Percentage of page scrolled (0-100)
  delay?: number // Delay in ms before showing popup after threshold is reached
}

export default function BottomPopup({ threshold = 85, delay = 500 }: BottomPopupProps) {
  const [isVisible, setIsVisible] = useState(false)
  const [isDismissed, setIsDismissed] = useState(false)
  const [hasReachedThreshold, setHasReachedThreshold] = useState(false)
  const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 })
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)
  const router = useRouter()
  const { user } = useAuth()

  const handleScroll = useCallback(() => {
    if (isDismissed || hasReachedThreshold) return

    const scrollHeight = document.documentElement.scrollHeight - window.innerHeight
    const scrollPosition = window.scrollY
    const scrollPercentage = (scrollPosition / scrollHeight) * 100

    if (scrollPercentage >= threshold) {
      setHasReachedThreshold(true)
    }
  }, [threshold, isDismissed, hasReachedThreshold])

  // Show popup with delay after threshold is reached
  useEffect(() => {
    if (hasReachedThreshold && !isDismissed && !isVisible) {
      timeoutRef.current = setTimeout(() => {
        setIsVisible(true)
      }, delay)
    }

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [hasReachedThreshold, isDismissed, isVisible, delay])

  useEffect(() => {
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [handleScroll])

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    setMousePosition({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    })
  }

  const handleDismiss = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsVisible(false)
    setIsDismissed(true)
  }

  const handleCTA = () => {
    router.push(user ? '/dashboard' : '/signup')
    setIsVisible(false)
    setIsDismissed(true)
  }

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          initial={{ opacity: 0, y: 100, scale: 0.8 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 50, scale: 0.9 }}
          transition={{
            type: "spring",
            stiffness: 260,
            damping: 20,
            mass: 1
          }}
          className="fixed bottom-6 right-6 z-[9999] w-[340px] sm:w-[380px]"
          onMouseMove={handleMouseMove}
        >
          {/* Close button - positioned outside the card for better click handling */}
          <button
            onClick={handleDismiss}
            className="absolute -top-2 -right-2 z-[10000] w-8 h-8 flex items-center justify-center rounded-full bg-white shadow-lg border border-gray-200 hover:bg-gray-50 transition-colors text-gray-500 hover:text-gray-700"
            aria-label="Close popup"
            type="button"
          >
            <svg width="12" height="12" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
          </button>

          {/* Animated gradient border */}
          <div className="absolute -inset-[2px] rounded-3xl overflow-hidden pointer-events-none">
            <div
              className="absolute inset-0 animate-spin-slow"
              style={{
                background: 'conic-gradient(from 0deg, #0015ff, #7c3aed, #ec4899, #f59e0b, #10b981, #0015ff)',
              }}
            />
          </div>

          {/* Glass card */}
          <div
            className="relative bg-white/95 backdrop-blur-xl rounded-3xl p-6 shadow-2xl"
            style={{
              boxShadow: '0 25px 50px -12px rgba(0, 21, 255, 0.25), 0 0 0 1px rgba(255,255,255,0.1)',
            }}
          >
            {/* Spotlight effect following mouse */}
            <div
              className="pointer-events-none absolute inset-0 rounded-3xl opacity-50"
              style={{
                background: `radial-gradient(400px circle at ${mousePosition.x}px ${mousePosition.y}px, rgba(0, 21, 255, 0.08), transparent 40%)`,
              }}
            />

            {/* Decorative element */}
            <div className="absolute -top-20 -right-20 w-40 h-40 bg-gradient-to-br from-[#0015ff]/20 to-purple-500/20 rounded-full blur-3xl pointer-events-none" />

            {/* Content */}
            <div className="relative">
              {/* Animated icon */}
              <motion.div
                className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#0015ff] to-[#7c3aed] flex items-center justify-center mb-4 shadow-lg"
                animate={{
                  rotate: [0, 5, -5, 0],
                  scale: [1, 1.05, 1]
                }}
                transition={{
                  duration: 4,
                  repeat: Infinity,
                  ease: "easeInOut"
                }}
              >
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" fill="white"/>
                </svg>
              </motion.div>

              <h3 className="text-xl font-bold text-foreground mb-2 font-serif italic">
                One more thing...
              </h3>

              <p className="text-muted-foreground text-sm mb-5 leading-relaxed">
                Get early access to new features and exclusive insights. Join 2,000+ analysts already using FinanceSum.
              </p>

              {/* Stats row */}
              <div className="flex items-center gap-4 mb-5 pb-5 border-b border-gray-100">
                <div className="flex -space-x-2">
                  {[1, 2, 3, 4].map((i) => (
                    <div
                      key={i}
                      className="w-8 h-8 rounded-full bg-gradient-to-br from-gray-200 to-gray-300 border-2 border-white flex items-center justify-center text-xs font-medium text-gray-600"
                    >
                      {['JM', 'AR', 'KL', 'PD'][i-1]}
                    </div>
                  ))}
                </div>
                <div className="text-xs text-muted-foreground">
                  <span className="font-semibold text-foreground">127</span> signed up this week
                </div>
              </div>

              {/* CTA Button with shine effect */}
              <motion.button
                onClick={handleCTA}
                className="w-full relative overflow-hidden bg-[#0015ff] text-white font-medium py-3.5 px-6 rounded-xl transition-all hover:shadow-lg hover:shadow-[#0015ff]/25"
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                type="button"
              >
                <span className="relative z-10">
                  {user ? 'Go to Dashboard' : 'Get Started Free'}
                </span>
                {/* Shine effect */}
                <motion.div
                  className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent -skew-x-12"
                  animate={{ x: ['-200%', '200%'] }}
                  transition={{ duration: 2, repeat: Infinity, repeatDelay: 3 }}
                />
              </motion.button>

              <p className="text-center text-xs text-muted-foreground mt-3">
                No credit card required
              </p>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
