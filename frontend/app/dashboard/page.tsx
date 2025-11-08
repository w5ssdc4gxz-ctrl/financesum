'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Navbar from '@/components/Navbar'
import CompanySearch from '@/components/CompanySearch'
import { useAuth } from '@/contexts/AuthContext'

interface Company {
  id: string
  ticker: string
  name: string
  exchange: string
}

export default function Dashboard() {
  const { user, loading } = useAuth()
  const router = useRouter()
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null)

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900 flex items-center justify-center">
        <div className="text-center">
          <div className="spinner mx-auto mb-4"></div>
          <p className="text-gray-300 text-xl">Loading your dashboard...</p>
        </div>
      </div>
    )
  }

  const handleSelectCompany = (company: Company) => {
    setSelectedCompany(company)
    router.push(`/company/${company.id}`)
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900">
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Hero Section */}
        <div className="mb-12 text-center animate-fade-in">
          <h1 className="text-4xl md:text-5xl font-bold mb-4">
            <span className="text-white">Welcome to Your</span>
            <span className="gradient-text"> Investment Hub</span>
          </h1>
          <p className="text-gray-300 text-lg">
            Search for any company to unlock AI-powered financial insights
          </p>
        </div>

        {/* Search Section */}
        <div className="mb-16 flex justify-center animate-slide-up">
          <CompanySearch onSelectCompany={handleSelectCompany} />
        </div>

        {/* Dashboard Cards */}
        {user ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 animate-scale-in">
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold text-white">Recent Analyses</h2>
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                  </svg>
                </div>
              </div>
              <div className="text-center py-8">
                <div className="w-16 h-16 rounded-full bg-primary-500/10 border border-primary-500/20 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                  </svg>
                </div>
                <p className="text-gray-400">
                  Your recent company analyses will appear here
                </p>
              </div>
            </div>
            
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold text-white">Watchlist</h2>
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
                  </svg>
                </div>
              </div>
              <div className="text-center py-8">
                <div className="w-16 h-16 rounded-full bg-primary-500/10 border border-primary-500/20 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                  </svg>
                </div>
                <p className="text-gray-400">
                  Add companies to your watchlist to track them
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="max-w-2xl mx-auto">
            <div className="card-premium bg-gradient-to-br from-primary-900/30 to-accent-900/30 border-primary-500/40 text-center">
              <div className="w-16 h-16 rounded-full bg-primary-500/20 border border-primary-500/40 flex items-center justify-center mx-auto mb-6">
                <svg className="w-8 h-8 text-primary-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <h3 className="text-2xl font-bold text-white mb-3">
                Unlock Your Full Potential
              </h3>
              <p className="text-gray-300 mb-6 leading-relaxed">
                Sign in to save your analyses, build watchlists, and track companies over time with advanced features.
              </p>
              <div className="flex justify-center">
                <span className="inline-flex items-center gap-2 text-primary-300">
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                  <span className="font-medium">Free to get started</span>
                </span>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}










