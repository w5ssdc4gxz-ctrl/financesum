'use client'

import { memo, useState } from 'react'
import { ComposableMap, Geographies, Geography, Marker } from 'react-simple-maps'
import { motion } from 'framer-motion'
import { Tooltip } from 'react-tooltip'
import { CompanyLogo } from '@/components/CompanyLogo'
import 'react-tooltip/dist/react-tooltip.css'

const geoUrl = 'https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json'

export interface MapDataPoint {
  name: string
  coordinates: [number, number]
  value: number
  tickers?: string[]
  country?: string
}

interface InteractiveWorldMapProps {
  data: MapDataPoint[]
  height?: number
  showTooltip?: boolean
}

const InteractiveWorldMap = memo(function InteractiveWorldMap({
  data,
  height = 400,
  showTooltip = true
}: InteractiveWorldMapProps) {
  const [hoveredMarker, setHoveredMarker] = useState<string | null>(null)
  
  const hoveredData = hoveredMarker 
    ? data[parseInt(hoveredMarker.split('-')[1])] 
    : null

  // Calculate marker sizes based on value
  const maxValue = Math.max(...data.map(d => d.value), 1)
  const getMarkerSize = (value: number) => {
    const minSize = 4
    const maxSize = 16
    return minSize + ((value / maxValue) * (maxSize - minSize))
  }

  if (!data || data.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
        className="flex items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-900/50 dark:text-gray-400"
        style={{ height }}
      >
        <div className="text-center">
          <p className="font-medium">No geographic data yet</p>
          <p className="mt-1 text-xs text-gray-400">Analyze companies to see global coverage</p>
        </div>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5 }}
      className="relative w-full overflow-hidden rounded-lg bg-gradient-to-br from-blue-50 via-white to-indigo-50 dark:from-gray-900 dark:via-gray-950 dark:to-blue-950/30"
      style={{ height }}
    >
      <ComposableMap
        projection="geoMercator"
        projectionConfig={{
          scale: 147,
          center: [0, 20]
        }}
        style={{ width: '100%', height: '100%' }}
      >
        <Geographies geography={geoUrl}>
          {({ geographies }: any) =>
            geographies.map((geo: any) => (
              <Geography
                key={geo.rsmKey}
                geography={geo}
                fill="#e5e7eb"
                stroke="#f9fafb"
                strokeWidth={0.5}
                className="transition-all duration-200 hover:fill-gray-300 dark:fill-gray-800 dark:stroke-gray-900 dark:hover:fill-gray-700"
                style={{
                  default: { outline: 'none' },
                  hover: { outline: 'none' },
                  pressed: { outline: 'none' }
                }}
              />
            ))
          }
        </Geographies>

        {data.map((point, idx) => {
            const markerId = `marker-${idx}`
            const markerSize = getMarkerSize(point.value)

            return (
              <Marker
                key={markerId}
                coordinates={point.coordinates}
                onMouseEnter={() => setHoveredMarker(markerId)}
                onMouseLeave={() => setHoveredMarker(null)}
              >
                <motion.g
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{
                    duration: 0.4,
                    delay: idx * 0.05,
                    ease: [0.4, 0, 0.2, 1]
                  }}
                >
                  {/* Pulse animation ring */}
                  <motion.circle
                    r={markerSize + 4}
                    fill="#3b82f6"
                    fillOpacity={0.1}
                    animate={hoveredMarker === markerId ? {
                      scale: [1, 1.3, 1],
                      opacity: [0.3, 0.1, 0.3]
                    } : {}}
                    transition={{
                      duration: 1.5,
                      repeat: Infinity,
                      ease: "easeInOut"
                    }}
                  />

                  {/* Main marker */}
                  <circle
                    r={markerSize}
                    fill="#3b82f6"
                    stroke="white"
                    strokeWidth={2}
                    className="cursor-pointer transition-all duration-200"
                    style={{
                      filter: hoveredMarker === markerId
                        ? 'drop-shadow(0 4px 12px rgba(59, 130, 246, 0.5))'
                        : 'drop-shadow(0 2px 6px rgba(0, 0, 0, 0.1))',
                      transform: hoveredMarker === markerId ? 'scale(1.2)' : 'scale(1)'
                    }}
                    data-tooltip-id="map-tooltip"
                  />

                  {/* Inner glow */}
                  <circle
                    r={markerSize * 0.5}
                    fill="white"
                    fillOpacity={0.6}
                    className="pointer-events-none"
                  />
                </motion.g>
              </Marker>
            )
          })}
      </ComposableMap>

      {showTooltip && (
        <Tooltip
          id="map-tooltip"
          style={{ opacity: 1 }}
          render={({ content }) => {
            if (!hoveredData) return null
            return (
              <div className="flex flex-col gap-2 p-1">
                <div className="font-bold text-sm">{hoveredData.name}</div>
                <div className="text-xs mb-1">{hoveredData.value} {hoveredData.value === 1 ? 'analysis' : 'analyses'}</div>
                {hoveredData.tickers && hoveredData.tickers.length > 0 && (
                  <motion.div 
                    className="flex flex-wrap gap-1 max-w-[200px]"
                    initial="hidden"
                    animate="visible"
                    variants={{
                      hidden: { opacity: 0 },
                      visible: {
                        opacity: 1,
                        transition: { staggerChildren: 0.05 }
                      }
                    }}
                  >
                    {hoveredData.tickers.slice(0, 12).map((ticker) => (
                      <motion.div 
                        key={ticker} 
                        className="h-6 w-6 rounded-full bg-white p-0.5 shadow-sm overflow-hidden"
                        variants={{
                          hidden: { opacity: 0, scale: 0.5 },
                          visible: { opacity: 1, scale: 1 }
                        }}
                      >
                        <CompanyLogo ticker={ticker} className="h-full w-full" />
                      </motion.div>
                    ))}
                    {hoveredData.tickers.length > 12 && (
                      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-100 text-[9px] font-bold text-gray-500">
                        +{hoveredData.tickers.length - 12}
                      </div>
                    )}
                  </motion.div>
                )}
              </div>
            )
          }}
        />
      )}

      {/* Legend */}
      <div className="absolute bottom-4 left-4 rounded-lg border border-gray-200 bg-white/90 p-3 shadow-lg backdrop-blur-sm dark:border-gray-800 dark:bg-gray-950/90">
        <p className="mb-2 text-xs font-semibold text-gray-700 dark:text-gray-300">Analysis Coverage</p>
        <div className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400">
          <div className="flex items-center gap-1">
            <div className="h-2 w-2 rounded-full bg-blue-600" />
            <span>1-5</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="h-3 w-3 rounded-full bg-blue-600" />
            <span>6-10</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="h-4 w-4 rounded-full bg-blue-600" />
            <span>11+</span>
          </div>
        </div>
      </div>

      {/* Stats overlay */}
      <div className="absolute right-4 top-4 rounded-lg border border-gray-200 bg-white/90 px-4 py-2 shadow-lg backdrop-blur-sm dark:border-gray-800 dark:bg-gray-950/90">
        <p className="text-2xl font-bold text-gray-900 dark:text-gray-50">{data.length}</p>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          {data.length === 1 ? 'Location' : 'Locations'}
        </p>
      </div>
    </motion.div>
  )
})

export default InteractiveWorldMap
