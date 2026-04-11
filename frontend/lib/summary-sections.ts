/**
 * Pure utility for parsing markdown summaries into structured sections.
 * Used by EnhancedSummary to enable section-aware rendering for long-form content.
 */

export interface SummarySection {
  id: string
  title: string
  content: string
  index: number
  wordCount: number
  accentGradient: string
  accentColor: string
  pullQuote: string | null
}

export interface StructuredRiskFactor {
  id: string
  title: string
  body: string
}

export interface ParsedRiskFactors {
  intro: string
  items: StructuredRiskFactor[]
  outro: string
}

export interface ParsedSummary {
  sections: SummarySection[]
  totalWordCount: number
  isLongForm: boolean
  readingTimeMinutes: number
  preamble: string
}

const SECTION_ACCENTS: Record<string, { gradient: string; color: string }> = {
  'executive summary': { gradient: 'from-blue-500 to-indigo-500', color: 'blue' },
  'financial performance': { gradient: 'from-emerald-500 to-teal-500', color: 'emerald' },
  'financial health': { gradient: 'from-amber-500 to-orange-500', color: 'amber' },
  'management discussion': { gradient: 'from-purple-500 to-violet-500', color: 'purple' },
  'risk factors': { gradient: 'from-rose-500 to-red-500', color: 'rose' },
  'closing takeaway': { gradient: 'from-sky-500 to-blue-500', color: 'sky' },
  'key metrics': { gradient: 'from-gray-500 to-slate-500', color: 'gray' },
}

const DEFAULT_ACCENT = { gradient: 'from-gray-400 to-gray-500', color: 'gray' }

const RISK_TITLE_HINT_RE =
  /\b(?:risk|exposure|constraint|dependency|disruption|pressure|concentration|overhang|headwind|delay|retention|renewal|controls?|investigation|remedy|shipment|capacity|pricing|demand|volatility|churn)\b/i

