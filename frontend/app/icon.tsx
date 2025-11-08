import { ImageResponse } from 'next/og'

export const runtime = 'edge'
export const size = {
  width: 32,
  height: 32,
}
export const contentType = 'image/png'

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          display: 'flex',
          height: '100%',
          width: '100%',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: '#111827',
          color: '#22d3ee',
          fontSize: 18,
          fontWeight: 700,
          letterSpacing: 1,
          borderRadius: 8,
        }}
      >
        FS
      </div>
    ),
    {
      ...size,
    }
  )
}

