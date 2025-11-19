'use client'

import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import { cn } from '@/lib/utils'

interface DashboardCheckboxProps extends CheckboxPrimitive.CheckboxProps {
  label?: string
  description?: string
}

export function DashboardCheckbox({ label, description, className, ...props }: DashboardCheckboxProps) {
  return (
    <label className={cn('flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-white p-3 text-sm shadow-sm transition hover:border-slate-300', className)}>
      <CheckboxPrimitive.Root
        {...props}
        className="mt-0.5 flex h-4 w-4 items-center justify-center rounded border border-slate-300 data-[state=checked]:border-transparent data-[state=checked]:bg-indigo-600"
      >
        <CheckboxPrimitive.Indicator className="text-white">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
            <path d="M12.0002 5.33334L6.66683 10.6667L4.00016 8.00001" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      <div>
        {label && <p className="font-semibold text-slate-900">{label}</p>}
        {description && <p className="text-xs text-slate-500">{description}</p>}
      </div>
    </label>
  )
}
