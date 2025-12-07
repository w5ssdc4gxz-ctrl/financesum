import { NextRequest, NextResponse } from 'next/server'

const DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000'
const BACKEND_BASE_URL =
  process.env.BACKEND_API_URL ||
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
const backendBase = normalizeBaseUrl(BACKEND_BASE_URL)

type Params = {
  path?: string[]
}

async function proxy(request: NextRequest, context: { params: Promise<Params> }) {
  // Next.js 14+: params is now a Promise that must be awaited
  const resolvedParams = await context.params
  const pathSegments = resolvedParams?.path ?? []
  const targetPath = pathSegments.join('/')
  const search = request.nextUrl.search
  const targetUrl = `${backendBase}/${targetPath}${search}`

  const headers = new Headers(request.headers)
  HOP_BY_HOP_HEADERS.forEach(header => headers.delete(header))

  const init: RequestInit & { duplex?: 'half' } = {
    method: request.method,
    headers,
    redirect: 'manual',
  }

  if (!['GET', 'HEAD'].includes(request.method) && request.body) {
    init.body = request.body
    init.duplex = 'half'
  }

  try {
    const response = await fetch(targetUrl, init)
    const responseHeaders = new Headers(response.headers)
    HOP_BY_HOP_HEADERS.forEach(header => responseHeaders.delete(header))

    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    })
  } catch (error: any) {
    return NextResponse.json(
      {
        error: 'Unable to reach backend API',
        detail: error?.message || 'Unknown error',
      },
      { status: 502 },
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
