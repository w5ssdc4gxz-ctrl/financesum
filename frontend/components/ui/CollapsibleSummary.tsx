'use client'

import { useState, useMemo, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

interface CollapsibleSummaryProps {
  content: string
  previewLength?: number
  renderMarkdown: (text: string) => React.ReactNode
  className?: string
  onExpandChange?: (expanded: boolean) => void
  externalExpandSignal?: number
}

export function CollapsibleSummary({
  content,
  previewLength = 500,
  renderMarkdown,
  className,
  onExpandChange,
  externalExpandSignal
}: CollapsibleSummaryProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  useEffect(() => {
    if (!externalExpandSignal || isExpanded) return
    setIsExpanded(true)
  }, [externalExpandSignal, isExpanded])

  useEffect(() => {
    onExpandChange?.(isExpanded)
  }, [isExpanded, onExpandChange])

  const isShort = useMemo(() => content.length <= previewLength, [content, previewLength])

  if (isShort) {
    return <div className={className}>{renderMarkdown(content)}</div>
  }

  return (
    <div className={className}>
      {/* Always render full content — section IDs must exist in DOM for pill navigation */}
      <div
        className={`relative ${!isExpanded ? 'max-h-[320px] overflow-hidden' : ''}`}
        style={!isExpanded ? {
          WebkitMaskImage: 'linear-gradient(to bottom, black 40%, transparent 100%)',
          maskImage: 'linear-gradient(to bottom, black 40%, transparent 100%)',
        } : undefined}
      >
        {renderMarkdown(content)}
      </div>

      <AnimatePresence mode="wait">
        {!isExpanded ? (
          <motion.div
            key="expand"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.25 }}
            className="relative z-20 -mt-4 flex justify-center"
          >
            <motion.button
              whileHover={{ scale: 1.05, rotate: -1 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setIsExpanded(true)}
              className="px-8 py-3.5 text-sm font-black uppercase tracking-widest cursor-pointer transition-all duration-200 flex items-center gap-3 group
                bg-blue-600 text-white
                border-2 border-black dark:border-white
                shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] dark:shadow-[6px_6px_0px_0px_rgba(255,255,255,1)]
                hover:shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[8px_8px_0px_0px_rgba(255,255,255,1)]
                hover:bg-blue-500"
            >
              <span>Read Full Brief</span>
              <motion.svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
                animate={{ y: [0, 5, 0] }}
                transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
              >
                <path d="M7 13l5 5 5-5M7 6l5 5 5-5" />
              </motion.svg>
            </motion.button>
          </motion.div>
        ) : (
          <motion.div
            key="collapse"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            transition={{ delay: 0.15, duration: 0.3 }}
            className="mt-8 pt-6 border-t-2 border-gray-100 dark:border-gray-800"
          >
            <button
              onClick={() => setIsExpanded(false)}
              className="px-4 py-2 text-sm font-bold uppercase tracking-wider cursor-pointer transition-all duration-200 flex items-center gap-2 group
                text-gray-500 hover:text-black dark:hover:text-white
                border-2 border-gray-300 dark:border-gray-700 hover:border-black dark:hover:border-white
                hover:-translate-y-0.5"
            >
              <motion.svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                animate={{ y: [0, -3, 0] }}
                transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
              >
                <path d="M12 19V5M5 12l7-7 7 7" />
              </motion.svg>
              <span>Collapse</span>
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
