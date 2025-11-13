"use client"

import { useMemo, useState } from "react"
import { ComposableMap, Geographies, Geography, Marker } from "react-simple-maps"
import type { DashboardMapPoint } from "@/hooks/useDashboardData"
import { buildLogoUrl } from "@/lib/logo-utils"
import { scoreToRating } from "@/lib/analysis-insights"

const geoUrl = "/data/world-110m.json"

type GeoImpactMapProps = {
  points: DashboardMapPoint[]
}

export function GeoImpactMap({ points }: GeoImpactMapProps) {
  const [activeMarker, setActiveMarker] = useState<string | null>(null)

  const pointMap = useMemo(() => {
    return points.map((point) => ({
      ...point,
      logoUrl: buildLogoUrl(point.ticker, point.exchange, point.country),
      rating: scoreToRating(point.score),
    }))
  }, [points])

  return (
    <div className="relative rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-2xl dark:bg-white/5">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <p className="text-sm uppercase tracking-widest text-primary-200/80">Global Coverage</p>
          <h3 className="text-2xl font-semibold text-white">Where your summaries come from</h3>
        </div>
        <span className="rounded-full border border-white/10 bg-white/10 px-4 py-1 text-xs font-semibold uppercase tracking-wide text-white/80">
          {points.length} companies
        </span>
      </div>
      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-b from-indigo-950/40 to-transparent">
        <ComposableMap
          projectionConfig={{ scale: 140 }}
          width={780}
          height={380}
          className="w-full"
        >
          <defs>
            <radialGradient id="markerGradient" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#c084fc" stopOpacity={0.9} />
              <stop offset="100%" stopColor="#c084fc" stopOpacity={0.1} />
            </radialGradient>
          </defs>
          <Geographies geography={geoUrl}>
            {({ geographies }: { geographies: any[] }) =>
              geographies.map((geo) => (
                <Geography
                  key={geo.rsmKey}
                  geography={geo}
                  fill="#0f172a"
                  stroke="#1f2937"
                  strokeWidth={0.5}
                  style={{
                    default: { outline: "none" },
                    hover: { outline: "none" },
                    pressed: { outline: "none" },
                  }}
                />
              ))
            }
          </Geographies>
          {pointMap.map((point) => (
            <Marker
              key={point.id}
              coordinates={[point.lng, point.lat]}
              onMouseEnter={() => setActiveMarker(point.id)}
              onMouseLeave={() => setActiveMarker((current) => (current === point.id ? null : current))}
            >
              <circle
                r={activeMarker === point.id ? 3.8 : 2.6}
                fill="url(#markerGradient)"
                stroke="#c084fc"
                strokeWidth={0.6}
              />
              <circle r={9} fill="rgba(192,132,252,0.2)" />
              {activeMarker === point.id && (
                <foreignObject x={10} y={-35} width={180} height={90}>
                  <div className="rounded-2xl border border-white/10 bg-slate-900/80 px-4 py-3 text-xs text-white shadow-lg backdrop-blur">
                    <div className="flex items-center gap-3">
                      {point.logoUrl ? (
                        <img
                          src={point.logoUrl}
                          alt={`${point.name} logo`}
                          className="h-9 w-9 rounded-full border border-white/10 bg-white/80 object-contain p-1"
                        />
                      ) : (
                        <div className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/10 font-semibold text-white">
                          {point.ticker.slice(0, 2)}
                        </div>
                      )}
                      <div>
                        <p className="text-sm font-semibold">{point.name}</p>
                        <p className="text-[11px] uppercase tracking-wide text-white/60">{point.ticker}</p>
                      </div>
                    </div>
                    <div className="mt-3 flex items-center justify-between text-[11px]">
                      <div className="flex flex-col">
                        <span className="text-white/70">Rating</span>
                        <span className="font-semibold text-primary-200">{point.rating.grade}</span>
                      </div>
                      {typeof point.score === 'number' && (
                        <div className="flex flex-col text-right">
                          <span className="text-white/70">Health</span>
                          <span className="font-semibold text-white">{Math.round(point.score)}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </foreignObject>
              )}
            </Marker>
          ))}
        </ComposableMap>
      </div>
    </div>
  )
}

export default GeoImpactMap
