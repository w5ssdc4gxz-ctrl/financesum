"use client"

import { useRef, useState, useEffect, ReactNode } from "react"
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion"
import { cn } from "@/lib/utils"

interface FloatProps {
  children: ReactNode
  className?: string
  rotationRange?: number | [number, number, number]
  perspective?: number
  scale?: number
  speed?: number
  amplitude?: [number, number, number]
  timeOffset?: number
  autoFloat?: boolean
}

export default function Float({
  children,
  className,
  rotationRange = 15,
  perspective = 1000,
  scale = 1.05,
  speed = 0.5,
  amplitude = [10, 30, 30],
  timeOffset = 0,
  autoFloat = true,
}: FloatProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [isHovered, setIsHovered] = useState(false)

  // Mouse-based motion values
  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)

  // Automatic float motion values
  const floatX = useMotionValue(0)
  const floatY = useMotionValue(0)
  const floatRotateX = useMotionValue(0)
  const floatRotateY = useMotionValue(0)
  const floatRotateZ = useMotionValue(0)

  const springConfig = { damping: 20, stiffness: 300 }
  const xSpring = useSpring(mouseX, springConfig)
  const ySpring = useSpring(mouseY, springConfig)

  // Normalize rotationRange to array format
  const rotRange = Array.isArray(rotationRange)
    ? rotationRange
    : [rotationRange, rotationRange, rotationRange * 0.5]

  const mouseRotateX = useTransform(ySpring, [-0.5, 0.5], [rotRange[0], -rotRange[0]])
  const mouseRotateY = useTransform(xSpring, [-0.5, 0.5], [-rotRange[1], rotRange[1]])

  // Automatic floating animation using sine waves
  useEffect(() => {
    if (!autoFloat) return

    let animationId: number
    const startTime = performance.now()

    const animate = () => {
      const elapsed = (performance.now() - startTime) / 1000 + timeOffset
      const t = elapsed * speed

      // Use sine waves for smooth movement on X, Y, Z axes
      floatX.set(Math.sin(t * 1.1) * amplitude[0])
      floatY.set(Math.sin(t * 0.9) * amplitude[1])

      // Rotation using different frequency sine waves
      floatRotateX.set(Math.sin(t * 0.8) * rotRange[0])
      floatRotateY.set(Math.sin(t * 1.2) * rotRange[1])
      floatRotateZ.set(Math.sin(t * 0.7) * rotRange[2])

      animationId = requestAnimationFrame(animate)
    }

    animate()

    return () => {
      cancelAnimationFrame(animationId)
    }
  }, [autoFloat, speed, amplitude, rotRange, timeOffset, floatX, floatY, floatRotateX, floatRotateY, floatRotateZ])

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return

    const rect = ref.current.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2

    const x = (e.clientX - centerX) / rect.width
    const y = (e.clientY - centerY) / rect.height

    mouseX.set(x)
    mouseY.set(y)
  }

  const handleMouseLeave = () => {
    setIsHovered(false)
    mouseX.set(0)
    mouseY.set(0)
  }

  const handleMouseEnter = () => {
    setIsHovered(true)
  }

  return (
    <motion.div
      ref={ref}
      className={cn("relative", className)}
      onMouseMove={handleMouseMove}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      style={{
        perspective,
        transformStyle: "preserve-3d",
      }}
    >
      <motion.div
        style={{
          x: floatX,
          y: floatY,
          rotateX: isHovered ? mouseRotateX : floatRotateX,
          rotateY: isHovered ? mouseRotateY : floatRotateY,
          rotateZ: floatRotateZ,
          transformStyle: "preserve-3d",
        }}
        animate={{
          scale: isHovered ? scale : 1,
        }}
        transition={{
          scale: { duration: 0.2 },
        }}
        className="w-full h-full"
      >
        {children}
      </motion.div>
    </motion.div>
  )
}
