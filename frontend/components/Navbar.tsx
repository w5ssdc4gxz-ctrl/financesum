'use client'

import Link from 'next/link'
import { motion } from 'framer-motion'
import { useAuth } from '@/contexts/AuthContext'
import { LetterSwapForward } from '@/components/fancy'

interface NavbarProps {
  variant?: 'light' | 'dark'
}

export default function Navbar({ variant = 'light' }: NavbarProps) {
  const { user, loading, signOut } = useAuth()

  const isDark = variant === 'dark'

  return (
    <motion.nav
      className={`fixed top-0 left-0 right-0 z-50 backdrop-blur-lg border-b ${
        isDark
          ? 'bg-[#050015]/80 border-white/10'
          : 'bg-white/80 border-border'
      }`}
      initial={{ y: -100, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
    >
      <div className="container-wide">
        <div className="flex justify-between items-center h-16">
          {/* Logo */}
          <Link href="/" className="flex items-center">
            <span className={`text-xl font-bold tracking-tight ${isDark ? 'text-white' : 'text-foreground'}`}>
              FinanceSum
            </span>
          </Link>

          {/* Navigation Links */}
          {user && (
            <motion.div
              className="hidden md:flex items-center gap-8"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.2 }}
            >
              <Link
                href="/dashboard"
                className={`text-sm font-medium transition-colors ${
                  isDark
                    ? 'text-white/70 hover:text-white'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <LetterSwapForward label="Dashboard" />
              </Link>
              <Link
                href="/compare"
                className={`text-sm font-medium transition-colors ${
                  isDark
                    ? 'text-white/70 hover:text-white'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <LetterSwapForward label="Compare" />
              </Link>
            </motion.div>
          )}

          {/* Right Side */}
          <motion.div
            className="flex items-center gap-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
          >
            {loading ? (
              <div className={`w-5 h-5 border-2 rounded-full animate-spin ${
                isDark
                  ? 'border-white/20 border-t-white'
                  : 'border-muted-foreground/20 border-t-foreground'
              }`} />
            ) : user ? (
              <>
                <span className={`hidden sm:block text-sm ${isDark ? 'text-white/70' : 'text-muted-foreground'}`}>
                  {user.email}
                </span>
                <button
                  onClick={signOut}
                  className={`text-sm font-medium transition-colors ${
                    isDark
                      ? 'text-white/70 hover:text-white'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Sign Out
                </button>
              </>
            ) : (
              <Link
                href="/signup"
                className="inline-flex items-center justify-center px-5 py-2 text-sm font-medium text-white bg-[#0015ff] rounded-full hover:bg-[#0012cc] transition-colors"
              >
                Get Started
              </Link>
            )}
          </motion.div>
        </div>
      </div>
    </motion.nav>
  )
}
