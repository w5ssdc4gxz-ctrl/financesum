"use client"

import { useState, useEffect } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { cn } from "@/lib/utils"

interface TextRotatorProps {
  words: string[]
  className?: string
  interval?: number
  animationType?: "fade" | "slide" | "blur"
}

export default function TextRotator({
  words,
  className,
  interval = 3000,
  animationType = "slide",
}: TextRotatorProps) {
  const [currentIndex, setCurrentIndex] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % words.length)
    }, interval)

    return () => clearInterval(timer)
  }, [words.length, interval])

  const variants = {
    fade: {
      initial: { opacity: 0 },
      animate: { opacity: 1 },
      exit: { opacity: 0 },
    },
    slide: {
      initial: { opacity: 0, y: 20 },
      animate: { opacity: 1, y: 0 },
      exit: { opacity: 0, y: -20 },
    },
    blur: {
      initial: { opacity: 0, filter: "blur(10px)", y: 10 },
      animate: { opacity: 1, filter: "blur(0px)", y: 0 },
      exit: { opacity: 0, filter: "blur(10px)", y: -10 },
    },
  }

  const currentVariant = variants[animationType]

  return (
    <span className={cn("relative inline-block", className)}>
      <AnimatePresence mode="wait">
        <motion.span
          key={currentIndex}
          initial={currentVariant.initial}
          animate={currentVariant.animate}
          exit={currentVariant.exit}
          transition={{
            duration: 0.5,
            ease: [0.4, 0, 0.2, 1],
          }}
          className="inline-block"
        >
          {words[currentIndex]}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}
