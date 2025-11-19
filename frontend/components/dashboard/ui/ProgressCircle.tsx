'use client'

import { ReactNode } from 'react'
import { cn } from '@/lib/utils'

type ProgressVariant = 'default' | 'neutral' | 'warning' | 'error' | 'success'

const variantClasses: Record<ProgressVariant, { track: string; ring: string }> = {
  default: { track: 'stroke-indigo-100', ring: 'stroke-indigo-500' },
  neutral: { track: 'stroke-slate-200', ring: 'stroke-slate-500' },
  warning: { track: 'stroke-amber-100', ring: 'stroke-amber-500' },
  error: { track: 'stroke-rose-100', ring: 'stroke-rose-500' },
  success: { track: 'stroke-emerald-100', ring: 'stroke-emerald-500' },
}

interface ProgressCircleProps {
  value?: number
  max?: number
  radius?: number
  strokeWidth?: number
  children?: ReactNode
  variant?: ProgressVariant
  className?: string
}

export function ProgressCircle({
  value = 0,
  max = 100,
  radius = 38,
  strokeWidth = 8,
  children,
  variant = 'default',
  className,
}: ProgressCircleProps) {
  const safeValue = Math.min(max, Math.max(0, value))
  const normalizedRadius = radius - strokeWidth / 2
  const circumference = normalizedRadius * 2 * Math.PI
  const offset = circumference - (safeValue / max) * circumference
  const styles = variantClasses[variant]

  return (
    <div
      className={cn('relative flex items-center justify-center', className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuenow={safeValue}
      aria-valuemax={max}
    >
      <svg width={radius * 2} height={radius * 2} className="-rotate-90 transform">
        <circle
          cx={radius}
          cy={radius}
          r={normalizedRadius}
          strokeWidth={strokeWidth}
          fill="transparent"
          className={styles.track}
        />
        <circle
          cx={radius}
          cy={radius}
          r={normalizedRadius}
          strokeWidth={strokeWidth}
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={offset}
          fill="transparent"
          className={cn(styles.ring, 'transition-all duration-300 ease-in-out')}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  )
}
