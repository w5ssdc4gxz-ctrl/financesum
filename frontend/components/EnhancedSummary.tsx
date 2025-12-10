'use client'

import ReactMarkdown from 'react-markdown'
import React, { useMemo } from 'react'

interface EnhancedSummaryProps {
  content: string
  persona?: {
    name: string
    image: string
    tagline: string
  } | null
}

/**
 * Parse a metric line like "Revenue: $13.47B" into label and value
 */
function parseMetricLine(line: string): { label: string; value: string } | null {
  // Match patterns like "Revenue: $13.47B" or "Operating Margin: 8.3%"
  const match = line.match(/^(.+?):\s*(.+)$/)
  if (match) {
    return { label: match[1].trim(), value: match[2].trim() }
  }
  return null
}

/**
 * Split a metrics line that may contain multiple metrics separated by | or →.
 * Uses currentLabel for the first value if it appears without a label.
 */
function parseMetricsFromLine(line: string, currentLabel?: string | null): { label: string; value: string }[] {
  const cleaned = line.replace(/[→]/g, '|')
  const segments = cleaned.split('|').map(seg => seg.trim()).filter(Boolean)

  const metrics: { label: string; value: string }[] = []
  let usedCurrentLabel = false

  for (const segment of segments) {
    const parsed = parseMetricLine(segment.replace(/\.$/, ''))
    if (parsed) {
      metrics.push(parsed)
      continue
    }

    if (currentLabel && !usedCurrentLabel) {
      const value = segment.replace(/\.$/, '').trim()
      if (value) {
        metrics.push({ label: currentLabel, value })
        usedCurrentLabel = true
      }
    }
  }

  return metrics
}

/**
 * Get metric category and styling based on label
 */
function getMetricStyle(label: string): {
  category: 'revenue' | 'profit' | 'cash' | 'balance' | 'margin' | 'other'
  icon: string
  gradient: string
  iconColor: string
} {
  const lowerLabel = label.toLowerCase()

  if (lowerLabel.includes('revenue')) {
    return {
      category: 'revenue',
      icon: '📈',
      gradient: 'from-emerald-500/10 to-emerald-600/5',
      iconColor: 'text-emerald-600 dark:text-emerald-400'
    }
  }
  if (lowerLabel.includes('operating income') || lowerLabel.includes('net income')) {
    return {
      category: 'profit',
      icon: '💰',
      gradient: 'from-amber-500/10 to-amber-600/5',
      iconColor: 'text-amber-600 dark:text-amber-400'
    }
  }
  if (lowerLabel.includes('cash flow') || lowerLabel.includes('fcf') || lowerLabel.includes('free cash')) {
    return {
      category: 'cash',
      icon: '💸',
      gradient: 'from-blue-500/10 to-blue-600/5',
      iconColor: 'text-blue-600 dark:text-blue-400'
    }
  }
  if (lowerLabel.includes('cash') || lowerLabel.includes('liquidity')) {
    return {
      category: 'balance',
      icon: '🏦',
      gradient: 'from-cyan-500/10 to-cyan-600/5',
      iconColor: 'text-cyan-600 dark:text-cyan-400'
    }
  }
  if (lowerLabel.includes('assets') || lowerLabel.includes('liabilities') || lowerLabel.includes('expenditure') || lowerLabel.includes('capex')) {
    return {
      category: 'balance',
      icon: '📊',
      gradient: 'from-purple-500/10 to-purple-600/5',
      iconColor: 'text-purple-600 dark:text-purple-400'
    }
  }
  if (lowerLabel.includes('margin')) {
    return {
      category: 'margin',
      icon: '📐',
      gradient: 'from-rose-500/10 to-rose-600/5',
      iconColor: 'text-rose-600 dark:text-rose-400'
    }
  }
  return {
    category: 'other',
    icon: '📌',
    gradient: 'from-gray-500/10 to-gray-600/5',
    iconColor: 'text-gray-600 dark:text-gray-400'
  }
}

/**
 * Determine if a value is positive, negative, or neutral for color coding
 */
function getValueSentiment(value: string): 'positive' | 'negative' | 'neutral' {
  // Check for negative indicators
  if (value.startsWith('-') || value.startsWith('(') || value.toLowerCase().includes('loss')) {
    return 'negative'
  }
  // Percentages over certain thresholds could be good
  const percentMatch = value.match(/(\d+\.?\d*)%/)
  if (percentMatch) {
    const pct = parseFloat(percentMatch[1])
    if (pct > 20) return 'positive'
    if (pct < 5) return 'negative'
  }
  return 'neutral'
}

/**
 * Key Data Appendix metrics grid component
 */
