'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { motion, useInView } from 'framer-motion'
import './AnimatedList.css'

export interface AnimatedListItem {
  id: string
  title: string
  description?: string
  meta?: string
}

interface AnimatedListProps {
  items: AnimatedListItem[]
  selectedId?: string
  onItemSelect?: (item: AnimatedListItem, index: number) => void
  showGradients?: boolean
  enableArrowNavigation?: boolean
  displayScrollbar?: boolean
  className?: string
  itemClassName?: string
}

const AnimatedItem = ({
  children,
  delay = 0,
  index,
  onMouseEnter,
  onClick,
}: {
  children: React.ReactNode
  delay?: number
  index: number
  onMouseEnter?: (index: number) => void
  onClick?: () => void
}) => {
  const ref = useRef<HTMLDivElement | null>(null)
  const inView = useInView(ref, { amount: 0.2, once: true })

  return (
    <motion.div
      ref={ref}
      data-index={index}
      onMouseEnter={() => onMouseEnter?.(index)}
      onClick={onClick}
      initial={{ scale: 0.92, opacity: 0 }}
      animate={inView ? { scale: 1, opacity: 1 } : { scale: 0.92, opacity: 0 }}
      transition={{ duration: 0.2, delay }}
      className="animated-list__motion-wrapper"
    >
      {children}
    </motion.div>
  )
}

const AnimatedList = ({
  items,
  selectedId,
  onItemSelect,
  showGradients = true,
  enableArrowNavigation = true,
  displayScrollbar = true,
  className = '',
  itemClassName = '',
}: AnimatedListProps) => {
  const listRef = useRef<HTMLDivElement | null>(null)
  const [activeIndex, setActiveIndex] = useState(() => items.findIndex((item) => item.id === selectedId))
  const [topGradientOpacity, setTopGradientOpacity] = useState(0)
  const [bottomGradientOpacity, setBottomGradientOpacity] = useState(1)
  const scrollRafRef = useRef<number | null>(null)

  const selectedIndex = useMemo(() => items.findIndex((item) => item.id === selectedId), [items, selectedId])

  useEffect(() => {
    setActiveIndex(selectedIndex)
  }, [selectedIndex])

  const scrollIntoView = (index: number) => {
    if (!listRef.current || index < 0) return
    const container = listRef.current
    const selectedItem = container.querySelector<HTMLElement>(`[data-index="${index}"]`)
    if (selectedItem) {
      const extraMargin = 40
      const containerScrollTop = container.scrollTop
      const containerHeight = container.clientHeight
      const itemTop = selectedItem.offsetTop
      const itemBottom = itemTop + selectedItem.offsetHeight
      if (itemTop < containerScrollTop + extraMargin) {
        container.scrollTo({ top: itemTop - extraMargin, behavior: 'smooth' })
      } else if (itemBottom > containerScrollTop + containerHeight - extraMargin) {
        container.scrollTo({
          top: itemBottom - containerHeight + extraMargin,
          behavior: 'smooth',
        })
      }
    }
  }

  useEffect(() => {
    scrollIntoView(activeIndex)
  }, [activeIndex])

  useEffect(() => {
    if (!enableArrowNavigation) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (items.length === 0) return
      if (e.key === 'ArrowDown' || (e.key === 'Tab' && !e.shiftKey)) {
        e.preventDefault()
        setActiveIndex((prev) => Math.min((prev ?? -1) + 1, items.length - 1))
      } else if (e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey)) {
        e.preventDefault()
        setActiveIndex((prev) => Math.max((prev ?? items.length) - 1, 0))
      } else if (e.key === 'Enter') {
        if (activeIndex >= 0 && activeIndex < items.length) {
          e.preventDefault()
          onItemSelect?.(items[activeIndex], activeIndex)
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [activeIndex, enableArrowNavigation, items, onItemSelect])

  const updateGradients = (element: HTMLDivElement) => {
    const { scrollTop, scrollHeight, clientHeight } = element
    const atCapacity = scrollHeight <= clientHeight
    const newTop = atCapacity ? 0 : Math.min(scrollTop / 40, 1)
    const bottomDistance = scrollHeight - (scrollTop + clientHeight)
    const newBottom = atCapacity ? 0 : Math.min(bottomDistance / 40, 1)

    setTopGradientOpacity((prev) => (Math.abs(prev - newTop) > 0.02 ? newTop : prev))
    setBottomGradientOpacity((prev) => (Math.abs(prev - newBottom) > 0.02 ? newBottom : prev))
  }

  useEffect(() => {
    if (listRef.current) {
      updateGradients(listRef.current)
    }
  }, [items])

  useEffect(() => {
    return () => {
      if (scrollRafRef.current) {
        cancelAnimationFrame(scrollRafRef.current)
        scrollRafRef.current = null
      }
    }
  }, [])

  return (
    <div className={`animated-list-container ${className}`}>
      <div
        ref={listRef}
        className={`animated-list ${!displayScrollbar ? 'animated-list--no-scrollbar' : ''}`}
        onScroll={(event) => {
          const element = event.currentTarget
          if (scrollRafRef.current !== null) return
          scrollRafRef.current = requestAnimationFrame(() => {
            scrollRafRef.current = null
            updateGradients(element)
          })
        }}
      >
        {items.map((item, index) => {
          const isSelected = item.id === selectedId
          const isActive = index === activeIndex

          return (
            <AnimatedItem
              key={item.id}
              delay={0.05 * index}
              index={index}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => onItemSelect?.(item, index)}
            >
              <div
                className={`animated-list__item ${isSelected ? 'animated-list__item--selected' : ''} ${
                  isActive && !isSelected ? 'animated-list__item--active' : ''
                } ${itemClassName}`}
              >
                <div className="animated-list__item-header">
                  <span className="animated-list__item-title">{item.title}</span>
                  {item.meta && <span className="animated-list__item-meta">{item.meta}</span>}
                </div>
                {item.description && <p className="animated-list__item-description">{item.description}</p>}
              </div>
            </AnimatedItem>
          )
        })}
      </div>

      {showGradients && (
        <>
          <div className="animated-list__gradient animated-list__gradient--top" style={{ opacity: topGradientOpacity }} />
          <div className="animated-list__gradient animated-list__gradient--bottom" style={{ opacity: bottomGradientOpacity }} />
        </>
      )}
    </div>
  )
}

export default AnimatedList
