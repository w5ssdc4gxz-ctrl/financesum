import { NextRequest, NextResponse } from 'next/server'

const TICKER_RE = /^[A-Z0-9.-]{1,24}$/
const EXCHANGE_RE = /^[A-Z0-9]{1,10}$/

export async function GET(request: NextRequest) {
    const searchParams = request.nextUrl.searchParams
    const ticker = searchParams.get('ticker')
    const exchange = searchParams.get('exchange') || 'US'

    if (!ticker) {
        return new NextResponse('Ticker is required', { status: 400 })
    }

    const cleanTicker = ticker.trim().toUpperCase()
    const cleanExchange = exchange.trim().toUpperCase()
    if (!TICKER_RE.test(cleanTicker) || !EXCHANGE_RE.test(cleanExchange)) {
        return new NextResponse('Invalid ticker or exchange', { status: 400 })
    }

    const apiKey = process.env.EODHD_API_KEY
    if (!apiKey) {
        return new NextResponse('Logo unavailable', { status: 404 })
    }

    // EODHD Logo API URL
    const symbol = encodeURIComponent(`${cleanTicker}.${cleanExchange}`)
    const url = `https://eodhd.com/api/logo/${symbol}?api_token=${encodeURIComponent(apiKey)}`

    try {
        const response = await fetch(url)

        if (!response.ok) {
            return new NextResponse('Logo not found', { status: response.status })
        }

        const contentType = response.headers.get('content-type') || 'image/png'
        if (!contentType.toLowerCase().startsWith('image/')) {
            return new NextResponse('Unexpected upstream content type', { status: 502 })
        }
        const arrayBuffer = await response.arrayBuffer()
        const buffer = Buffer.from(arrayBuffer)

        return new NextResponse(buffer, {
            headers: {
                'Content-Type': contentType,
                'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
            },
        })
    } catch (error) {
        console.error('Error fetching logo:', error)
        return new NextResponse('Error fetching logo', { status: 500 })
    }
}
