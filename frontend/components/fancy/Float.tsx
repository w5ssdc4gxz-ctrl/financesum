"use client"

import { useRef, useState, ReactNode } from "react"
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion"
import { cn } from "@/lib/utils"

interface FloatProps {
  children: ReactNode
  className?: string
  rotationRange?: number
  perspective?: number
  scale?: number
}

export default function Float({
  children,
  className,
  rotationRange = 15,
  perspective = 1000,
  scale = 1.05,
}: FloatProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [isHovered, setIsHovered] = useState(false)

  const x = useMotionValue(0)
  const y = useMotionValue(0)

  const springConfig = { damping: 20, stiffness: 300 }
  const xSpring = useSpring(x, springConfig)
  const ySpring = useSpring(y, springConfig)

  const rotateX = useTransform(ySpring, [-0.5, 0.5], [rotationRange, -rotationRange])
  const rotateY = useTransform(xSpring, [-0.5, 0.5], [-rotationRange, rotationRange])

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return

    const rect = ref.current.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2

    const mouseX = (e.clientX - centerX) / rect.width
    const mouseY = (e.clientY - centerY) / rect.height

    x.set(mouseX)
    y.set(mouseY)
  }

  const handleMouseLeave = () => {
    setIsHovered(false)
    x.set(0)
    y.set(0)
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
          rotateX,
          rotateY,
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
