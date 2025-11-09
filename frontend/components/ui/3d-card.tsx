'use client'

import React, { useRef } from 'react'
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'

type CardContainerProps = {
  children: React.ReactNode
  className?: string
}

type CardBodyProps = React.HTMLAttributes<HTMLDivElement> & {
  as?: React.ElementType
}

type CardItemProps = React.HTMLAttributes<HTMLElement> & {
  translateZ?: number | string
  as?: React.ElementType
}

const cn = (...classes: Array<string | undefined | false>) => classes.filter(Boolean).join(' ')

export const CardContainer: React.FC<CardContainerProps> = ({ children, className }) => {
  const ref = useRef<HTMLDivElement | null>(null)
  const motionX = useMotionValue(0)
  const motionY = useMotionValue(0)

  const rotateX = useSpring(useTransform(motionY, [-0.5, 0.5], [12, -12]), {
    stiffness: 200,
    damping: 20,
  })
  const rotateY = useSpring(useTransform(motionX, [-0.5, 0.5], [-12, 12]), {
    stiffness: 200,
    damping: 20,
  })

  const handleMouseMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const element = ref.current
    if (!element) return
    const rect = element.getBoundingClientRect()
    const x = (event.clientX - rect.left) / rect.width - 0.5
    const y = (event.clientY - rect.top) / rect.height - 0.5
    motionX.set(x)
    motionY.set(y)
  }

  const resetMotion = () => {
    motionX.set(0)
    motionY.set(0)
  }

  return (
    <div
      ref={ref}
      onMouseMove={handleMouseMove}
      onMouseLeave={resetMotion}
      className={cn('relative flex w-full justify-center', className)}
      style={{ perspective: '1600px' }}
    >
      <motion.div style={{ rotateX, rotateY, transformStyle: 'preserve-3d', width: '100%' }}>
        {children}
      </motion.div>
    </div>
  )
}

export const CardBody: React.FC<CardBodyProps> = ({ children, className, as: Component = 'div', style, ...props }) => {
  return (
    <Component
      className={cn(
        'relative w-full rounded-3xl border border-white/10 bg-gradient-to-br from-slate-900/80 to-slate-950/90 p-8 shadow-2xl transition-[box-shadow] duration-300 group/card',
        className,
      )}
      style={{ transformStyle: 'preserve-3d', ...style }}
      {...props}
    >
      {children}
    </Component>
  )
}

export const CardItem: React.FC<CardItemProps> = ({
  children,
  className,
  translateZ = 0,
  as: Component = 'div',
  style,
  ...props
}) => {
  const depthValue =
    typeof translateZ === 'number'
      ? `${translateZ}px`
      : translateZ || '0px'

  return (
    <Component
      className={cn('transform-gpu transition-transform duration-300 ease-out', className)}
      style={{ transform: `translateZ(${depthValue})`, ...style }}
      {...props}
    >
      {children}
    </Component>
  )
}
