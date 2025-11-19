'use client'

import { memo } from 'react'

type SectorItem = {
  label: string
  value: number
  percent: number
}

interface SectorStackProps {
  sectors: SectorItem[]
}

const SectorStack = memo(function SectorStack({ sectors }: SectorStackProps) {
  if (!sectors.length) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-6 text-sm text-slate-500">
        Add a couple of analyses to surface sector coverage.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {sectors.map((sector) => (
        <div key={sector.label} className="space-y-2">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
            <span className="uppercase tracking-[0.2em]">{sector.label}</span>
            <span>{sector.value} · {sector.percent}%</span>
          </div>
          <div className="h-2 rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500"
              style={{ width: `${sector.percent}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
})

export default SectorStack
