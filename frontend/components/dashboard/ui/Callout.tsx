'use client'

import { ReactNode } from 'react'
import { cn } from '@/lib/utils'

type CalloutVariant = 'default' | 'success' | 'warning' | 'error' | 'neutral'

const variantStyles: Record<CalloutVariant, { container: string; badge: string }> = {
  default: {
    container: 'bg-indigo-50 text-indigo-900 border-indigo-100',
    badge: 'text-indigo-600 bg-indigo-100',
  },
  success: {
    container: 'bg-emerald-50 text-emerald-900 border-emerald-100',
    badge: 'text-emerald-600 bg-emerald-100',
  },
  warning: {
    container: 'bg-amber-50 text-amber-900 border-amber-100',
    badge: 'text-amber-600 bg-amber-100',
  },
  error: {
    container: 'bg-rose-50 text-rose-900 border-rose-100',
    badge: 'text-rose-600 bg-rose-100',
  },
  neutral: {
    container: 'bg-slate-100 text-slate-800 border-slate-200',
    badge: 'text-slate-600 bg-white',
  },
}

interface CalloutProps {
  title: string
  description?: string
  icon?: ReactNode
  variant?: CalloutVariant
  action?: ReactNode
  className?: string
  children?: ReactNode
}

export function DashboardCallout({
  title,
  description,
  icon,
  variant = 'default',
  action,
  className,
  children,
}: CalloutProps) {
  const styles = variantStyles[variant]

  return (
    <div
      className={cn(
        'rounded-2xl border p-5 shadow-sm transition-all duration-300 hover:shadow-md',
        styles.container,
        className,
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          {icon && (
            <div className={cn('flex h-10 w-10 items-center justify-center rounded-xl', styles.badge)}>{icon}</div>
          )}
          <div>
            <p className="text-base font-semibold leading-snug">{title}</p>
            {description && <p className="text-sm text-slate-600/80">{description}</p>}
          </div>
        </div>
        {action && <div className="text-sm font-semibold">{action}</div>}
      </div>
      {children && <div className="mt-4 text-sm leading-relaxed">{children}</div>}
    </div>
  )
}
