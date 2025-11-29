import { NextRequest, NextResponse } from 'next/server'

export async function GET(request: NextRequest) {
    const searchParams = request.nextUrl.searchParams
    const ticker = searchParams.get('ticker')
    const exchange = searchParams.get('exchange') || 'US'

    if (!ticker) {
        return new NextResponse('Ticker is required', { status: 400 })
    }

    const apiKey = process.env.EODHD_API_KEY
    if (!apiKey) {
        console.error('EODHD_API_KEY is not set')
        return new NextResponse('Server configuration error', { status: 500 })
    }

    const cleanTicker = ticker.trim().toUpperCase()
    const cleanExchange = exchange.trim().toUpperCase()

    // EODHD Logo API URL
    const url = `https://eodhd.com/api/logo/${cleanTicker}.${cleanExchange}?api_token=${apiKey}`

    try {
        const response = await fetch(url)

        if (!response.ok) {
            return new NextResponse('Logo not found', { status: response.status })
        }

        const contentType = response.headers.get('content-type') || 'image/png'
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
