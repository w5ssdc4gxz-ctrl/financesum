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
    <div className="bg-white p-6 rounded-lg shadow">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold">Select Investor Personas</h3>
        <div className="space-x-2">
          <Button
            onClick={selectAll}
            color="ghost"
            size="sm"
            className="text-primary-600 hover:text-primary-700"
          >
            Select All
          </Button>
          <Button
            onClick={clearAll}
            color="ghost"
            size="sm"
            className="text-gray-600 hover:text-gray-700"
          >
            Clear
          </Button>
        </div>
      </div>
      
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {PERSONAS.map(persona => (
          <label
            key={persona.id}
            className="flex items-start space-x-3 p-3 border rounded-lg cursor-pointer hover:bg-gray-50"
          >
            <input
              type="checkbox"
              checked={selectedPersonas.includes(persona.id)}
              onChange={() => togglePersona(persona.id)}
              className="mt-1 h-4 w-4 text-primary-600 rounded"
            />
            <div className="flex-1">
              <div className="font-medium text-sm">{persona.name}</div>
              <div className="text-xs text-gray-500">{persona.description}</div>
            </div>
          </label>
        ))}
      </div>
      
      <div className="mt-4 text-xs text-gray-500">
        Selected: {selectedPersonas.length} / {PERSONAS.length}
      </div>
    </div>
  )
}












