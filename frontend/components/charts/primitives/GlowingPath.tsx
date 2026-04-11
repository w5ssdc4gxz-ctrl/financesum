'use client'

import React, { useRef, useEffect, useState, useId } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface GlowingPathProps {
  d: string
  className?: string
  strokeWidth?: number
  color?: string
  glowColor?: string
  glowIntensity?: number
  animated?: boolean
  duration?: number
  delay?: number
}

/**
 * GlowingPath - SVG path with animated draw and glow effect
 * 
 * Features:
 * - Path drawing animation
 * - Configurable glow effect
 * - Gradient stroke support
 * - Trailing particle effect (optional)
 */
export function GlowingPath({
  d,
  className,
  strokeWidth = 2,
  color = '#10B981',
  glowColor,
  glowIntensity = 0.4,
  animated = true,
  duration = 1.5,
  delay = 0,
}: GlowingPathProps) {
  const svgId = useId().replace(/:/g, '')
  const pathRef = useRef<SVGPathElement>(null)
  const [pathLength, setPathLength] = useState(0)
  
  useEffect(() => {
    if (pathRef.current) {
      setPathLength(pathRef.current.getTotalLength())
    }
  }, [d])
  
  const effectiveGlowColor = glowColor || color
  const filterId = `glowFilter-${svgId}`
  
  return (
    <g className={className}>
      <defs>
        <filter id={filterId} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur in="SourceGraphic" stdDeviation={glowIntensity * 10} result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      
      {/* Glow layer */}
      <motion.path
        ref={pathRef}
        d={d}
        fill="none"
        stroke={effectiveGlowColor}
        strokeWidth={strokeWidth + 4}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={glowIntensity}
        filter={`url(#${filterId})`}
        initial={animated ? { pathLength: 0 } : undefined}
        animate={animated ? { pathLength: 1 } : undefined}
        transition={{ duration, delay, ease: [0.4, 0, 0.2, 1] }}
        style={animated ? { strokeDasharray: pathLength, strokeDashoffset: pathLength } : undefined}
      />
      
      {/* Main path */}
      <motion.path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={animated ? { pathLength: 0, opacity: 0 } : undefined}
        animate={animated ? { pathLength: 1, opacity: 1 } : undefined}
        transition={{ duration, delay, ease: [0.4, 0, 0.2, 1] }}
      />
    </g>
  )
}

interface AnimatedAreaProps {
  d: string
  className?: string
  gradientId: string
  gradientColors: { offset: string; color: string; opacity?: number }[]
  animated?: boolean
  duration?: number
  delay?: number
}

/**
 * AnimatedArea - SVG area fill with gradient and animation
 */
export function AnimatedArea({
  d,
  className,
  gradientId,
  gradientColors,
  animated = true,
  duration = 1.5,
  delay = 0,
}: AnimatedAreaProps) {
  return (
    <g className={className}>
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="0%" y2="100%">
          {gradientColors.map((stop, i) => (
            <stop
              key={i}
              offset={stop.offset}
              stopColor={stop.color}
              stopOpacity={stop.opacity ?? 1}
            />
          ))}
        </linearGradient>
      </defs>
      
      <motion.path
        d={d}
        fill={`url(#${gradientId})`}
        initial={animated ? { opacity: 0, scaleY: 0 } : undefined}
        animate={animated ? { opacity: 1, scaleY: 1 } : undefined}
        transition={{ duration: duration * 0.8, delay: delay + duration * 0.2, ease: [0.4, 0, 0.2, 1] }}
        style={{ transformOrigin: 'bottom' }}
      />
    </g>
  )
}

interface AnimatedDotsProps {
  points: { x: number; y: number }[]
  className?: string
  color?: string
  radius?: number
  animated?: boolean
  staggerDelay?: number
  delay?: number
}

/**
 * AnimatedDots - Data point dots with staggered reveal
 */
