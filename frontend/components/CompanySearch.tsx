'use client'

/* eslint-disable @next/next/no-img-element -- Search result logos use raw img fallbacks. */

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useQuery } from '@tanstack/react-query'
import { companyApi } from '@/lib/api-client'
import { PlaceholdersAndVanishInput } from '@/components/ui/placeholders-and-vanish-input'

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

  const placeholders = [
    "Search by ticker (e.g. AAPL, MSFT)...",
    "Search by company name (e.g. Nvidia, Tesla)...",
    "Search by CIK number...",
    "Analyze your favorite stock...",
    "Find detailed financial reports...",
  ];

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value);
  };

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSearchTerm(query);
  };

  return (
    <div className="w-full max-w-3xl relative">
      <div className="mb-8">
        <PlaceholdersAndVanishInput
          placeholders={placeholders}
          onChange={handleChange}
          onSubmit={onSubmit}
        />
      </div>

      <AnimatePresence>
        {isLoading && (
          <motion.div
            className="absolute top-full left-0 right-0 mt-4 bg-white dark:bg-zinc-950 rounded-none border border-black dark:border-white shadow-[4px_4px_0_0_#000] dark:shadow-[4px_4px_0_0_#fff] p-8 text-center z-50"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.2 }}
          >
            <div className="flex flex-col items-center justify-center gap-3">
              <div className="w-8 h-8 border-4 border-black/30 dark:border-white/30 border-t-black dark:border-t-white rounded-none flex items-center justify-center animate-spin" />
              <p className="text-sm font-bold uppercase tracking-widest text-black dark:text-white">Searching...</p>
            </div>
          </motion.div>
        )}
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
            className="absolute top-full left-0 right-0 mt-4 bg-white dark:bg-zinc-950 rounded-none border border-black dark:border-white shadow-[4px_4px_0_0_#000] dark:shadow-[4px_4px_0_0_#fff] overflow-hidden z-50"
            initial={{ opacity: 0, y: -10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.98 }}
            transition={{ duration: 0.2 }}
          >
            <div className="max-h-[400px] overflow-y-auto py-2">
              {data.companies.map((company: Company, index: number) => (
                <motion.button
                  key={company.id}
                  onClick={() => onSelectCompany(company)}
                  className="w-full px-4 py-3 flex items-center gap-4 hover:bg-gray-50 dark:hover:bg-zinc-800/50 transition-colors group text-left"
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.03, duration: 0.2 }}
                >
                  {/* Logo */}
                  <div className="relative h-10 w-10 flex-shrink-0 rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 overflow-hidden flex items-center justify-center">
                    <img
                      src={`/api/backend/api/v1/companies/logo/${company.ticker}`}
                      alt={`${company.name} logo`}
                      className="h-full w-full object-contain p-1"
                      onError={(e) => {
                        e.currentTarget.style.display = 'none';
                        e.currentTarget.nextElementSibling?.classList.remove('hidden');
                      }}
                    />
                    <div className="hidden absolute inset-0 flex items-center justify-center text-sm font-bold uppercase tracking-widest text-black dark:text-white">
                      {company.ticker.slice(0, 2)}
                    </div>
                  </div>

                  {/* Text Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-black dark:text-white truncate">
                        {company.ticker}
                      </span>
                      <span className="px-1.5 py-0.5 rounded-none text-[10px] font-bold uppercase tracking-widest border border-black dark:border-white bg-white dark:bg-zinc-950 text-black dark:text-white">
                        {company.exchange}
                      </span>
                    </div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400 truncate">
                      {company.name}
                    </div>
                  </div>

                  {/* Arrow */}
                  <svg
                    className="h-5 w-5 text-gray-300 group-hover:text-indigo-500 transition-colors"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </motion.button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {data && data.companies && data.companies.length === 0 && (
          <motion.div
            className="absolute top-full left-0 right-0 mt-4 bg-white dark:bg-zinc-950 rounded-none border border-black dark:border-white shadow-[4px_4px_0_0_#000] dark:shadow-[4px_4px_0_0_#fff] p-8 text-center z-50"
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.2 }}
          >
            <div className="mx-auto h-12 w-12 rounded-none border border-black dark:border-white bg-white dark:bg-zinc-950 flex items-center justify-center mb-3">
              <svg className="h-6 w-6 text-black dark:text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="square" strokeLinejoin="miter" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <h3 className="text-sm font-bold uppercase tracking-widest text-black dark:text-white">No results found</h3>
            <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400 mt-1">
              We couldn&apos;t find any companies matching &quot;{searchTerm}&quot;
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

