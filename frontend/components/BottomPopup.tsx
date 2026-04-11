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
          {/* Close button  */}
          <button
            onClick={handleDismiss}
            className="absolute -top-3 -right-3 z-[10000] w-8 h-8 flex items-center justify-center bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors text-black dark:text-white"
            aria-label="Close popup"
            type="button"
          >
            <svg width="12" height="12" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>

          {/* Flat card */}
          <div
            className="relative bg-white dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 p-8 shadow-2xl"
          >
            {/* Content */}
            <div className="relative">
              {/* Animated icon */}
              <motion.div
                className="w-12 h-12 bg-black dark:bg-white text-white dark:text-black flex items-center justify-center mb-6"
                animate={{
                  y: [0, -5, 0]
                }}
                transition={{
                  duration: 2,
                  repeat: Infinity,
                  ease: "easeInOut"
                }}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" fill="currentColor" />
                </svg>
              </motion.div>

              <h3 className="text-xl font-black text-black dark:text-white mb-2 uppercase tracking-tighter">
                Stay Ahead
              </h3>

              <p className="text-zinc-600 dark:text-zinc-400 text-sm mb-6 leading-relaxed font-medium">
                Join our community of analysts for early access to new features and exclusive insights.
              </p>

              {/* Divider */}
              <div className="mb-6 pb-6 border-b border-zinc-200 dark:border-zinc-800" />

              {/* CTA Button */}
              <button
                onClick={handleCTA}
                className="w-full bg-black text-white hover:bg-zinc-800 dark:bg-white dark:text-black dark:hover:bg-zinc-200 border border-black dark:border-white text-xs font-bold tracking-widest uppercase py-4 transition-colors"
                type="button"
              >
                {user ? 'Go to Dashboard' : 'Get Started Free'}
              </button>

              <p className="text-center text-[10px] font-bold tracking-widest uppercase text-zinc-400 mt-4">
                No credit card required
              </p>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