export function AnimatedDots({
  points,
  className,
  color = '#10B981',
  radius = 4,
  animated = true,
  staggerDelay = 0.05,
  delay = 0.5,
}: AnimatedDotsProps) {
  return (
    <g className={className}>
      {points.map((point, i) => (
        <motion.circle
          key={i}
          cx={point.x}
          cy={point.y}
          r={radius}
          fill={color}
          initial={animated ? { scale: 0, opacity: 0 } : undefined}
          animate={animated ? { scale: 1, opacity: 1 } : undefined}
          transition={{
            duration: 0.3,
            delay: delay + i * staggerDelay,
            ease: [0.34, 1.56, 0.64, 1], // Back easing
          }}
        />
      ))}
    </g>
  )
}

interface PulsingDotProps {
  cx: number
  cy: number
  color?: string
  size?: number
  pulseScale?: number
}

/**
 * PulsingDot - Animated pulsing indicator dot
 */
export function PulsingDot({
  cx,
  cy,
  color = '#10B981',
  size = 6,
  pulseScale = 2,
}: PulsingDotProps) {
  return (
    <g>
      {/* Pulse ring */}
      <motion.circle
        cx={cx}
        cy={cy}
        r={size}
        fill="none"
        stroke={color}
        strokeWidth={2}
        initial={{ scale: 1, opacity: 0.8 }}
        animate={{ scale: pulseScale, opacity: 0 }}
        transition={{
          duration: 1.5,
          repeat: Infinity,
          ease: 'easeOut',
        }}
      />
      {/* Static dot */}
      <circle cx={cx} cy={cy} r={size / 2} fill={color} />
    </g>
  )
}

interface GradientLineProps {
  x1: number
  y1: number
  x2: number
  y2: number
  gradientId: string
  startColor: string
  endColor: string
  strokeWidth?: number
  animated?: boolean
}

/**
 * GradientLine - Line with gradient stroke
 */
export function GradientLine({
  x1,
  y1,
  x2,
  y2,
  gradientId,
  startColor,
  endColor,
  strokeWidth = 2,
  animated = true,
}: GradientLineProps) {
  const length = Math.sqrt(Math.pow(x2 - x1, 2) + Math.pow(y2 - y1, 2))
  
  return (
    <g>
      <defs>
        <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor={startColor} />
          <stop offset="100%" stopColor={endColor} />
        </linearGradient>
      </defs>
      
      <motion.line
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
        stroke={`url(#${gradientId})`}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        initial={animated ? { pathLength: 0 } : undefined}
        animate={animated ? { pathLength: 1 } : undefined}
        transition={{ duration: 0.8, ease: [0.4, 0, 0.2, 1] }}
        style={animated ? { strokeDasharray: length, strokeDashoffset: length } : undefined}
      />
    </g>
  )
}

/**
 * GridLines - Animated chart grid lines
 */
export function GridLines({
  width,
  height,
  rows = 4,
  cols = 6,
  color = 'currentColor',
  opacity = 0.1,
  animated = true,
}: {
  width: number
  height: number
  rows?: number
  cols?: number
  color?: string
  opacity?: number
  animated?: boolean
}) {
  const horizontalLines = Array.from({ length: rows + 1 }, (_, i) => ({
    y: (height / rows) * i,
  }))
  
  const verticalLines = Array.from({ length: cols + 1 }, (_, i) => ({
    x: (width / cols) * i,
  }))
  
  return (
    <g className="grid-lines" opacity={opacity}>
      {horizontalLines.map((line, i) => (
        <motion.line
          key={`h-${i}`}
          x1={0}
          y1={line.y}
          x2={width}
          y2={line.y}
          stroke={color}
          strokeWidth={1}
          initial={animated ? { scaleX: 0 } : undefined}
          animate={animated ? { scaleX: 1 } : undefined}
          transition={{ duration: 0.5, delay: i * 0.05 }}
          style={{ transformOrigin: 'left' }}
        />
      ))}
      {verticalLines.map((line, i) => (
        <motion.line
          key={`v-${i}`}
          x1={line.x}
          y1={0}
          x2={line.x}
          y2={height}
          stroke={color}
          strokeWidth={1}
          initial={animated ? { scaleY: 0 } : undefined}
          animate={animated ? { scaleY: 1 } : undefined}
          transition={{ duration: 0.5, delay: 0.2 + i * 0.05 }}
          style={{ transformOrigin: 'top' }}
        />
      ))}
    </g>
  )
}

export { GlowingPath as default }
