"use client"

import { motion } from "framer-motion"
import { useState } from "react"
import { cn } from "@/lib/utils"

interface LetterSwapForwardProps {
  label: string
  className?: string
  staggerFrom?: "first" | "center" | "last"
  staggerDelay?: number
  reverse?: boolean
}

export default function LetterSwapForward({
  label,
  className,
  staggerFrom = "first",
  staggerDelay = 0.03,
  reverse = false,
}: LetterSwapForwardProps) {
  const [isHovered, setIsHovered] = useState(false)
  const letters = label.split("")

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

  return (
    <motion.span
      className={cn("relative inline-flex overflow-hidden cursor-pointer", className)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {letters.map((letter, index) => (
        <span key={index} className="relative inline-block">
          {/* Original letter */}
          <motion.span
            className="inline-block"
            initial={{ y: 0 }}
            animate={{ y: isHovered ? (reverse ? "100%" : "-100%") : 0 }}
            transition={{
              duration: 0.3,
              delay: getStaggerDelay(index),
              ease: "easeInOut",
            }}
          >
            {letter === " " ? "\u00A0" : letter}
          </motion.span>
          
          {/* Duplicate letter (hidden initially, slides in on hover) */}
          <motion.span
            className="absolute left-0 inline-block"
            initial={{ y: reverse ? "-100%" : "100%" }}
            animate={{ y: isHovered ? 0 : (reverse ? "-100%" : "100%") }}
            transition={{
              duration: 0.3,
              delay: getStaggerDelay(index),
              ease: "easeInOut",
            }}
          >
            {letter === " " ? "\u00A0" : letter}
          </motion.span>
        </span>
      ))}
    </motion.span>
  )
}
