'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'

interface CompanyLogoProps {
  ticker: string
  exchange?: string
  className?: string
  fallback?: React.ReactNode
}

export function CompanyLogo({ ticker, exchange = 'US', className, fallback }: CompanyLogoProps) {
  const [error, setError] = useState(false)

  if (error) {
    return <>{fallback || <div className={cn("flex items-center justify-center bg-gray-100 text-xs font-bold text-gray-500", className)}>{ticker.slice(0, 2)}</div>}</>
  }

  const cleanTicker = ticker.split('.')[0] // Handle cases where ticker is passed as 'AAPL.US'
  // Use the backend proxy - version is hardcoded to v1 for now, or could use env var if exposed
  const src = `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/companies/logo/${cleanTicker}?exchange=${exchange}`

  return (
    <img 
      src={src} 
      alt={`${ticker} logo`} 
      className={cn("object-contain", className)}
      onError={() => setError(true)}
    />
  )
}
