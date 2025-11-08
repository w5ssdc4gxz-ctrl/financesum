'use client'

import Link from 'next/link'
import Navbar from '@/components/Navbar'
import { useAuth } from '@/contexts/AuthContext'

export default function Home() {
  const { user } = useAuth()

  return (
    <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900">
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Hero Section */}
        <div className="text-center py-20 md:py-32 animate-fade-in">
          <div className="mb-6">
            <span className="inline-block px-4 py-2 rounded-full bg-primary-500/10 border border-primary-500/20 text-primary-300 text-sm font-semibold mb-6">
              ✨ AI-Powered Financial Intelligence
            </span>
          </div>
          <h1 className="text-5xl md:text-7xl font-bold mb-6 leading-tight">
            <span className="text-white">Financial Analysis</span>
            <br />
            <span className="gradient-text inline-block mt-2">
              Reimagined for You
            </span>
          </h1>
          <p className="text-xl md:text-2xl text-gray-300 mb-12 max-w-3xl mx-auto leading-relaxed">
            Transform SEC filings into actionable insights. Get AI-powered analysis through the lens of legendary investors.
          </p>
          <div className="flex flex-col sm:flex-row justify-center gap-4 mb-8">
            {user ? (
              <Link
                href="/dashboard"
                className="btn-premium text-lg"
              >
                Go to Dashboard →
              </Link>
            ) : (
              <Link
                href="/dashboard"
                className="btn-premium text-lg"
              >
                Get Started Free →
              </Link>
            )}
            <Link
              href="#features"
              className="px-8 py-3 rounded-lg font-semibold text-white border-2 border-white/20 hover:border-primary-500 transition-all duration-300 hover:bg-white/5"
            >
              Learn More
            </Link>
          </div>
          <div className="flex items-center justify-center gap-8 text-gray-400 text-sm">
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-green-400" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>No credit card required</span>
            </div>
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-green-400" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Setup in 2 minutes</span>
            </div>
          </div>
        </div>

        {/* Features */}
        <div id="features" className="py-20 bg-gradient-to-b from-dark-900 to-dark-800">
          <div className="text-center mb-16 animate-slide-up">
            <h2 className="text-4xl md:text-5xl font-bold text-white mb-4">
              Everything You Need to <span className="gradient-text">Invest Smarter</span>
            </h2>
            <p className="text-gray-400 text-lg">Powerful tools designed for serious investors</p>
          </div>
          
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50 group">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <span className="text-2xl">📊</span>
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">Automated Analysis</h3>
              <p className="text-gray-400 leading-relaxed">
                Calculate comprehensive financial ratios and health scores automatically from SEC filings
              </p>
            </div>
            
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50 group">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <span className="text-2xl">🤖</span>
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">AI-Powered Insights</h3>
              <p className="text-gray-400 leading-relaxed">
                Generate investor-grade memos with risks, catalysts, and key metrics using advanced AI
              </p>
            </div>
            
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50 group">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <span className="text-2xl">👥</span>
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">Investor Personas</h3>
              <p className="text-gray-400 leading-relaxed">
                See how Warren Buffett, Cathie Wood, and 8 other legendary investors would analyze each company
              </p>
            </div>
            
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50 group">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <span className="text-2xl">📈</span>
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">Visual Charts</h3>
              <p className="text-gray-400 leading-relaxed">
                Interactive charts showing financial trends, ratio comparisons, and historical performance
              </p>
            </div>
            
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50 group">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <span className="text-2xl">⚖️</span>
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">Compare Companies</h3>
              <p className="text-gray-400 leading-relaxed">
                Side-by-side comparison of multiple companies with their key metrics and ratios
              </p>
            </div>
            
            <div className="card-premium bg-gradient-to-br from-dark-800 to-dark-900 border-primary-500/20 hover:border-primary-500/50 group">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-primary-500 to-accent-500 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                <span className="text-2xl">📥</span>
              </div>
              <h3 className="text-xl font-bold mb-3 text-white">Export & Share</h3>
              <p className="text-gray-400 leading-relaxed">
                Download comprehensive analysis as PDF, Word, or Markdown for your investment records
              </p>
            </div>
          </div>
        </div>

        {/* How It Works */}
        <div className="py-20 bg-dark-900">
          <div className="text-center mb-16">
            <h2 className="text-4xl md:text-5xl font-bold text-white mb-4">
              Get Started in <span className="gradient-text">Minutes</span>
            </h2>
            <p className="text-gray-400 text-lg">Simple, fast, and powerful</p>
          </div>
          
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="flex items-start gap-6 p-8 rounded-2xl bg-gradient-to-r from-dark-800/50 to-dark-800/30 border border-primary-500/20 hover:border-primary-500/40 transition-all">
              <div className="flex-shrink-0 w-16 h-16 bg-gradient-to-br from-primary-600 to-accent-600 text-white rounded-2xl flex items-center justify-center font-bold text-2xl shadow-premium">
                1
              </div>
              <div>
                <h3 className="text-2xl font-bold mb-2 text-white">Search for a Company</h3>
                <p className="text-gray-400 text-lg leading-relaxed">
                  Enter a ticker symbol, company name, or CIK to instantly fetch SEC filings and financial data
                </p>
              </div>
            </div>
            
            <div className="flex items-start gap-6 p-8 rounded-2xl bg-gradient-to-r from-dark-800/50 to-dark-800/30 border border-primary-500/20 hover:border-primary-500/40 transition-all">
              <div className="flex-shrink-0 w-16 h-16 bg-gradient-to-br from-primary-600 to-accent-600 text-white rounded-2xl flex items-center justify-center font-bold text-2xl shadow-premium">
                2
              </div>
              <div>
                <h3 className="text-2xl font-bold mb-2 text-white">Select Filings</h3>
                <p className="text-gray-400 text-lg leading-relaxed">
                  Choose which quarterly or annual reports to analyze with our intuitive filing selector
                </p>
              </div>
            </div>
            
            <div className="flex items-start gap-6 p-8 rounded-2xl bg-gradient-to-r from-dark-800/50 to-dark-800/30 border border-primary-500/20 hover:border-primary-500/40 transition-all">
              <div className="flex-shrink-0 w-16 h-16 bg-gradient-to-br from-primary-600 to-accent-600 text-white rounded-2xl flex items-center justify-center font-bold text-2xl shadow-premium">
                3
              </div>
              <div>
                <h3 className="text-2xl font-bold mb-2 text-white">Get AI Analysis</h3>
                <p className="text-gray-400 text-lg leading-relaxed">
                  Our AI extracts data, calculates comprehensive ratios, and generates professional investment analysis
                </p>
              </div>
            </div>
            
            <div className="flex items-start gap-6 p-8 rounded-2xl bg-gradient-to-r from-dark-800/50 to-dark-800/30 border border-primary-500/20 hover:border-primary-500/40 transition-all">
              <div className="flex-shrink-0 w-16 h-16 bg-gradient-to-br from-primary-600 to-accent-600 text-white rounded-2xl flex items-center justify-center font-bold text-2xl shadow-premium">
                4
              </div>
              <div>
                <h3 className="text-2xl font-bold mb-2 text-white">Explore Investor Views</h3>
                <p className="text-gray-400 text-lg leading-relaxed">
                  See simulated perspectives from 10 legendary investors including Warren Buffett and Cathie Wood
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Disclaimer */}
        <div className="py-20 bg-dark-800">
          <div className="max-w-4xl mx-auto">
            <div className="bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border border-yellow-500/30 rounded-2xl p-8">
              <div className="flex items-start gap-4">
                <div className="flex-shrink-0 w-12 h-12 bg-yellow-500/20 rounded-xl flex items-center justify-center">
                  <svg className="w-6 h-6 text-yellow-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-xl font-bold text-yellow-300 mb-3">Important Disclaimer</h3>
                  <p className="text-gray-300 leading-relaxed">
                    All investor persona outputs are simulations based on publicly available writings and investment philosophies. 
                    They do not represent actual advice from these investors or constitute financial advice. 
                    Always conduct your own research and consult with financial professionals before making investment decisions.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        {/* CTA Section */}
        <div className="py-20 bg-gradient-to-br from-primary-900 via-accent-900 to-primary-900">
          <div className="text-center max-w-4xl mx-auto">
            <h2 className="text-4xl md:text-5xl font-bold text-white mb-6">
              Ready to Transform Your Investment Research?
            </h2>
            <p className="text-xl text-gray-300 mb-10">
              Join investors who are making smarter decisions with AI-powered financial analysis
            </p>
            <Link
              href="/dashboard"
              className="btn-premium text-lg inline-block"
            >
              Start Analyzing Now →
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}










