'use client'

import { useState } from 'react'
import { Button } from '@/components/base/buttons/button'

const PERSONAS = [
  { id: 'buffett', name: 'Warren Buffett', description: 'Value, moat, FCF focus' },
  { id: 'munger', name: 'Charlie Munger', description: 'Rational, quality businesses' },
  { id: 'graham', name: 'Benjamin Graham', description: 'Margin of safety, quantitative' },
  { id: 'lynch', name: 'Peter Lynch', description: 'Growth at reasonable price' },
  { id: 'dalio', name: 'Ray Dalio', description: 'Macro-aware, risk parity' },
  { id: 'wood', name: 'Cathie Wood', description: 'Disruptive innovation' },
  { id: 'greenblatt', name: 'Joel Greenblatt', description: 'Magic formula value' },
  { id: 'bogle', name: 'John Bogle', description: 'Index investor, low costs' },
  { id: 'marks', name: 'Howard Marks', description: 'Cycles, risk assessment' },
  { id: 'ackman', name: 'Bill Ackman', description: 'Activist, catalysts' },
]

interface PersonaSelectorProps {
  selectedPersonas: string[]
  onSelectionChange: (selected: string[]) => void
}

export default function PersonaSelector({ selectedPersonas, onSelectionChange }: PersonaSelectorProps) {
  const togglePersona = (personaId: string) => {
    if (selectedPersonas.includes(personaId)) {
      onSelectionChange(selectedPersonas.filter(id => id !== personaId))
    } else {
      onSelectionChange([...selectedPersonas, personaId])
    }
  }

  const selectAll = () => {
    onSelectionChange(PERSONAS.map(p => p.id))
  }

  const clearAll = () => {
    onSelectionChange([])
  }

  return (
    <div className="bg-white dark:bg-zinc-900 border-2 border-black dark:border-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]">
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-lg font-black uppercase text-black dark:text-white">Select Investor Personas</h3>
        <div className="space-x-2">
          <button
            onClick={selectAll}
            className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white hover:bg-black hover:text-white dark:hover:bg-white dark:hover:text-black transition-all"
          >
            Select All
          </button>
          <button
            onClick={clearAll}
            className="px-3 py-1 text-xs font-bold uppercase border-2 border-black dark:border-white hover:bg-red-500 hover:text-white transition-all"
          >
            Clear
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {PERSONAS.map(persona => (
          <label
            key={persona.id}
            className={`flex items-start space-x-3 p-4 border-2 border-black dark:border-white cursor-pointer transition-all duration-200 ${selectedPersonas.includes(persona.id)
                ? 'bg-black text-white dark:bg-white dark:text-black shadow-[4px_4px_0px_0px_rgba(128,128,128,1)] translate-y-[-2px] translate-x-[-2px]'
                : 'bg-white dark:bg-black hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] dark:hover:shadow-[4px_4px_0px_0px_rgba(255,255,255,1)]'
              }`}
          >
            <div className="relative flex items-center mt-1">
              <input
                type="checkbox"
                checked={selectedPersonas.includes(persona.id)}
                onChange={() => togglePersona(persona.id)}
                className="peer h-5 w-5 cursor-pointer appearance-none border-2 border-black dark:border-white bg-white checked:bg-blue-600 transition-all"
              />
              <svg
                className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-white opacity-0 peer-checked:opacity-100 transition-opacity"
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  d="M10 3L4.5 8.5L2 6"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="square"
                  strokeLinejoin="miter"
                />
              </svg>
            </div>
            <div className="flex-1">
              <div className="font-black uppercase text-sm">
                {persona.name}
              </div>
              <div className={`text-xs mt-1 font-mono ${selectedPersonas.includes(persona.id)
                  ? 'text-gray-300 dark:text-gray-600'
                  : 'text-gray-500 dark:text-gray-400'
                }`}>
                {persona.description}
              </div>
            </div>
          </label>
        ))}
      </div>

      <div className="mt-6 flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 border-t-2 border-gray-100 dark:border-gray-800 pt-4 font-mono">
        <span>Select multiple personas to compare different viewpoints</span>
        <span className="font-bold bg-gray-100 dark:bg-gray-800 px-2 py-1 border border-black dark:border-white text-black dark:text-white">
          {selectedPersonas.length} selected
        </span>
      </div>
    </div>
  )
}
















