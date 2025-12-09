'use client'

import ReactMarkdown from 'react-markdown'

interface EnhancedSummaryProps {
  content: string
  persona?: {
    name: string
    image: string
    tagline: string
  } | null
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

export default function EnhancedSummary({ content, persona }: EnhancedSummaryProps) {
  // Preprocess content to fix markdown formatting issues
  const processedContent = preprocessContent(content)

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
        className={`prose dark:prose-invert max-w-none font-mono normal-case [&_*]:normal-case [&_h2]:uppercase [&_h3]:uppercase ${
          persona ? 'pt-2 md:pt-16' : ''
        }`}
      >
        <ReactMarkdown
          className="normal-case [&_*]:normal-case"
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
              const content = String(children)
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
          {processedContent}
        </ReactMarkdown>
      </div>
    </div>
  )
}
