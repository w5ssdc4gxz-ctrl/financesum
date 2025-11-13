import { NextRequest, NextResponse } from 'next/server'

const resolveLogoToken = () =>
  process.env.EODHD_API_KEY ||
  process.env.NEXT_PUBLIC_EODHD_API_KEY ||
  process.env.EODHD_TOKEN ||
  process.env.NEXT_PUBLIC_EODHD_TOKEN

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const symbol = searchParams.get('symbol')

  if (!symbol) {
    return NextResponse.json({ error: 'Missing symbol parameter' }, { status: 400 })
  }

  const apiToken = resolveLogoToken()
  if (!apiToken) {
    return NextResponse.json(
      { error: 'EODHD_API_KEY not configured on the server. Add it to your environment to enable logos.' },
      { status: 500 },
    )
  }

  const normalizedSymbol = symbol.trim().toUpperCase()
  const upstreamUrl = `https://eodhd.com/api/logo/${normalizedSymbol}?api_token=${apiToken}`

  try {
    const response = await fetch(upstreamUrl, { cache: 'force-cache' })
    if (!response.ok) {
      return NextResponse.json({ error: `Logo not available for ${normalizedSymbol}` }, { status: response.status })
    }
    const buffer = await response.arrayBuffer()
    return new NextResponse(buffer, {
      status: 200,
      headers: {
        'Content-Type': response.headers.get('content-type') ?? 'image/png',
        'Cache-Control': 'public, max-age=86400',
      },
    })
  } catch (error) {
    console.error('Logo proxy error', error)
    return NextResponse.json({ error: 'Unable to retrieve logo right now' }, { status: 502 })
  }
}
