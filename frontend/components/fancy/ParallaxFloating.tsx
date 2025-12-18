"use client"

import { createContext, useContext, useRef, useEffect, useState, ReactNode } from "react"
import { motion, useSpring, useTransform } from "framer-motion"
import { cn } from "@/lib/utils"

interface MousePosition {
  x: number
  y: number
}

const FloatingContext = createContext<{
  mouseX: ReturnType<typeof useSpring>
  mouseY: ReturnType<typeof useSpring>
  sensitivity: number
} | null>(null)

interface FloatingProps {
  children: ReactNode
  className?: string
  sensitivity?: number
}

export default function Floating({ 
  children, 
  className,
  sensitivity = 1 
}: FloatingProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [mousePosition, setMousePosition] = useState<MousePosition>({ x: 0.5, y: 0.5 })

  const springConfig = { damping: 30, stiffness: 150 }
  const mouseX = useSpring(0.5, springConfig)
  const mouseY = useSpring(0.5, springConfig)

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!containerRef.current) return
      
      const rect = containerRef.current.getBoundingClientRect()
      const x = (e.clientX - rect.left) / rect.width
      const y = (e.clientY - rect.top) / rect.height
      
      mouseX.set(x)
      mouseY.set(y)
    }

    const handleTouchMove = (e: TouchEvent) => {
      if (!containerRef.current || !e.touches[0]) return
      
      const rect = containerRef.current.getBoundingClientRect()
      const x = (e.touches[0].clientX - rect.left) / rect.width
      const y = (e.touches[0].clientY - rect.top) / rect.height
      
      mouseX.set(x)
      mouseY.set(y)
    }

    const container = containerRef.current
    if (container) {
      container.addEventListener("mousemove", handleMouseMove)
      container.addEventListener("touchmove", handleTouchMove)
    }

    return () => {
      if (container) {
        container.removeEventListener("mousemove", handleMouseMove)
        container.removeEventListener("touchmove", handleTouchMove)
      }
    }
  }, [mouseX, mouseY])

  return (
    <FloatingContext.Provider value={{ mouseX, mouseY, sensitivity }}>
      <div 
        ref={containerRef} 
        className={cn("relative w-full h-full", className)}
      >
        {children}
      </div>
    </FloatingContext.Provider>
  )
}

interface FloatingElementProps {
  children: ReactNode
  className?: string
  depth?: number
}

export function FloatingElement({ 
  children, 
  className,
  depth = 1 
}: FloatingElementProps) {
  const context = useContext(FloatingContext)
  
  if (!context) {
    throw new Error("FloatingElement must be used within a Floating component")
  }

  const { mouseX, mouseY, sensitivity } = context
  
  const moveRange = 40 * depth * sensitivity
  
  const x = useTransform(mouseX, [0, 1], [-moveRange, moveRange])
  const y = useTransform(mouseY, [0, 1], [-moveRange, moveRange])

  return (
    <motion.div
      className={cn("absolute", className)}
      style={{ x, y }}
    >
      {children}
    </motion.div>
  )
}