function MetricsGrid({ metrics }: { metrics: { label: string; value: string }[] }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 my-6">
      {metrics.map((metric, index) => {
        const style = getMetricStyle(metric.label)
        const sentiment = getValueSentiment(metric.value)

        return (
          <div
            key={index}
            className={`
              group relative overflow-hidden
              bg-gradient-to-br ${style.gradient}
              border-2 border-black dark:border-white
              shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] dark:shadow-[3px_3px_0px_0px_rgba(255,255,255,1)]
              hover:shadow-[5px_5px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[5px_5px_0px_0px_rgba(255,255,255,1)]
              hover:-translate-x-[2px] hover:-translate-y-[2px]
              transition-all duration-200 ease-out
              p-4 cursor-default
            `}
          >
            {/* Decorative corner accent */}
            <div className="absolute top-0 right-0 w-12 h-12 bg-gradient-to-br from-white/20 to-transparent dark:from-white/5" />

            {/* Icon */}
            <div className={`text-2xl mb-2 ${style.iconColor}`}>
              {style.icon}
            </div>

            {/* Label */}
            <div className="text-xs font-bold uppercase tracking-wider text-gray-600 dark:text-gray-400 mb-1">
              {metric.label}
            </div>

            {/* Value */}
            <div className={`
              text-xl font-black font-mono
              ${sentiment === 'positive' ? 'text-emerald-700 dark:text-emerald-400' : ''}
              ${sentiment === 'negative' ? 'text-red-700 dark:text-red-400' : ''}
              ${sentiment === 'neutral' ? 'text-gray-900 dark:text-white' : ''}
            `}>
              {metric.value}
            </div>

            {/* Hover indicator bar */}
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-black dark:bg-white scale-x-0 group-hover:scale-x-100 transition-transform duration-200 origin-left" />
          </div>
        )
      })}
    </div>
  )
}

/**
 * Preprocess markdown content to ensure proper formatting for ReactMarkdown.
 * This fixes issues where headers appear inline without proper line breaks.
 */