const INLINE_RISK_HEADER_RE =
  /([.!?])\s+(?=(?:\*\*)?(?:[A-Z][A-Za-z0-9/&(),'"’.-]*\s+){0,8}(?:Risk|Exposure|Constraint|Dependency|Disruption|Pressure|Concentration|Overhang|Headwind|Delay|Retention|Renewal|Controls?|Investigation|Remedy|Shipment|Capacity|Pricing|Demand|Volatility|Churn)\b[^:\n]{0,18}(?:\*\*)?\s*:)/g

const BOLD_RISK_HEADER_RE = /\s+(?=\*\*[^*]{2,120}\*\*\s*:)/g

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .trim()
}

function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length
}

function getAccent(title: string): { gradient: string; color: string } {
  const lower = title.toLowerCase()
  for (const [keyword, accent] of Object.entries(SECTION_ACCENTS)) {
    if (lower.includes(keyword)) return accent
  }
  return DEFAULT_ACCENT
}

export function isRiskFactorsSection(title: string): boolean {
  return title.toLowerCase().includes('risk factors')
}

/**
 * Extract the first bold sentence as a pull quote.
 * Returns { quote, strippedContent } or null if no suitable quote found.
 */
function extractPullQuote(
  content: string,
  wordCount: number
): { quote: string; strippedContent: string } | null {
  if (wordCount < 80) return null

  // Match first **bold** phrase that looks like a lead sentence
  const boldMatch = content.match(/^\s*\*\*(.+?)\*\*[.:]?\s*/m)
  if (boldMatch && boldMatch[1].length <= 140) {
    const quote = boldMatch[1].replace(/[.:]+$/, '')
    const strippedContent = content.replace(boldMatch[0], '').trimStart()
    return { quote, strippedContent }
  }

  return null
}

export function getSectionAccentColor(title: string): string {
  return getAccent(title).color
}

export function getSectionAccentGradient(title: string): string {
  return getAccent(title).gradient
}

export function splitSentences(text: string): string[] {
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (!normalized) return []
  return (normalized.match(/[^.!?]+[.!?]+(?:\s|$)|.+$/g) || [])
    .map((sentence) => sentence.trim())
    .filter(Boolean)
}

function cleanRiskTitle(title: string): string {
  return title
    .replace(/^\*\*|\*\*$/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function normalizeRiskFactorsContent(content: string): string {
  return content
    .replace(/\r\n/g, '\n')
    .replace(BOLD_RISK_HEADER_RE, '\n\n')
    .replace(INLINE_RISK_HEADER_RE, '$1\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function looksLikeRiskTitle(title: string): boolean {
  const cleaned = cleanRiskTitle(title).replace(/^["“]|["”]$/g, '')
  if (!cleaned || cleaned.length > 96) return false
  if (/^(?:the filing|management|watch|early-warning signal|early warning signal)/i.test(cleaned)) {
    return false
  }
  const tokens = cleaned.split(/\s+/).filter(Boolean)
  if (tokens.length === 0 || tokens.length > 10) return false
  if (RISK_TITLE_HINT_RE.test(cleaned)) return true
  return tokens.length <= 6 && tokens.every((token) => /^[A-Z0-9][A-Za-z0-9/&()'".-]*$/.test(token))
}

function parseRiskBlock(block: string): { title: string; body: string } | null {
  const boldMatch = block.match(/^(?:>\s*)?\*\*(.+?)\*\*\s*:\s*([\s\S]*)$/)
  if (boldMatch) {
    return {
      title: cleanRiskTitle(boldMatch[1]),
      body: (boldMatch[2] || '').trim(),
    }
  }

  const plainMatch = block.match(/^([^:\n]{3,100})\s*:\s*([\s\S]*)$/)
  if (!plainMatch) return null

  const title = cleanRiskTitle(plainMatch[1])
  if (!looksLikeRiskTitle(title)) return null

  return {
    title,
    body: (plainMatch[2] || '').trim(),
  }
}

function buildStructuredRiskFactor(
  title: string,
  body: string,
  index: number
): StructuredRiskFactor {
  return {
    id: `${slugify(title)}-${index}`,
    title,
    body: body.replace(/\s+/g, ' ').trim(),
  }
}

export function parseRiskFactorsContent(content: string): ParsedRiskFactors {
  const normalized = normalizeRiskFactorsContent(content)
  if (!normalized) {
    return { intro: '', items: [], outro: '' }
  }

  const blocks = normalized
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)

  const introParts: string[] = []
  const outroParts: string[] = []
  const items: StructuredRiskFactor[] = []
  const seenTitles = new Set<string>()
  let seenFirstRisk = false

  for (const block of blocks) {
    const parsed = parseRiskBlock(block)
    if (parsed) {
      seenFirstRisk = true
      const titleKey = slugify(parsed.title)
      if (titleKey && !seenTitles.has(titleKey)) {
        seenTitles.add(titleKey)
        items.push(buildStructuredRiskFactor(parsed.title, parsed.body, items.length))
      }
      continue
    }

    if (!seenFirstRisk) {
      introParts.push(block)
      continue
    }

    if (items.length > 0) {
      const last = items[items.length - 1]
      items[items.length - 1] = buildStructuredRiskFactor(
        last.title,
        `${last.body} ${block}`.trim(),
        items.length - 1
      )
      continue
    }

    outroParts.push(block)
  }

  return {
    intro: introParts.join('\n\n').trim(),
    items,
    outro: outroParts.join('\n\n').trim(),
  }
}

export function parseSummary(content: string): ParsedSummary {
  if (!content || !content.trim()) {
    return {
      sections: [],
      totalWordCount: 0,
      isLongForm: false,
      readingTimeMinutes: 0,
      preamble: '',
    }
  }

  // Split on ## headings (keeping the heading text)
  const parts = content.split(/^## /m)
  const preamble = parts[0]?.trim() || ''

  const sections: SummarySection[] = []

  for (let i = 1; i < parts.length; i++) {
    const part = parts[i]
    const newlineIdx = part.indexOf('\n')
    const title = (newlineIdx >= 0 ? part.slice(0, newlineIdx) : part).trim()
    const body = newlineIdx >= 0 ? part.slice(newlineIdx + 1).trim() : ''

    const wc = countWords(body)
    const accent = getAccent(title)
    const pullQuoteResult = isRiskFactorsSection(title) ? null : extractPullQuote(body, wc)

    sections.push({
      id: slugify(title),
      title,
      content: pullQuoteResult ? pullQuoteResult.strippedContent : body,
      index: i - 1,
      wordCount: wc,
      accentGradient: accent.gradient,
      accentColor: accent.color,
      pullQuote: pullQuoteResult ? pullQuoteResult.quote : null,
    })
  }

  const totalWordCount = countWords(preamble) + sections.reduce((sum, s) => sum + s.wordCount, 0)

  return {
    sections,
    totalWordCount,
    isLongForm: totalWordCount >= 500,
    readingTimeMinutes: Math.max(1, Math.ceil(totalWordCount / 238)),
    preamble,
  }
}
