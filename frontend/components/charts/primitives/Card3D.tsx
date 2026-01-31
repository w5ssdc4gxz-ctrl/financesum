'use client'

import React, { useRef, useState, useCallback } from 'react'
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'
import { cn } from '@/lib/utils'

interface Card3DProps {
  children: React.ReactNode
  className?: string
  containerClassName?: string
  glareEnabled?: boolean
  tiltMaxAngle?: number
  perspective?: number
  scale?: number
  transitionSpeed?: number
  disabled?: boolean
}

/**
 * Card3D - Premium 3D card with mouse-tracking tilt and optional glare effect
 * 
 * Features:
 * - Smooth spring-physics tilt following mouse position
 * - Optional glare/shine overlay that moves with cursor
 * - Depth-aware inner content layer
 * - Glassmorphism-ready styling
 */
export function Card3D({
  children,
  className,
  containerClassName,
  glareEnabled = true,
  tiltMaxAngle = 10,
  perspective = 1000,
  scale = 1.02,
  transitionSpeed = 400,
  disabled = false,
}: Card3DProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [isHovered, setIsHovered] = useState(false)
  
  // Motion values for smooth animation
  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)
  
  // Spring physics for buttery smooth motion
  const springConfig = { stiffness: 300, damping: 30, mass: 0.5 }
  const rotateX = useSpring(useTransform(mouseY, [-0.5, 0.5], [tiltMaxAngle, -tiltMaxAngle]), springConfig)
  const rotateY = useSpring(useTransform(mouseX, [-0.5, 0.5], [-tiltMaxAngle, tiltMaxAngle]), springConfig)
  
  // Glare position
  const glareX = useSpring(useTransform(mouseX, [-0.5, 0.5], [0, 100]), springConfig)
  const glareY = useSpring(useTransform(mouseY, [-0.5, 0.5], [0, 100]), springConfig)
  
  // Pre-compute glare position transforms (moved from JSX to comply with Rules of Hooks)
  const glareLeft = useTransform(glareX, (v) => `${v}%`)
  const glareTop = useTransform(glareY, (v) => `${v}%`)
  
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (disabled) return
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    
    const x = (e.clientX - rect.left) / rect.width - 0.5
    const y = (e.clientY - rect.top) / rect.height - 0.5
    
    mouseX.set(x)
    mouseY.set(y)
  }, [disabled, mouseX, mouseY])
  
  const handleMouseEnter = useCallback(() => {
    if (!disabled) setIsHovered(true)
  }, [disabled])
  
  const handleMouseLeave = useCallback(() => {
    setIsHovered(false)
    mouseX.set(0)
    mouseY.set(0)
  }, [mouseX, mouseY])

  if (disabled) {
    return (
      <div className={cn('relative', containerClassName)}>
        <div className={className}>{children}</div>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={cn('relative', containerClassName)}
      style={{ perspective: `${perspective}px` }}
      onMouseMove={handleMouseMove}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <motion.div
        className={cn(
          'relative rounded-2xl transition-shadow duration-300',
          'bg-white/80 dark:bg-gray-900/80',
          'backdrop-blur-xl',
          'border border-white/20 dark:border-gray-700/50',
          isHovered && 'shadow-2xl shadow-black/10 dark:shadow-black/30',
          className
        )}
        style={{
          rotateX,
          rotateY,
          transformStyle: 'preserve-3d',
        }}
        animate={{
          scale: isHovered ? scale : 1,
        }}
        transition={{
          scale: { type: 'spring', stiffness: 400, damping: 25 },
        }}
      >
        {/* Glare overlay */}
        {glareEnabled && (
          <motion.div
            className="pointer-events-none absolute inset-0 rounded-2xl overflow-hidden"
            style={{
              opacity: isHovered ? 1 : 0,
              transition: `opacity ${transitionSpeed}ms ease`,
            }}
          >
            <motion.div
              className="absolute w-[200%] h-[200%] -translate-x-1/2 -translate-y-1/2"
              style={{
                left: glareLeft,
                top: glareTop,
                background: 'radial-gradient(circle at center, rgba(255,255,255,0.25) 0%, transparent 50%)',
              }}
            />
          </motion.div>
        )}
        
        {/* Content with slight depth offset */}
        <div 
          className="relative"
          style={{ 
            transform: 'translateZ(20px)',
            transformStyle: 'preserve-3d',
          }}
        >
          {children}
        </div>
        
        {/* Subtle border glow on hover */}
        <motion.div
          className="pointer-events-none absolute inset-0 rounded-2xl"
          style={{
            opacity: isHovered ? 1 : 0,
            transition: `opacity ${transitionSpeed}ms ease`,
            boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.1), 0 0 30px rgba(59,130,246,0.1)',
          }}
        />
      </motion.div>
    </div>
  )
}

