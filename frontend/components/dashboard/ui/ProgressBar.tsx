'use client'

import { cn } from '@/lib/utils'

type ProgressVariant = 'default' | 'neutral' | 'warning' | 'error' | 'success'

const variantClasses: Record<ProgressVariant, { track: string; bar: string }> = {
  default: { track: 'bg-indigo-100', bar: 'bg-indigo-500' },
  neutral: { track: 'bg-slate-200', bar: 'bg-slate-500' },
  warning: { track: 'bg-amber-100', bar: 'bg-amber-500' },
  error: { track: 'bg-rose-100', bar: 'bg-rose-500' },
  success: { track: 'bg-emerald-100', bar: 'bg-emerald-500' },
}

interface ProgressBarProps {
  value?: number
  max?: number
  label?: string
  animate?: boolean
  className?: string
  variant?: ProgressVariant
}

export function ProgressBar({
  value = 0,
  max = 100,
  label,
  animate = true,
  variant = 'default',
  className,
}: ProgressBarProps) {
  const ratio = max ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const styles = variantClasses[variant]

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className={cn('relative h-2 rounded-full', styles.track)}>
        <div
          className={cn('h-full rounded-full', styles.bar, animate && 'transition-all duration-300 ease-in-out')}
          style={{ width: `${ratio}%` }}
        />
      </div>
      {label && <span className="text-xs font-medium text-slate-500">{label}</span>}
    </div>
  )
}
