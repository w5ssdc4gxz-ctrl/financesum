/** @type {import('next').NextConfig} */
const { PHASE_DEVELOPMENT_SERVER } = require('next/constants')

const baseConfig = {
  reactStrictMode: true,
  swcMinify: true,
  async rewrites() {
    const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
    return [
      {
        source: '/api/backend/:path*',
        destination: `${backendUrl}/:path*`,
      },
    ]
  },
}

module.exports = (phase) => {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER

  return {
    ...baseConfig,
    // Keep dev and prod build outputs separate to avoid stale `.next` build artifacts
    // breaking `next dev` (manifest mismatch causes 404s for core Next assets).
    distDir: isDev ? '.next-dev' : '.next',
  }
}
















