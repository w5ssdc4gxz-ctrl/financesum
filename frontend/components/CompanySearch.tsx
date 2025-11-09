'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useQuery } from '@tanstack/react-query'
import { companyApi } from '@/lib/api-client'
import { fadeInUp, scaleIn } from '@/lib/animations'
import { Button } from '@/components/base/buttons/button'

interface Company {
  id: string
  ticker: string
  name: string
  exchange: string
}

interface CompanySearchProps {
  onSelectCompany: (company: Company) => void
}

export default function CompanySearch({ onSelectCompany }: CompanySearchProps) {
  const [query, setQuery] = useState('')
  const [searchTerm, setSearchTerm] = useState('')

  const { data, isLoading, error } = useQuery({
    queryKey: ['company-search', searchTerm],
    queryFn: async () => {
      if (!searchTerm) return null
      const response = await companyApi.lookup(searchTerm)
      return response.data
    },
    enabled: searchTerm.length > 0,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setSearchTerm(query)
  }

  return (
    <div className="w-full max-w-3xl">
      <form onSubmit={handleSearch} className="mb-6">
        <div className="relative group">
          <div className="absolute inset-0 bg-gradient-to-r from-primary-500 to-accent-500 rounded-2xl blur opacity-20 group-hover:opacity-40 transition-opacity"></div>
          <div className="relative flex shadow-premium">
            <div className="relative flex-1">
              <div className="absolute inset-y-0 left-0 pl-6 flex items-center pointer-events-none">
                <svg className="h-5 w-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </div>
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by ticker, company name, or CIK..."
                className="w-full pl-14 pr-4 py-5 bg-white border-2 border-gray-200 rounded-l-2xl focus:outline-none focus:border-primary-500 focus:ring-4 focus:ring-primary-100 transition-all text-gray-900 placeholder-gray-400 font-medium"
              />
            </div>
            <Button
              type="submit"
              color="primary"
              size="lg"
              disabled={!query}
              isLoading={isLoading}
              className="rounded-r-2xl px-8 py-5"
              asMotion={false}
              rightIcon={
                !isLoading ? (
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                  </svg>
                ) : undefined
              }
            >
              {isLoading ? 'Searching...' : 'Search'}
            </Button>
          </div>
        </div>
      </form>

      <AnimatePresence>
        {error && (
          <motion.div 
            className="bg-gradient-to-r from-red-500/10 to-red-600/10 border-2 border-red-500/30 text-red-300 px-6 py-4 rounded-2xl backdrop-blur-sm"
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.3 }}
          >
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
              <p className="font-medium">{(error as any)?.response?.data?.detail ?? 'Error searching for company. Please try again.'}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {data && data.companies && data.companies.length > 0 && (
          <motion.div 
            className="glass rounded-2xl shadow-premium-lg border border-white/20 overflow-hidden"
            initial={{ opacity: 0, y: -10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.95 }}
            transition={{ duration: 0.3 }}
          >
            <div className="divide-y divide-white/10">
              {data.companies.map((company: Company, index: number) => (
                <motion.button
                  key={company.id}
                  onClick={() => onSelectCompany(company)}
                  className="w-full px-6 py-5 text-left hover:bg-white/10 focus:outline-none focus:bg-white/10 transition-all group"
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.05, duration: 0.3 }}
                  whileHover={{ x: 4, backgroundColor: 'rgba(255, 255, 255, 0.1)' }}
                  whileTap={{ scale: 0.98 }}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-2">
                        <span className="font-bold text-xl text-white group-hover:text-primary-300 transition-colors">{company.ticker}</span>
                        <span className="px-2 py-1 rounded-md bg-primary-500/20 text-primary-300 text-xs font-semibold">{company.exchange}</span>
                      </div>
                      <div className="text-sm text-gray-300 group-hover:text-gray-200 transition-colors">{company.name}</div>
                    </div>
                    <motion.svg 
                      className="w-6 h-6 text-gray-400 group-hover:text-primary-400" 
                      fill="none" 
                      stroke="currentColor" 
                      viewBox="0 0 24 24"
                      animate={{ x: 0 }}
                      whileHover={{ x: 4 }}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </motion.svg>
                  </div>
                </motion.button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {data && data.companies && data.companies.length === 0 && (
          <motion.div 
            className="glass rounded-2xl shadow-premium border border-white/20 px-6 py-8 text-center"
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9 }}
            transition={{ duration: 0.3 }}
          >
            <motion.div 
              className="w-16 h-16 rounded-full bg-gray-500/10 border border-gray-500/20 flex items-center justify-center mx-auto mb-4"
              animate={{ rotate: [0, 10, -10, 0] }}
              transition={{ duration: 0.5, delay: 0.2 }}
            >
              <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </motion.div>
            <p className="text-gray-300 font-medium">No companies found. Try a different search term.</p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}



