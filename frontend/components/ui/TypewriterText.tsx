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
  const animationRef = useRef<number>()
  const onCompleteRef = useRef(onComplete)
  const textRef = useRef(text)
  const renderedIndexRef = useRef(0)
  
  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    textRef.current = text
    renderedIndexRef.current = 0
    setDisplayText('')
    setIsComplete(false)

    if (!text) {
      setIsComplete(true)
      onCompleteRef.current?.()
      return
    }

    let startTime: number | null = null

    const animate = (currentTime: number) => {
      if (startTime == null) {
        startTime = currentTime
      }

      const nextIndex = Math.min(
        textRef.current.length,
        Math.max(1, Math.floor(((currentTime - startTime) * Math.max(speed, 1)) / 1000)),
      )

      if (nextIndex !== renderedIndexRef.current) {
        renderedIndexRef.current = nextIndex
        setDisplayText(textRef.current.slice(0, nextIndex))
      }

      if (nextIndex >= textRef.current.length) {
        setIsComplete(true)
        onCompleteRef.current?.()
        return
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