function preprocessContent(text: string): string {
  if (!text) return text

  let result = text

  // Ensure any ## header has a blank line before it (unless at start of text)
  // This is critical for ReactMarkdown to recognize headers
  result = result.replace(/([^\n])\n(#{1,6}\s+)/g, '$1\n\n$2')

  // Also handle cases where ## appears inline after text without any newline
  result = result.replace(/([^\n])\s+(#{1,6}\s+)/g, '$1\n\n$2')

  // Ensure blank line after headers before content
  result = result.replace(/(#{1,6}\s+[^\n]+)\n([^#\n])/g, '$1\n\n$2')

  // Clean up excessive newlines (more than 2)
  result = result.replace(/\n{3,}/g, '\n\n')

  return result
}

/**
 * Normalize shouty/all-caps body lines to sentence case while leaving headings alone.
 * Mirrors backend behavior so only headings stay capitalized.
 */
function normalizeCasing(text: string): string {
  if (!text) return text

  const sentenceCase = (line: string) =>
    line
      .toLowerCase()
      .replace(/(^|[.!?]\s+)(\w)/g, (_, prefix: string, char: string) => `${prefix}${char.toUpperCase()}`)

  return text
    .split('\n')
    .map((line) => {
      const trimmed = line.trimStart()
      if (trimmed.startsWith('#')) return line

      const alphaChars = line.match(/[A-Za-z]/g)
      if (!alphaChars?.length) return line

      const upperRatio = alphaChars.filter((c) => c === c.toUpperCase()).length / alphaChars.length
      const lettersOnly = alphaChars.join('')
      const isAllCaps = lettersOnly === lettersOnly.toUpperCase()
      // Treat lines that are all caps or majority uppercase (>=40%) as shouty; convert to sentence case
      if (isAllCaps || upperRatio >= 0.4) {
        return sentenceCase(line)
      }
      return line
    })
    .join('\n')
}

/**
 * Extract Key Data Appendix section and parse metrics
 */
function extractKeyDataAppendix(content: string): {
  beforeAppendix: string
  metrics: { label: string; value: string }[]
  afterAppendix: string
} | null {
  // Find the Key Data Appendix section
  const appendixMatch = content.match(/##\s*Key\s+Data\s+Appendix\s*\n([\s\S]*?)(?=\n##\s|\n\n##\s|$)/i)

  if (!appendixMatch) return null

  const appendixContent = appendixMatch[1]
  const appendixStart = content.indexOf(appendixMatch[0])
  const appendixEnd = appendixStart + appendixMatch[0].length

  // Parse metrics from the appendix content
  const lines = appendixContent.split('\n')
  const metrics: { label: string; value: string }[] = []
  let currentLabel: string | null = null

  for (const line of lines) {
    // Clean up the line (remove list markers like -, *, →)
    const cleanLine = line.replace(/^[\s\-\*→]+/, '').trim()
    if (!cleanLine) continue

    // If the line looks like a label (no digits and no colon), store for next values
    const looksLikeLabel = !cleanLine.includes(':') && !/\d/.test(cleanLine) && cleanLine.length <= 60
    if (looksLikeLabel) {
      currentLabel = cleanLine
      continue
    }

    const parsedMetrics = parseMetricsFromLine(cleanLine, currentLabel)
    if (parsedMetrics.length) {
      metrics.push(...parsedMetrics)
      currentLabel = null
    }
  }

  if (metrics.length === 0) return null

  return {
    beforeAppendix: content.slice(0, appendixStart),
    metrics,
    afterAppendix: content.slice(appendixEnd)
  }
}

export default function EnhancedSummary({ content, persona }: EnhancedSummaryProps) {
  // Preprocess content to fix markdown formatting issues
  const normalizedContent = useMemo(() => normalizeCasing(content), [content])
  const processedContent = preprocessContent(normalizedContent)

  // Extract Key Data Appendix for special rendering
  const appendixData = useMemo(() => extractKeyDataAppendix(processedContent), [processedContent])

  const renderMarkdown = (text: string) => (
    <ReactMarkdown
      className="normal-case [&_*]:normal-case [text-transform:none]"
      style={{ textTransform: 'none' }}
      components={{
        // Custom renderers for better styling
        p: ({ children }) => (
          <p className="normal-case leading-relaxed text-[15px] text-gray-900 dark:text-gray-100">
            {children}
          </p>
        ),
        h2: ({ children }) => (
          <h2 className="flex items-center gap-3 text-xl font-black uppercase mt-8 mb-4 border-b-2 border-black dark:border-white pb-2">
            <span className="w-4 h-4 bg-black dark:bg-white"></span>
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="flex items-center gap-2 text-lg font-bold uppercase mt-6 mb-3 text-gray-800 dark:text-gray-200">
            <span className="text-blue-600">#</span>
            {children}
          </h3>
        ),
        ul: ({ children }) => (
          <ul className="space-y-2 my-4">
            {children}
          </ul>
        ),
        li: ({ children }) => {
          return (
            <li className="flex items-start gap-2">
              <span className="text-blue-600 font-bold mt-1">→</span>
              <span className="normal-case">{children}</span>
            </li>
          )
        },
        strong: ({ children }) => (
          <strong className="font-black bg-yellow-200 dark:bg-yellow-900/50 px-1">
            {children}
          </strong>
        ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-4 border-black dark:border-white pl-4 italic my-4 bg-gray-50 dark:bg-zinc-900/50 p-4">
            {children}
          </blockquote>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  )

  return (
    <div className="relative space-y-6">
      {/* Persona Badge - Top Right */}
      {persona && (
        <div className="md:absolute md:top-0 md:right-0 flex items-center gap-3 p-3 bg-white dark:bg-zinc-900 border-2 border-black dark:border-white shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)] mb-6 md:mb-0 z-10 max-w-[300px]">
          <div className="w-12 h-12 rounded-full overflow-hidden border-2 border-black dark:border-white shrink-0">
            <img
              src={persona.image}
              alt={persona.name}
              className="w-full h-full object-cover"
            />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-black uppercase text-sm">{persona.name}</h3>
            </div>
            <p className="text-xs font-mono text-gray-600 dark:text-gray-400 line-clamp-1 italic">
              "{persona.tagline}"
            </p>
          </div>
        </div>
      )}

      {/* Render the full content with premium styling */}
      <div
        className={`prose dark:prose-invert max-w-none font-mono normal-case [&_*]:normal-case [&_h2]:uppercase [&_h3]:uppercase ${persona ? 'pt-2 md:pt-16' : ''
          } [text-transform:none]`}
        style={{ textTransform: 'none' }}
      >
        {appendixData ? (
          <>
            {/* Content before Key Data Appendix */}
            {renderMarkdown(appendixData.beforeAppendix)}

            {/* Beautiful Key Data Appendix Section */}
            <h2 className="flex items-center gap-3 text-xl font-black uppercase mt-8 mb-4 border-b-2 border-black dark:border-white pb-2">
              <span className="w-4 h-4 bg-black dark:bg-white"></span>
              Key Data Appendix
            </h2>
            <MetricsGrid metrics={appendixData.metrics} />

            {/* Content after Key Data Appendix */}
            {appendixData.afterAppendix && renderMarkdown(appendixData.afterAppendix)}
          </>
        ) : (
          renderMarkdown(processedContent)
        )}
      </div>
    </div>
  )
}