/**
 * Card3DContent - Inner content wrapper with additional depth
 */
export function Card3DContent({
  children,
  className,
  depth = 30,
}: {
  children: React.ReactNode
  className?: string
  depth?: number
}) {
  return (
    <div
      className={className}
      style={{
        transform: `translateZ(${depth}px)`,
        transformStyle: 'preserve-3d',
      }}
    >
      {children}
    </div>
  )
}

/**
 * FloatingElement - Element that floats above the card surface
 */
export function FloatingElement({
  children,
  className,
  depth = 40,
  floatIntensity = 5,
}: {
  children: React.ReactNode
  className?: string
  depth?: number
  floatIntensity?: number
}) {
  return (
    <motion.div
      className={className}
      style={{
        transform: `translateZ(${depth}px)`,
        transformStyle: 'preserve-3d',
      }}
      animate={{
        y: [0, -floatIntensity, 0],
      }}
      transition={{
        duration: 3,
        repeat: Infinity,
        ease: 'easeInOut',
      }}
    >
      {children}
    </motion.div>
  )
}

/**
 * GlassCard - Simplified glassmorphism card without 3D effects
 */
export function GlassCard({
  children,
  className,
  hoverable = true,
  glowColor = 'sky',
}: {
  children: React.ReactNode
  className?: string
  hoverable?: boolean
  glowColor?: 'sky' | 'emerald' | 'violet' | 'amber'
}) {
  const glowColors = {
    sky: 'group-hover:shadow-sky-500/20',
    emerald: 'group-hover:shadow-emerald-500/20',
    violet: 'group-hover:shadow-violet-500/20',
    amber: 'group-hover:shadow-amber-500/20',
  }

  return (
    <div className={cn('group', hoverable && 'cursor-pointer')}>
      <motion.div
        className={cn(
          'relative rounded-2xl overflow-hidden',
          'bg-white/80 dark:bg-gray-900/80',
          'backdrop-blur-xl',
          'border border-white/20 dark:border-gray-700/50',
          'shadow-lg shadow-black/5',
          hoverable && [
            'transition-all duration-300',
            'group-hover:shadow-2xl',
            glowColors[glowColor],
          ],
          className
        )}
        whileHover={hoverable ? { y: -4, scale: 1.01 } : undefined}
        transition={{ type: 'spring', stiffness: 400, damping: 25 }}
      >
        {/* Gradient border effect */}
        <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-white/10 to-transparent pointer-events-none" />
        
        {/* Content */}
        <div className="relative">{children}</div>
      </motion.div>
    </div>
  )
}

/**
 * ShimmerCard - Card with animated shimmer effect
 */
export function ShimmerCard({
  children,
  className,
  shimmerColor = 'rgba(255,255,255,0.1)',
}: {
  children: React.ReactNode
  className?: string
  shimmerColor?: string
}) {
  return (
    <div
      className={cn(
        'relative rounded-2xl overflow-hidden',
        'bg-white dark:bg-gray-900',
        'border border-gray-200 dark:border-gray-700',
        className
      )}
    >
      {/* Shimmer effect */}
      <motion.div
        className="absolute inset-0 -translate-x-full"
        style={{
          background: `linear-gradient(90deg, transparent, ${shimmerColor}, transparent)`,
        }}
        animate={{
          translateX: ['100%', '-100%'],
        }}
        transition={{
          duration: 2,
          repeat: Infinity,
          repeatDelay: 3,
          ease: 'linear',
        }}
      />
      
      {/* Content */}
      <div className="relative">{children}</div>
    </div>
  )
}

export default Card3D
