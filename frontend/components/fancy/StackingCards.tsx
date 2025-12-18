"use client"

import { ReactNode, useRef, createContext, useContext } from "react"
import { motion, useScroll, useTransform, MotionValue, UseScrollOptions } from "framer-motion"
import { cn } from "@/lib/utils"

interface StackingCardsContextType {
  totalCards: number
  scrollYProgress: MotionValue<number>
  scaleMultiplier: number
}

const StackingCardsContext = createContext<StackingCardsContextType | null>(null)

interface StackingCardsProps {
  children: ReactNode
  totalCards: number
  className?: string
  scaleMultiplier?: number
  scrollOptions?: UseScrollOptions
}

export default function StackingCards({
  children,
  totalCards,
  className,
  scaleMultiplier = 0.03,
  scrollOptions,
}: StackingCardsProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start start", "end end"],
    ...scrollOptions,
  })

  return (
    <StackingCardsContext.Provider value={{ totalCards, scrollYProgress, scaleMultiplier }}>
      <div ref={containerRef} className={cn("relative", className)}>
        {children}
      </div>
    </StackingCardsContext.Provider>
  )
}

interface StackingCardItemProps {
  children: ReactNode
  index: number
  className?: string
  topPosition?: number | string
}

export function StackingCardItem({ 
  children, 
  index, 
  className,
  topPosition,
}: StackingCardItemProps) {
  const context = useContext(StackingCardsContext)
  
  if (!context) {
    throw new Error("StackingCardItem must be used within StackingCards")
  }

  const { totalCards, scrollYProgress, scaleMultiplier } = context

  // All cards stick at nearly the same top position.
  // Default matches the reference demo behavior: 5vh + index * 3vh.
  const topOffset = topPosition ?? `${5 + index * 3}vh`

  // Calculate the target scale for this card
  // Card 0 scales down the most, last card stays at scale 1
  // This makes earlier cards appear smaller/behind later cards
  const targetScale = 1 - (scaleMultiplier * (totalCards - 1 - index))
  
  // Demo-style behavior: all cards scale based on the overall scroll progress.
  // Earlier cards end up smaller, creating the "stored behind" stack.
  const scale = useTransform(scrollYProgress, [0, 1], [1, targetScale])

  return (
    <motion.div
      className={cn("sticky will-change-transform", className)}
      style={{
        top: topOffset,
        scale,
        // Later cards (higher index) have higher z-index - they stack ON TOP
        zIndex: index + 1,
        transformOrigin: "center top",
      }}
    >
      {children}
    </motion.div>
  )
}
