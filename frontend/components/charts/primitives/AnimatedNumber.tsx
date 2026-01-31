'use client'

import React, { useRef, useEffect, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { cn } from '@/lib/utils'

interface AnimatedNumberProps {
  value: number
  format?: (value: number) => string
  duration?: number
  delay?: number
  className?: string
  prefix?: string
  suffix?: string
  decimals?: number
  morphDigits?: boolean
}

/**
 * AnimatedNumber - GSAP-powered number counter with optional digit morphing
 * 
 * Features:
 * - Smooth count-up animation
 * - Custom formatting
 * - Optional digit morphing effect
 * - Spring-like easing
 */
export function AnimatedNumber({
  value,
  format,
  duration = 1.2,
  delay = 0,
  className,
  prefix = '',
  suffix = '',
  decimals = 0,
  morphDigits = false,
}: AnimatedNumberProps) {
  const containerRef = useRef<HTMLSpanElement>(null)
  const [displayValue, setDisplayValue] = useState(0)
  const [hasAnimated, setHasAnimated] = useState(false)
  
  // Default formatter
  const defaultFormat = (v: number) => {
    if (decimals > 0) {
      return v.toFixed(decimals)
    }
    return Math.round(v).toLocaleString()
  }
  
  const formatter = format || defaultFormat

  useGSAP(() => {
    if (hasAnimated) return
    
    const obj = { val: 0 }
    
    gsap.to(obj, {
      val: value,
      duration,
      delay,
      ease: 'power3.out',
      onUpdate: () => {
        setDisplayValue(obj.val)
      },
      onComplete: () => {
        setDisplayValue(value)
        setHasAnimated(true)
      },
    })
  }, [value, duration, delay, hasAnimated])

  // Re-animate when value changes significantly
  useEffect(() => {
    if (hasAnimated && Math.abs(displayValue - value) > 0.01) {
      setHasAnimated(false)
    }
  }, [value, displayValue, hasAnimated])

  if (morphDigits) {
    return (
      <MorphingNumber
        value={value}
        format={format}
        duration={duration}
        delay={delay}
        className={className}
        prefix={prefix}
        suffix={suffix}
      />
    )
  }

  return (
    <span 
      ref={containerRef}
      className={cn(
        'tabular-nums font-bold',
        className
      )}
    >
      {prefix}{formatter(displayValue)}{suffix}
    </span>
  )
}

/**
 * MorphingNumber - Digit-by-digit morphing animation
 */
function MorphingNumber({
  value,
  format,
  duration = 1.2,
  delay = 0,
  className,
  prefix = '',
  suffix = '',
}: Omit<AnimatedNumberProps, 'decimals' | 'morphDigits'>) {
  const containerRef = useRef<HTMLSpanElement>(null)
  const [animatedValue, setAnimatedValue] = useState(0)
  
  const formatter = format || ((v: number) => Math.round(v).toLocaleString())
  const displayString = formatter(animatedValue)
  
  useGSAP(() => {
    const obj = { val: 0 }
    
    gsap.to(obj, {
      val: value,
      duration,
      delay,
      ease: 'power3.out',
      onUpdate: () => {
        setAnimatedValue(obj.val)
      },
    })
  }, [value, duration, delay])

  return (
    <span 
      ref={containerRef}
      className={cn(
        'inline-flex items-baseline tabular-nums font-bold overflow-hidden',
        className
      )}
    >
      {prefix && <span>{prefix}</span>}
      {displayString.split('').map((char, index) => (
        <MorphingDigit key={`${index}-${char}`} char={char} delay={delay + index * 0.03} />
      ))}
      {suffix && <span>{suffix}</span>}
    </span>
  )
}

/**
 * Individual morphing digit with slide animation
 */
function MorphingDigit({ char, delay }: { char: string; delay: number }) {
  const digitRef = useRef<HTMLSpanElement>(null)
  
  useGSAP(() => {
    if (!digitRef.current) return
    
    gsap.fromTo(
      digitRef.current,
      { 
        y: 20, 
        opacity: 0,
        rotateX: -90,
      },
      { 
        y: 0, 
        opacity: 1,
        rotateX: 0,
        duration: 0.4,
        delay,
        ease: 'back.out(1.7)',
      }
    )
  }, [char, delay])

  return (
    <span
      ref={digitRef}
      className="inline-block"
      style={{ 
        transformStyle: 'preserve-3d',
        perspective: '100px',
      }}
    >
      {char}
    </span>
  )
}

/**
 * AnimatedPercentage - Specialized for percentage values
 */
export function AnimatedPercentage({
  value,
  duration = 1.2,
  delay = 0,
  className,
  showSign = false,
}: {
  value: number
  duration?: number
  delay?: number
  className?: string
  showSign?: boolean
}) {
  const sign = showSign && value > 0 ? '+' : ''
  
  return (
    <AnimatedNumber
      value={value}
      format={(v) => `${sign}${v.toFixed(1)}%`}
      duration={duration}
      delay={delay}
      className={className}
      decimals={1}
    />
  )
}

/**
 * AnimatedCurrency - Specialized for currency values
 */
export function AnimatedCurrency({
  value,
  symbol = '$',
  duration = 1.2,
  delay = 0,
  className,
  compact = true,
}: {
  value: number
  symbol?: string
  duration?: number
  delay?: number
  className?: string
  compact?: boolean
}) {
  const format = (v: number) => {
    const abs = Math.abs(v)
    const sign = v < 0 ? '-' : ''
    
    if (compact) {
      if (abs >= 1_000_000_000) return `${sign}${symbol}${(abs / 1_000_000_000).toFixed(2)}B`
      if (abs >= 1_000_000) return `${sign}${symbol}${(abs / 1_000_000).toFixed(1)}M`
      if (abs >= 1_000) return `${sign}${symbol}${(abs / 1_000).toFixed(1)}K`
    }
    return `${sign}${symbol}${abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
  }
  
  return (
    <AnimatedNumber
      value={value}
      format={format}
      duration={duration}
      delay={delay}
      className={className}
    />
  )
}

export default AnimatedNumber
