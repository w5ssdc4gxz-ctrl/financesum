'use client'

import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react'
import { motion } from 'framer-motion'

type ButtonColor = 'primary' | 'secondary' | 'success' | 'danger' | 'warning' | 'ghost'
type ButtonSize = 'sm' | 'md' | 'lg' | 'xl'

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'color'> {
  color?: ButtonColor
  size?: ButtonSize
  children: ReactNode
  isLoading?: boolean
  leftIcon?: ReactNode
  rightIcon?: ReactNode
  asMotion?: boolean
}

const colorClasses: Record<ButtonColor, string> = {
  primary: 'bg-gradient-to-r from-primary-600 to-accent-600 hover:from-primary-700 hover:to-accent-700 text-white shadow-premium hover:shadow-premium-lg',
  secondary: 'bg-white/10 hover:bg-white/20 text-white border border-white/20 hover:border-primary-500',
  success: 'bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-700 hover:to-emerald-700 text-white shadow-premium',
  danger: 'bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-700 hover:to-rose-700 text-white shadow-premium',
  warning: 'bg-gradient-to-r from-yellow-600 to-orange-600 hover:from-yellow-700 hover:to-orange-700 text-white shadow-premium',
  ghost: 'bg-transparent hover:bg-slate-100 dark:hover:bg-white/5 text-slate-600 dark:text-gray-300 hover:text-slate-900 dark:hover:text-white border border-transparent hover:border-slate-200 dark:hover:border-white/10'
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-4 py-2 text-sm rounded-lg',
  md: 'px-6 py-3 text-base rounded-lg',
  lg: 'px-8 py-4 text-lg rounded-xl',
  xl: 'px-12 py-5 text-xl rounded-xl'
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      color = 'primary',
      size = 'md',
      children,
      className = '',
      disabled = false,
      isLoading = false,
      leftIcon,
      rightIcon,
      asMotion = true,
      ...props
    },
    ref
  ) => {
    const baseClasses = 'inline-flex items-center justify-center gap-2 font-semibold transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100'
    const classes = `${baseClasses} ${colorClasses[color]} ${sizeClasses[size]} ${className}`

    const content = (
      <>
        {isLoading && (
          <div className="spinner w-5 h-5 border-2"></div>
        )}
        {!isLoading && leftIcon && <span className="flex-shrink-0">{leftIcon}</span>}
        <span>{children}</span>
        {!isLoading && rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
      </>
    )

    if (asMotion) {
      const { onDrag, onDragStart, onDragEnd, ...restProps } = props
      return (
        <motion.button
          ref={ref}
          className={classes}
          disabled={disabled || isLoading}
          whileHover={{ scale: disabled || isLoading ? 1 : 1.05 }}
          whileTap={{ scale: disabled || isLoading ? 1 : 0.95 }}
          {...(restProps as any)}
        >
          {content}
        </motion.button>
      )
    }

    return (
      <button
        ref={ref}
        className={classes}
        disabled={disabled || isLoading}
        {...props}
      >
        {content}
      </button>
    )
  }
)

Button.displayName = 'Button'
