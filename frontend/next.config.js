/** @type {import('next').NextConfig} */
const { PHASE_DEVELOPMENT_SERVER } = require('next/constants')

const baseConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
}

const securityHeaders = [
  { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=()' },
]

module.exports = (phase) => {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER

  return {
    ...baseConfig,
    // Keep dev and prod build outputs separate to avoid stale `.next` build artifacts
    // breaking `next dev` (manifest mismatch causes 404s for core Next assets).
    distDir: isDev ? '.next-dev' : '.next',
    turbopack: {
      root: __dirname,
    },
    async headers() {
      // This app is deployed on our own infra (not Vercel), so we must avoid long-lived
      // CDN caching of HTML. Otherwise, users can get "stuck" on old JS bundles (e.g.,
      // slider max = 3000) even after we deploy a fix.
      return [
        {
          source: '/:path*',
          headers: securityHeaders,
        },
        {
          source: '/',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/company/:path*',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/dashboard',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/dashboard/:path*',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/compare',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/billing/:path*',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/signin',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
        {
          source: '/signup',
          headers: [{ key: 'Cache-Control', value: 'no-store' }],
        },
      ]
    },
  }
}










