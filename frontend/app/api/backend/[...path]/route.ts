import { NextRequest, NextResponse } from 'next/server'
import { Agent, fetch as undiciFetch } from 'undici'

const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000'
const BACKEND_BASE_URL =
  process.env.BACKEND_API_URL ||
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  DEFAULT_BACKEND_URL

const normalizeBaseUrl = (rawValue: string) => {
  const value = rawValue.trim()
  if (!value) return DEFAULT_BACKEND_URL

  try {
    const parsed = new URL(value)
    if (parsed.hostname === 'localhost') {
      parsed.hostname = '127.0.0.1'
    }
    const normalizedPath = parsed.pathname.replace(/\/+$/, '')
    return `${parsed.protocol}//${parsed.host}${normalizedPath}`.replace(/\/+$/, '')
  } catch {
    return value.replace(/\/+$/, '')
  }
}
const HOP_BY_HOP_HEADERS = [
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'host',
  'content-length',
]
const SUMMARY_ENDPOINT_RE = /^api\/v1\/filings\/[^/]+\/summary$/i
const SUMMARY_RETRYABLE_422_CODES = new Set([
  'SUMMARY_ONE_SHOT_CONTRACT_FAILED',
  'SUMMARY_CONTRACT_FAILED',
  'SUMMARY_BUDGET_EXCEEDED',
  'INSUFFICIENT_NUMERIC_KEY_METRICS',
])
const backendBase = normalizeBaseUrl(BACKEND_BASE_URL)
const parsedBackendBase = (() => {
  try {
    return new URL(backendBase)
  } catch {
    return null
  }
})()
const isCloudConsoleBackend =
  backendBase.includes('console.cloud.google.com') ||
  parsedBackendBase?.hostname === 'console.cloud.google.com'

// Keep proxy timeout slightly below Cloud Run's max request timeout (60m).
const PROXY_REQUEST_TIMEOUT_MS = 59 * 60 * 1000

// Cloud Run summary generation can exceed the default undici headers timeout (~300s).
// Disable undici's internal request timers and rely on the explicit AbortController below.
const longRunningProxyDispatcher = new Agent({
  headersTimeout: 0,
  bodyTimeout: 0,
})

type Params = {
  path?: string[]
}

const cloneForwardHeaders = (headers: Headers): Headers => {
  const cloned = new Headers(headers)
  HOP_BY_HOP_HEADERS.forEach(header => cloned.delete(header))
  return cloned
}

const responseHeadersForClient = (response: any): Headers => {
  const responseHeaders = new Headers()
  response.headers.forEach((value: string, key: string) => {
    responseHeaders.set(key, value)
  })
  HOP_BY_HOP_HEADERS.forEach(header => responseHeaders.delete(header))
  return responseHeaders
}

const parseJsonSafely = (value: string): any | null => {
  const text = String(value || '').trim()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

const extractSummaryFailureCode = (payload: any): string => {
  if (!payload || typeof payload !== 'object') return ''
  const detail =
    payload.detail && typeof payload.detail === 'object'
      ? payload.detail
      : null
  const code = detail?.failure_code ?? payload.failure_code
  return String(code || '').trim()
}

type SummaryRetryPlan = {
  bodies: string[]
  explicitTargetLocked: boolean
  explicitTargetValue: number | null
}

const buildSummaryRetryPlan = (requestBodyText: string): SummaryRetryPlan => {
  const parsed = parseJsonSafely(requestBodyText)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {
      bodies: [],
      explicitTargetLocked: false,
      explicitTargetValue: null,
    }
  }

  const payload = { ...(parsed as Record<string, any>) }
  const retryBodies: string[] = []
  const pushRetryBody = (nextPayload: Record<string, any>) => {
    retryBodies.push(JSON.stringify(nextPayload))
  }

  const explicitTargetValueRaw = Number(payload.target_length)
  const hasExplicitTargetLength = Number.isFinite(explicitTargetValueRaw)
  const healthRating =
    payload.health_rating && typeof payload.health_rating === 'object'
      ? { ...(payload.health_rating as Record<string, any>) }
      : null
  const hasEnabledHealthRating = Boolean(healthRating?.enabled)

  if (hasExplicitTargetLength) {
    // Explicit target requests are hard-contract requests.
    // Retries can relax options, but target_length must be preserved.
    if (hasEnabledHealthRating) {
      pushRetryBody({
        ...payload,
        health_rating: {
          ...healthRating,
          enabled: false,
        },
      })
    }

    const defaultModeWithTarget = {
      ...payload,
      mode: 'default',
      target_length: payload.target_length,
    }
    pushRetryBody(defaultModeWithTarget)
    pushRetryBody({
      ...defaultModeWithTarget,
      health_rating: {
        ...(healthRating || {}),
        enabled: false,
      },
    })
  } else {
    if (hasEnabledHealthRating) {
      pushRetryBody({
        ...payload,
        health_rating: {
          ...healthRating,
          enabled: false,
        },
      })
    }

    // Non-explicit target requests keep best-effort fallback behavior.
    pushRetryBody({})
    pushRetryBody({ mode: 'default' })
    pushRetryBody({
      mode: 'default',
      health_rating: {
        enabled: false,
      },
    })
  }

  const seen = new Set<string>()
  const dedupedBodies = retryBodies.filter(body => {
    if (!body || seen.has(body)) return false
    seen.add(body)
    return true
  })

  return {
    bodies: dedupedBodies,
    explicitTargetLocked: hasExplicitTargetLength,
    explicitTargetValue: hasExplicitTargetLength ? Number(explicitTargetValueRaw) : null,
  }
}

