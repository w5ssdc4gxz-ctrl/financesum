'use client'

import ReactMarkdown from 'react-markdown'

interface EnhancedSummaryProps {
  content: string
}

export default function EnhancedSummary({ content }: EnhancedSummaryProps) {
  // Extract key metrics from the content
  const extractMetrics = (text: string) => {
    const metrics: Array<{ label: string; value: string }> = []
    
    // Common patterns for financial metrics
    const patterns = [
      /Total Net Sales:\s*\$?([\d,.]+ (?:billion|million|thousand)?)/i,
      /Net Income:\s*\$?([\d,.]+ (?:billion|million|thousand)?)/i,
      /Earnings Per Share.*?:\s*\$?([\d,.]+)/i,
      /(?:Products|Services) Gross Margin:\s*([\d.]+%)/i,
      /Total Gross Margin.*?:\s*([\d.]+%)/i,
      /(?:Research and Development|R&D) Expense.*?:\s*\$?([\d,.]+ (?:billion|million)?)/i,
      /Cash.*?:\s*\$?([\d,.]+ (?:billion|million)?)/i,
      /Free Cash Flow:\s*\$?([\d,.]+ (?:billion|million)?)/i,
      /Shares Outstanding.*?:\s*([\d,]+)/i,
      /Dividends.*?:\s*\$?([\d.]+)/i,
    ]
    
    patterns.forEach(pattern => {
      const match = text.match(pattern)
      if (match) {
        const fullMatch = match[0]
        const label = fullMatch.split(':')[0].trim()
        const value = match[1].trim()
        metrics.push({ label, value })
      }
    })
    
    return metrics
  }

  // Split content into sections
  const sections = content.split(/(?=^#{1,3} )/m).filter(Boolean)
  const metrics = extractMetrics(content)
  
  // Check if content has "Key Metrics Dashboard" section
  const hasKeyMetricsSection = content.toLowerCase().includes('key metrics dashboard')

  return (
    <div className="space-y-6">
      {/* Display metrics in cards if we found any */}
      {metrics.length > 0 && (
        <div className="mb-8">
          <h3 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
            <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            Key Metrics
          </h3>
          <div className="metrics-grid">
            {metrics.map((metric, idx) => (
              <div key={idx} className="metric-card group">
                <div className="metric-label">{metric.label}</div>
                <div className="metric-value">{metric.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      
      {/* Render the full content with premium styling */}
      <div className="prose-premium">
        <ReactMarkdown
          components={{
            // Custom renderers for better styling
            h2: ({ children }) => (
              <h2 className="flex items-center gap-3">
                <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center text-white text-sm font-bold">
                  {String(children).charAt(0)}
                </span>
                {children}
              </h2>
            ),
            h3: ({ children }) => (
              <h3 className="flex items-center gap-2">
                <span className="w-1 h-6 bg-gradient-to-b from-primary-500 to-accent-500 rounded"></span>
                {children}
              </h3>
            ),
            ul: ({ children }) => (
              <ul className="space-y-3">
                {children}
              </ul>
            ),
            li: ({ children }) => {
              const content = String(children)
              const hasStrongMetric = content.includes(':')
              
              return (
                <li className={`flex items-start gap-3 ${hasStrongMetric ? 'highlight-box' : ''}`}>
                  <svg className="w-5 h-5 text-primary-400 flex-shrink-0 mt-1" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                  <span>{children}</span>
                </li>
              )
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    </div>
  )
}
