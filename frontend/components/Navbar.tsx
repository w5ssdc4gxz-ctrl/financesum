'use client'

import Link from 'next/link'
import { useAuth } from '@/contexts/AuthContext'

export default function Navbar() {
  const { user, loading, signIn, signOut } = useAuth()

  const handleSignIn = async () => {
    try {
      await signIn()
    } catch (error: any) {
      const message = error?.message ?? 'Unable to sign in'
      alert(message)
    }
  }

  return (
    <nav className="glass-dark sticky top-0 z-50 border-b border-white/10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex items-center">
            <Link href="/" className="flex items-center group">
              <span className="text-2xl font-bold bg-gradient-to-r from-primary-400 to-accent-400 bg-clip-text text-transparent transition-all group-hover:scale-105">
                FinanceSum
              </span>
            </Link>
            {user && (
              <div className="hidden sm:ml-10 sm:flex sm:space-x-2">
                <Link
                  href="/dashboard"
                  className="inline-flex items-center px-4 py-2 text-sm font-medium text-white hover:text-primary-300 rounded-lg hover:bg-white/10 transition-all"
                >
                  Dashboard
                </Link>
                <Link
                  href="/compare"
                  className="inline-flex items-center px-4 py-2 text-sm font-medium text-gray-300 hover:text-primary-300 rounded-lg hover:bg-white/10 transition-all"
                >
                  Compare
                </Link>
              </div>
            )}
          </div>
          <div className="flex items-center gap-4">
            {loading ? (
              <div className="spinner"></div>
            ) : user ? (
              <>
                <span className="hidden sm:block text-sm text-gray-300">
                  {user.email}
                </span>
                <button
                  onClick={signOut}
                  className="px-4 py-2 text-sm font-semibold rounded-lg text-white border border-white/20 hover:border-primary-500 hover:bg-white/10 transition-all"
                >
                  Sign Out
                </button>
              </>
            ) : (
              <button
                onClick={handleSignIn}
                className="btn-premium"
              >
                Sign In
              </button>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}









