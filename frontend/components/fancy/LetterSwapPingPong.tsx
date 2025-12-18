"use client"

import { motion, useAnimation, Variants } from "framer-motion"
import { cn } from "@/lib/utils"

interface LetterSwapPingPongProps {
  label: string
  className?: string
  staggerFrom?: "first" | "center" | "last"
  staggerDelay?: number
  reverse?: boolean
}

export default function LetterSwapPingPong({
  label,
  className,
  staggerFrom = "first",
  staggerDelay = 0.03,
  reverse = false,
}: LetterSwapPingPongProps) {
  const letters = label.split("")
  const controls = useAnimation()

  const getStaggerDelay = (index: number) => {
    const total = letters.length
    switch (staggerFrom) {
      case "center":
        return Math.abs(index - Math.floor(total / 2)) * staggerDelay
      case "last":
        return (total - 1 - index) * staggerDelay
      case "first":
      default:
        return index * staggerDelay
    }
  }

  const handleMouseEnter = () => {
    controls.start("hover")
  }

  const handleMouseLeave = () => {
    controls.start("initial")
  }

  return (
    <motion.span
      className={cn("relative inline-flex overflow-hidden cursor-pointer", className)}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {letters.map((letter, index) => {
        const delay = getStaggerDelay(index)
        
        return (
          <span key={index} className="relative inline-block">
            {/* Original letter */}
            <motion.span
              className="inline-block"
              initial={{ y: 0 }}
              animate={controls}
              variants={{
                initial: { 
                  y: 0,
                  transition: { duration: 0.3, delay, ease: "easeInOut" }
                },
                hover: { 
                  y: reverse ? "100%" : "-100%",
                  transition: { duration: 0.3, delay, ease: "easeInOut" }
                }
              }}
            >
              {letter === " " ? "\u00A0" : letter}
            </motion.span>
            
            {/* Duplicate letter */}
            <motion.span
              className="absolute left-0 inline-block"
              initial={{ y: reverse ? "-100%" : "100%" }}
              animate={controls}
              variants={{
                initial: { 
                  y: reverse ? "-100%" : "100%",
                  transition: { duration: 0.3, delay, ease: "easeInOut" }
                },
                hover: { 
                  y: 0,
                  transition: { duration: 0.3, delay, ease: "easeInOut" }
                }
              }}
            >
              {letter === " " ? "\u00A0" : letter}
            </motion.span>
          </span>
        )
      })}
    </motion.span>
  )
}
