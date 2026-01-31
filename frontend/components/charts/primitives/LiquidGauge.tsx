'use client'

import React, { useRef, useEffect, useState } from 'react'
import { motion, useSpring, useTransform, useMotionValue } from 'framer-motion'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { cn } from '@/lib/utils'
import { chartColors } from '@/lib/chart-utils'

interface LiquidGaugeProps {
  value: number // 0-100
  size?: number
  strokeWidth?: number
  className?: string
  showValue?: boolean
  label?: string
  colorScheme?: 'auto' | 'emerald' | 'sky' | 'violet' | 'amber'
  animated?: boolean
  glowEnabled?: boolean
}

/**
 * LiquidGauge - Organic, liquid-like radial gauge
 * 
 * Features:
 * - Smooth arc fill animation
 * - Animated gradient that shifts colors
 * - Glow effect on the arc
 * - Spring physics for value changes
 */
export function LiquidGauge({
  value,
  size = 120,
  strokeWidth = 10,
  className,
  showValue = true,
  label,
  colorScheme = 'auto',
  animated = true,
  glowEnabled = true,
}: LiquidGaugeProps) {
  const [mounted, setMounted] = useState(false)
  const gaugeRef = useRef<SVGSVGElement>(null)
  
  // Clamp value
  const clampedValue = Math.max(0, Math.min(100, value))
  
  // Spring animation for smooth value transitions
  const springValue = useSpring(0, { stiffness: 100, damping: 20, mass: 0.5 })
  
  useEffect(() => {
    setMounted(true)
    if (animated) {
      springValue.set(clampedValue)
    }
  }, [clampedValue, animated, springValue])
  
  // Calculate arc geometry
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const arcLength = circumference * 0.75 // 270 degrees
  
  // Animated stroke offset
  const strokeDashoffset = useTransform(
    springValue,
    [0, 100],
    [arcLength, 0]
  )
  
  // Determine color based on value or explicit scheme
  const getColors = () => {
    if (colorScheme !== 'auto') {
      const schemes = {
        emerald: { start: '#10B981', end: '#059669', glow: 'rgba(16, 185, 129, 0.4)' },
        sky: { start: '#0EA5E9', end: '#0284C7', glow: 'rgba(14, 165, 233, 0.4)' },
        violet: { start: '#8B5CF6', end: '#7C3AED', glow: 'rgba(139, 92, 246, 0.4)' },
        amber: { start: '#F59E0B', end: '#D97706', glow: 'rgba(245, 158, 11, 0.4)' },
      }
      return schemes[colorScheme]
    }
    
    // Auto color based on value
    if (clampedValue >= 70) return { start: '#10B981', end: '#059669', glow: 'rgba(16, 185, 129, 0.4)' }
    if (clampedValue >= 40) return { start: '#0EA5E9', end: '#0284C7', glow: 'rgba(14, 165, 233, 0.4)' }
    if (clampedValue >= 20) return { start: '#F59E0B', end: '#D97706', glow: 'rgba(245, 158, 11, 0.4)' }
    return { start: '#EF4444', end: '#DC2626', glow: 'rgba(239, 68, 68, 0.4)' }
  }
  
  const colors = getColors()
  const gradientId = `liquidGaugeGradient-${Math.random().toString(36).slice(2)}`
  const glowId = `liquidGaugeGlow-${Math.random().toString(36).slice(2)}`
  
  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      <svg
        ref={gaugeRef}
        width={size}
        height={size}
        className="transform -rotate-[135deg]"
        style={{ overflow: 'visible' }}
      >
        <defs>
          {/* Animated gradient */}
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
            <motion.stop
              offset="0%"
              animate={{
                stopColor: [colors.start, colors.end, colors.start],
              }}
              transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
            />
            <stop offset="100%" stopColor={colors.end} />
          </linearGradient>
          
          {/* Glow filter */}
          {glowEnabled && (
            <filter id={glowId} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          )}
        </defs>
        
        {/* Background track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeDasharray={arcLength}
          strokeDashoffset={0}
          strokeLinecap="round"
          className="text-gray-100 dark:text-gray-800"
        />
        
        {/* Animated foreground arc */}
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeWidth={strokeWidth}
          strokeDasharray={arcLength}
          style={{ strokeDashoffset: animated ? strokeDashoffset : arcLength * (1 - clampedValue / 100) }}
          strokeLinecap="round"
          filter={glowEnabled ? `url(#${glowId})` : undefined}
          initial={animated ? { strokeDashoffset: arcLength } : undefined}
        />
        
        {/* End cap glow dot */}
        {glowEnabled && mounted && (
          <motion.circle
            cx={size / 2 + radius * Math.cos((Math.PI * (135 + 270 * clampedValue / 100)) / 180)}
            cy={size / 2 + radius * Math.sin((Math.PI * (135 + 270 * clampedValue / 100)) / 180)}
            r={strokeWidth / 2 + 2}
            fill={colors.start}
            className="transform rotate-[135deg] origin-center"
            style={{
              transformBox: 'fill-box',
              transformOrigin: 'center',
            }}
            initial={{ opacity: 0, scale: 0 }}
            animate={{ opacity: 0.6, scale: 1 }}
            transition={{ delay: 0.8, duration: 0.3 }}
          />
        )}
      </svg>
      
      {/* Center content */}
      {(showValue || label) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {showValue && (
            <motion.span
              className="text-2xl font-bold text-gray-900 dark:text-gray-100 tabular-nums"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.3, duration: 0.4 }}
            >
              {Math.round(clampedValue)}%
            </motion.span>
          )}
          {label && (
            <motion.span
              className="text-xs text-gray-500 dark:text-gray-400 mt-0.5"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.5 }}
            >
              {label}
            </motion.span>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * MiniGauge - Compact inline gauge for tables/lists
 */
