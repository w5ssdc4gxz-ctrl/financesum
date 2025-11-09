'use client'

import { useState } from 'react'
import Navbar from '@/components/Navbar'
import CompanySearch from '@/components/CompanySearch'
import { Button } from '@/components/base/buttons/button'

interface Company {
  id: string
  ticker: string
  name: string
  exchange: string
}

export default function ComparePage() {
  const [selectedCompanies, setSelectedCompanies] = useState<Company[]>([])

  const handleSelectCompany = (company: Company) => {
    // Limit to 4 companies
    if (selectedCompanies.length < 4 && !selectedCompanies.find(c => c.id === company.id)) {
      setSelectedCompanies([...selectedCompanies, company])
    }
  }

  const removeCompany = (companyId: string) => {
    setSelectedCompanies(selectedCompanies.filter(c => c.id !== companyId))
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Compare Companies</h1>
          <p className="text-gray-600">
            Select up to 4 companies to compare side-by-side
          </p>
        </div>

        <div className="mb-8">
          <CompanySearch onSelectCompany={handleSelectCompany} />
        </div>

        {selectedCompanies.length > 0 && (
          <div className="bg-white p-6 rounded-lg shadow">
            <h2 className="text-xl font-semibold mb-4">
              Selected Companies ({selectedCompanies.length}/4)
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {selectedCompanies.map(company => (
                <div key={company.id} className="border rounded-lg p-4 relative">
                  <Button
                    onClick={() => removeCompany(company.id)}
                    color="ghost"
                    size="sm"
                    className="absolute top-2 right-2 text-gray-400 hover:text-red-600 p-1"
                  >
                    ✕
                  </Button>
                  <div className="font-semibold text-gray-900">{company.ticker}</div>
                  <div className="text-sm text-gray-600 mt-1">{company.name}</div>
                </div>
              ))}
            </div>

            {selectedCompanies.length >= 2 && (
              <div className="mt-6">
                <Button
                  color="primary"
                  size="md"
                >
                  Generate Comparison
                </Button>
              </div>
            )}
          </div>
        )}

        {selectedCompanies.length === 0 && (
          <div className="bg-gray-100 border-2 border-dashed border-gray-300 rounded-lg p-12 text-center">
            <div className="text-gray-500 text-lg">
              Search and select companies to start comparing
            </div>
          </div>
        )}

        {selectedCompanies.length === 1 && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mt-4">
            <p className="text-blue-800 text-sm">
              Add at least one more company to enable comparison
            </p>
          </div>
        )}
      </main>
    </div>
  )
}