async function proxy(request: NextRequest, context: { params: Promise<Params> }) {
  if (isCloudConsoleBackend) {
    return NextResponse.json(
      {
        error: 'Misconfigured backend URL',
        detail:
          'Your backend base URL points to the Google Cloud Console (console.cloud.google.com). Set BACKEND_API_URL (or BACKEND_URL) to your Cloud Run service URL (https://*.a.run.app), not the Console page URL.',
      },
      { status: 500 },
    )
  }

  // Next.js 14+: params is now a Promise that must be awaited
  const resolvedParams = await context.params
  const pathSegments = resolvedParams?.path ?? []
  const targetPath = pathSegments.join('/')
  const search = request.nextUrl.search
  const targetUrl = `${backendBase}/${targetPath}${search}`

  const headers = new Headers(request.headers)
  HOP_BY_HOP_HEADERS.forEach(header => headers.delete(header))

  const init: RequestInit & { duplex?: 'half'; dispatcher?: Agent } = {
    method: request.method,
    headers,
    redirect: 'manual',
    dispatcher: longRunningProxyDispatcher,
  }

  if (!['GET', 'HEAD'].includes(request.method) && request.body) {
    init.body = request.body
    init.duplex = 'half'
  }

  // Use a long timeout for summary generation which can take several minutes
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), PROXY_REQUEST_TIMEOUT_MS)

  try {
    const isSummaryPostRequest =
      request.method === 'POST' && SUMMARY_ENDPOINT_RE.test(targetPath)

    if (isSummaryPostRequest) {
      const requestBodyText = await request.text()
      const summaryForwardHeaders = cloneForwardHeaders(headers)
      summaryForwardHeaders.delete('content-length')
      if (!summaryForwardHeaders.has('content-type')) {
        summaryForwardHeaders.set('content-type', 'application/json')
      }

      const callSummaryEndpoint = async (bodyText: string) =>
        undiciFetch(targetUrl, {
          method: request.method,
          headers: summaryForwardHeaders,
          body: bodyText,
          redirect: 'manual',
          dispatcher: longRunningProxyDispatcher,
          signal: controller.signal,
        } as any)

      const firstResponse = await callSummaryEndpoint(requestBodyText)
      const firstResponseHeaders = responseHeadersForClient(firstResponse)
      const firstResponseText = await firstResponse.text()

      if (firstResponse.status !== 422) {
        return new NextResponse(firstResponseText, {
          status: firstResponse.status,
          headers: firstResponseHeaders,
        })
      }

      const firstPayload = parseJsonSafely(firstResponseText)
      const failureCode = extractSummaryFailureCode(firstPayload)
      const shouldRetry422 =
        !failureCode || SUMMARY_RETRYABLE_422_CODES.has(failureCode)
      const retryPlan = buildSummaryRetryPlan(requestBodyText)
      const hasFallbackPlan = retryPlan.bodies.length > 0
      let lastFailure = {
        status: firstResponse.status,
        text: firstResponseText,
        headers: firstResponseHeaders,
      }

      if (shouldRetry422 && hasFallbackPlan) {
        for (const retryBody of retryPlan.bodies) {
          const retryResponse = await callSummaryEndpoint(retryBody)
          const retryHeaders = responseHeadersForClient(retryResponse)
          const retryText = await retryResponse.text()
          if (retryResponse.status < 400) {
            retryHeaders.set('x-financesum-summary-fallback-retry', '1')
            retryHeaders.set(
              'x-financesum-summary-retry-policy',
              retryPlan.explicitTargetLocked
                ? 'explicit-target-hard-contract'
                : 'best-effort',
            )
            if (failureCode) {
              retryHeaders.set('x-financesum-initial-failure-code', failureCode)
            }
            if (
              retryPlan.explicitTargetLocked &&
              Number.isFinite(retryPlan.explicitTargetValue)
            ) {
              retryHeaders.set('x-financesum-target-length-locked', '1')
              retryHeaders.set(
                'x-financesum-target-length',
                String(retryPlan.explicitTargetValue),
              )
            }
            return new NextResponse(retryText, {
              status: retryResponse.status,
              headers: retryHeaders,
            })
          }
          lastFailure = {
            status: retryResponse.status,
            text: retryText,
            headers: retryHeaders,
          }
        }
      }

      if (shouldRetry422 && hasFallbackPlan && lastFailure.headers) {
        lastFailure.headers.set('x-financesum-summary-fallback-attempted', '1')
        lastFailure.headers.set(
          'x-financesum-summary-retry-policy',
          retryPlan.explicitTargetLocked
            ? 'explicit-target-hard-contract'
            : 'best-effort',
        )
        if (failureCode) {
          lastFailure.headers.set('x-financesum-initial-failure-code', failureCode)
        }
        if (
          retryPlan.explicitTargetLocked &&
          Number.isFinite(retryPlan.explicitTargetValue)
        ) {
          lastFailure.headers.set('x-financesum-target-length-locked', '1')
          lastFailure.headers.set(
            'x-financesum-target-length',
            String(retryPlan.explicitTargetValue),
          )
        }
      }

      return new NextResponse(lastFailure.text, {
        status: lastFailure.status,
        headers: lastFailure.headers,
      })
    }

    const response = await undiciFetch(
      targetUrl,
      {
        ...init,
        signal: controller.signal,
      } as any,
    )
    clearTimeout(timeoutId)

    const responseHeaders = new Headers()
    response.headers.forEach((value, key) => {
      responseHeaders.set(key, value)
    })
    HOP_BY_HOP_HEADERS.forEach(header => responseHeaders.delete(header))

    return new NextResponse(response.body as any, {
      status: response.status,
      headers: responseHeaders,
    })
  } catch (error: any) {
    clearTimeout(timeoutId)

    const errorName = error?.name || 'Error'
    const errorMessage = error?.message || 'Unknown error'
    const errorCauseCode = error?.cause?.code || error?.code || ''
    const errorCauseName = error?.cause?.name || ''
    console.error('Backend proxy request failed', {
      method: request.method,
      targetUrl,
      errorName,
      errorMessage,
      errorCauseCode,
      errorCauseName,
    })

    if (error?.name === 'AbortError') {
      return NextResponse.json(
        {
          error: 'Request timeout',
          detail: 'The backend request took too long to complete',
        },
        { status: 504 },
      )
    }

    const isUpstreamTimeout =
      errorCauseCode === 'UND_ERR_HEADERS_TIMEOUT' ||
      errorCauseCode === 'UND_ERR_BODY_TIMEOUT' ||
      errorCauseName === 'HeadersTimeoutError' ||
      errorCauseName === 'BodyTimeoutError'

    return NextResponse.json(
      {
        error: isUpstreamTimeout ? 'Upstream request timeout' : 'Unable to reach backend API',
        detail: errorMessage,
      },
      { status: isUpstreamTimeout ? 504 : 502 },
    )
  }
}

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export {
  proxy as GET,
  proxy as POST,
  proxy as PUT,
  proxy as PATCH,
  proxy as DELETE,
  proxy as OPTIONS,
}