export function MiniGauge({
  value,
  size = 32,
  className,
}: {
  value: number
  size?: number
  className?: string
}) {
  const clampedValue = Math.max(0, Math.min(100, value))
  const strokeWidth = 3
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (clampedValue / 100) * circumference
  
  const color = clampedValue >= 70 ? '#10B981' : clampedValue >= 40 ? '#0EA5E9' : clampedValue >= 20 ? '#F59E0B' : '#EF4444'
  
  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-gray-200 dark:text-gray-700"
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1, ease: [0.4, 0, 0.2, 1] }}
        />
      </svg>
      <span className="absolute text-[10px] font-bold text-gray-700 dark:text-gray-300">
        {Math.round(clampedValue)}
      </span>
    </div>
  )
}

/**
 * HorizontalGauge - Linear progress bar style gauge
 */
export function HorizontalGauge({
  value,
  height = 8,
  className,
  showLabel = true,
  colorScheme = 'auto',
}: {
  value: number
  height?: number
  className?: string
  showLabel?: boolean
  colorScheme?: 'auto' | 'emerald' | 'sky' | 'violet' | 'amber'
}) {
  const clampedValue = Math.max(0, Math.min(100, value))
  
  const getGradient = () => {
    if (colorScheme !== 'auto') {
      const gradients = {
        emerald: 'from-emerald-500 to-teal-400',
        sky: 'from-sky-500 to-blue-400',
        violet: 'from-violet-500 to-purple-400',
        amber: 'from-amber-500 to-orange-400',
      }
      return gradients[colorScheme]
    }
    if (clampedValue >= 70) return 'from-emerald-500 to-teal-400'
    if (clampedValue >= 40) return 'from-sky-500 to-blue-400'
    if (clampedValue >= 20) return 'from-amber-500 to-orange-400'
    return 'from-red-500 to-rose-400'
  }
  
  return (
    <div className={cn('w-full', className)}>
      {showLabel && (
        <div className="flex justify-between items-center mb-1">
          <span className="text-xs text-gray-500 dark:text-gray-400">Progress</span>
          <span className="text-xs font-semibold text-gray-700 dark:text-gray-300">{Math.round(clampedValue)}%</span>
        </div>
      )}
      <div 
        className="w-full bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden"
        style={{ height }}
      >
        <motion.div
          className={cn('h-full rounded-full bg-gradient-to-r', getGradient())}
          initial={{ width: 0 }}
          animate={{ width: `${clampedValue}%` }}
          transition={{ duration: 1, ease: [0.4, 0, 0.2, 1] }}
        />
      </div>
    </div>
  )
}

export default LiquidGauge
