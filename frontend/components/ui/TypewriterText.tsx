'use client'

import { useEffect, useState, useRef } from 'react'

interface TypewriterTextProps {
  text: string
  speed?: number
  onComplete?: () => void
  className?: string
  children?: (displayText: string) => React.ReactNode
}

export function TypewriterText({ 
  text, 
  speed = 80, 
  onComplete, 
  className,
  children 
}: TypewriterTextProps) {
  const [displayText, setDisplayText] = useState('')
  const [isComplete, setIsComplete] = useState(false)
  const indexRef = useRef(0)
  const animationRef = useRef<number>()
  const onCompleteRef = useRef(onComplete)
  const textRef = useRef(text)
  
  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    textRef.current = text
    indexRef.current = 0
    setDisplayText('')
    setIsComplete(false)
    
    const charsPerFrame = Math.max(1, Math.floor(speed / 60))
    const interval = 1000 / 60

    let lastTime = 0

    const animate = (currentTime: number) => {
      if (currentTime - lastTime >= interval) {
        if (indexRef.current < textRef.current.length) {
          indexRef.current = Math.min(indexRef.current + charsPerFrame, textRef.current.length)
          setDisplayText(textRef.current.slice(0, indexRef.current))
          lastTime = currentTime
        } else {
          setIsComplete(true)
          onCompleteRef.current?.()
          return
        }
      }
      animationRef.current = requestAnimationFrame(animate)
    }

    animationRef.current = requestAnimationFrame(animate)

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [text, speed])

  if (children) {
    return <>{children(displayText)}</>
  }

  return (
    <span className={className}>
      {displayText}
      {!isComplete && (
        <span className="inline-block w-[2px] h-[1em] bg-current ml-0.5 align-middle animate-blink" />
      )}
    </span>
  )
}