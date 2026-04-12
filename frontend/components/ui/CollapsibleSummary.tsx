'use client'

import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { TypewriterText } from './TypewriterText'

interface CollapsibleSummaryProps {
  content: string
  previewLength?: number
  renderMarkdown: (text: string) => React.ReactNode
  className?: string
}

export function CollapsibleSummary({
  content,
  previewLength = 500,
  renderMarkdown,
  className
}: CollapsibleSummaryProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [hasAnimated, setHasAnimated] = useState(false)
  
  const { preview, remaining } = useMemo(() => {
    if (content.length <= previewLength) {
      return { preview: content, remaining: '' }
    }
    
    let breakPoint = content.lastIndexOf('\n\n', previewLength)
    if (breakPoint === -1 || breakPoint < previewLength * 0.5) {
      breakPoint = content.lastIndexOf('. ', previewLength)
    }
    if (breakPoint === -1 || breakPoint < previewLength * 0.5) {
      breakPoint = content.lastIndexOf(' ', previewLength)
    }
    if (breakPoint === -1) {
      breakPoint = previewLength
    }
    
    return {
      preview: content.slice(0, breakPoint + 1).trim(),
      remaining: content.slice(breakPoint + 1).trim()
    }
  }, [content, previewLength])

  if (!remaining) {
    return <div className={className}>{renderMarkdown(content)}</div>
  }

  return (
    <div className={className}>
      <div className={`relative ${!isExpanded ? 'max-h-[320px] overflow-hidden' : ''}`}>
        {renderMarkdown(preview)}
        
        {!isExpanded && (
          <div 
            className="absolute bottom-0 left-0 right-0 h-40 pointer-events-none z-10"
            style={{
              background: 'linear-gradient(to bottom, transparent 0%, rgb(255 255 255 / 0.8) 50%, rgb(255 255 255) 100%)'
            }}
          />
        )}
        {/* Dark mode gradient overlay */}
        {!isExpanded && (
          <div 
            className="absolute bottom-0 left-0 right-0 h-40 pointer-events-none hidden dark:block z-10"
            style={{
              background: 'linear-gradient(to bottom, transparent 0%, rgb(24 24 27 / 0.8) 50%, rgb(24 24 27) 100%)'
            }}
          />
        )}
      </div>
      
      {!isExpanded && (
        <div className="relative z-20 -mt-12 flex justify-center">
          <motion.button
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            whileHover={{ scale: 1.05, rotate: -1 }}
            whileTap={{ scale: 0.95 }}
            transition={{ duration: 0.3 }}
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
        </div>
      )}

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }}
            className="overflow-hidden"
          >
            {/* Decorative divider */}
            <motion.div 
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ duration: 0.6, delay: 0.1, ease: [0.25, 0.1, 0.25, 1] }}
              className="h-px bg-gradient-to-r from-transparent via-gray-300 dark:via-gray-600 to-transparent my-6 origin-center"
            />
            
            {!hasAnimated ? (
              <TypewriterText
                text={remaining}
                speed={500}
                onComplete={() => setHasAnimated(true)}
              >
                {(displayText) => {
                  const safeText = typeof displayText === 'string' ? displayText : String(displayText || '')
                  const isTyping = safeText.length < remaining.length
                  return (
                    <div className="relative">
                      {renderMarkdown(safeText)}
                      {isTyping && (
                        <motion.span 
                          className="inline-block w-[3px] h-[1.2em] bg-blue-500 ml-0.5 align-baseline absolute rounded-full"
                          animate={{ opacity: [1, 0.3, 1] }}
                          transition={{ duration: 0.8, repeat: Infinity, ease: "easeInOut" }}
                          style={{ 
                            verticalAlign: 'text-bottom',
                            bottom: '1.5em',
                            right: 0
                          }}
                        />
                      )}
                    </div>
                  )
                }}
              </TypewriterText>
            ) : (
              renderMarkdown(remaining)
            )}
            
            {hasAnimated && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.3, duration: 0.4 }}
                className="mt-8 pt-6 border-t-2 border-gray-100 dark:border-gray-800"
              >
                <button
                  onClick={() => {
                    setIsExpanded(false)
                    setHasAnimated(false)
                  }}
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
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
