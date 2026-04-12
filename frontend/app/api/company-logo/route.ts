import { NextRequest, NextResponse } from 'next/server'

const SYMBOL_RE = /^[A-Z0-9.-]{1,32}$/

const resolveLogoToken = () =>
  process.env.EODHD_API_KEY ||
  process.env.EODHD_TOKEN

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const symbol = searchParams.get('symbol')

  if (!symbol) {
    return NextResponse.json({ error: 'Missing symbol parameter' }, { status: 400 })
  }

  const normalizedSymbol = symbol.trim().toUpperCase()
  if (!SYMBOL_RE.test(normalizedSymbol)) {
    return NextResponse.json({ error: 'Invalid symbol parameter' }, { status: 400 })
  }

  const apiToken = resolveLogoToken()
  if (!apiToken) {
    return NextResponse.json({ error: 'Logo unavailable' }, { status: 404 })
  }

  const upstreamUrl = `https://eodhd.com/api/logo/${encodeURIComponent(normalizedSymbol)}?api_token=${encodeURIComponent(apiToken)}`

  try {
    const response = await fetch(upstreamUrl, { cache: 'force-cache' })
    if (!response.ok) {
      return NextResponse.json({ error: `Logo not available for ${normalizedSymbol}` }, { status: response.status })
    }
    const contentType = response.headers.get('content-type') ?? 'image/png'
    if (!contentType.toLowerCase().startsWith('image/')) {
      return NextResponse.json({ error: 'Unexpected upstream content type' }, { status: 502 })
    }
    const buffer = await response.arrayBuffer()
    return new NextResponse(buffer, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=86400',
      },
    })
  } catch (error) {
    console.error('Logo proxy error', error)
    return NextResponse.json({ error: 'Unable to retrieve logo right now' }, { status: 502 })
  }
}
