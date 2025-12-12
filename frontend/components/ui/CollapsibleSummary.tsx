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
      <div className={`relative ${!isExpanded ? 'max-h-[280px] overflow-hidden' : ''}`}>
        {renderMarkdown(preview)}
        
        {!isExpanded && (
          <div 
            className="absolute bottom-0 left-0 right-0 h-24 pointer-events-none"
            style={{
              background: 'linear-gradient(to bottom, transparent 0%, rgb(24 24 27 / 0.8) 50%, rgb(24 24 27) 100%)'
            }}
          />
        )}
      </div>
      
      {!isExpanded && (
        <motion.button
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          onClick={() => setIsExpanded(true)}
          className="mt-2 text-sm font-bold text-blue-500 hover:text-blue-400 cursor-pointer transition-colors flex items-center gap-1.5 group"
        >
          <span className="border-b border-transparent group-hover:border-current">
            Show more
          </span>
          <motion.svg 
            width="14" 
            height="14" 
            viewBox="0 0 24 24" 
            fill="none" 
            stroke="currentColor" 
            strokeWidth="3"
            strokeLinecap="round" 
            strokeLinejoin="round"
            animate={{ y: [0, 3, 0] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
          >
            <path d="M12 5v14M5 12l7 7 7-7" />
          </motion.svg>
        </motion.button>
      )}

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
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
                        <span 
                          className="inline-block w-[3px] h-[1em] bg-blue-500 ml-0.5 align-baseline animate-pulse absolute"
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
              <motion.button
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.2 }}
                onClick={() => {
                  setIsExpanded(false)
                  setHasAnimated(false)
                }}
                className="mt-6 mb-4 text-sm font-bold text-gray-500 hover:text-gray-400 cursor-pointer transition-colors flex items-center gap-1.5 group"
              >
                <motion.svg 
                  width="14" 
                  height="14" 
                  viewBox="0 0 24 24" 
                  fill="none" 
                  stroke="currentColor" 
                  strokeWidth="3"
                  strokeLinecap="round" 
                  strokeLinejoin="round"
                  animate={{ y: [0, -3, 0] }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
                >
                  <path d="M12 19V5M5 12l7-7 7 7" />
                </motion.svg>
                <span className="border-b border-transparent group-hover:border-current">
                  Show less
                </span>
              </motion.button>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}