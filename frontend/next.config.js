/** @type {import('next').NextConfig} */
const { PHASE_DEVELOPMENT_SERVER } = require('next/constants')

const baseConfig = {
  reactStrictMode: true,
  swcMinify: true,
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














