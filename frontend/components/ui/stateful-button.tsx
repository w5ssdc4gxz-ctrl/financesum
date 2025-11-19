'use client'

import React, { useCallback, useState } from 'react'

type ButtonStatus = 'idle' | 'loading' | 'success' | 'error'

type StatefulButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  onClick?: () => void | Promise<void>
}

const statusIcon = (status: ButtonStatus) => {
  if (status === 'loading') {
    return (
      <span className="inline-flex h-4 w-4 animate-spin rounded-full border-2 border-primary-200 border-t-transparent" />
    )
  }
  if (status === 'success') {
    return (
      <svg className="h-4 w-4 text-emerald-300" fill="none" viewBox="0 0 24 24">
        <path stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  if (status === 'error') {
    return (
      <svg className="h-4 w-4 text-red-300" fill="none" viewBox="0 0 24 24">
        <path stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
      </svg>
    )
  }
  return null
}

export function Button({ onClick, children, className = '', disabled, ...rest }: StatefulButtonProps) {
  const [status, setStatus] = useState<ButtonStatus>('idle')

  const handleClick = useCallback(async () => {
    if (!onClick || disabled) return
    if (status === 'loading') return

    try {
      const result = onClick()
      if (result && typeof (result as Promise<unknown>).then === 'function') {
        setStatus('loading')
        await result
      }
      setStatus('success')
      setTimeout(() => setStatus('idle'), 1200)
    } catch {
      setStatus('error')
      setTimeout(() => setStatus('idle'), 1500)
    }
  }, [onClick, disabled, status])

  const baseClasses =
    'inline-flex items-center justify-center gap-2 rounded-lg border px-5 py-2.5 text-sm font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/60'

  const statusClasses =
    status === 'loading'
      ? 'border-primary-400 bg-primary-500/10 text-primary-100'
      : status === 'success'
        ? 'border-emerald-400 bg-emerald-500/10 text-emerald-100'
        : status === 'error'
          ? 'border-red-400 bg-red-500/10 text-red-100'
          : 'border-white/20 bg-white/10 text-white hover:border-primary-400 hover:text-primary-100 hover:bg-primary-500/10'

  return (
    <button
      type="button"
      className={`${baseClasses} ${statusClasses} ${className}`}
      onClick={handleClick}
      disabled={disabled || status === 'loading'}
      {...rest}
    >
      {statusIcon(status)}
      <span>{children}</span>
    </button>
  )
}

export default Button
