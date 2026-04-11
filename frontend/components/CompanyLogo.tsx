'use client'

/* eslint-disable @next/next/no-img-element -- Backend logo proxy needs raw img error fallback. */

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
  const src = `/api/backend/api/v1/companies/logo/${cleanTicker}?exchange=${exchange}`

  return (
    <img 
      src={src} 
      alt={`${ticker} logo`} 
      className={cn("object-contain", className)}
      onError={() => setError(true)}
    />
  )
}
