'use client'

import Link from 'next/link'
import { motion } from 'framer-motion'
import { useAuth } from '@/contexts/AuthContext'
import { Button } from '@/components/base/buttons/button'

export default function Navbar() {
  const { user, loading, signOut } = useAuth()

  return (
    <motion.nav 
      className="glass-dark sticky top-0 z-50 border-b border-white/10 backdrop-blur-xl bg-dark-900/80"
      initial={{ y: -100, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.6, ease: "easeOut" }}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex items-center">
            <Link href="/" className="flex items-center group">
              <motion.span 
                className="text-2xl font-bold bg-gradient-to-r from-primary-400 to-accent-400 bg-clip-text text-transparent"
                whileHover={{ scale: 1.05 }}
                transition={{ type: 'spring', stiffness: 400, damping: 10 }}
              >
                FinanceSum
              </motion.span>
            </Link>
            {user && (
              <motion.div 
                className="hidden sm:ml-10 sm:flex sm:space-x-2"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.2 }}
              >
                <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                  <Link
                    href="/dashboard"
                    className="inline-flex items-center px-4 py-2 text-sm font-medium text-white hover:text-primary-300 rounded-lg hover:bg-white/10 transition-all"
                  >
                    Dashboard
                  </Link>
                </motion.div>
                <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
                  <Link
                    href="/compare"
                    className="inline-flex items-center px-4 py-2 text-sm font-medium text-gray-300 hover:text-primary-300 rounded-lg hover:bg-white/10 transition-all"
                  >
                    Compare
                  </Link>
                </motion.div>
              </motion.div>
            )}
          </div>
          <motion.div 
            className="flex items-center gap-4"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3 }}
          >
            {loading ? (
              <div className="spinner"></div>
            ) : user ? (
              <>
                <motion.span 
                  className="hidden sm:block text-sm text-gray-300"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.4 }}
                >
                  {user.email}
                </motion.span>
                <Button
                  onClick={signOut}
                  color="secondary"
                  size="sm"
                >
                  Sign Out
                </Button>
              </>
            ) : (
              <Link
                href="/signup"
                className="inline-flex items-center rounded-xl border border-white/30 px-5 py-2 text-sm font-semibold text-white transition-all duration-300 hover:border-primary-400 hover:bg-white/5"
              >
                Join the beta
              </Link>
            )}
          </motion.div>
        </div>
      </div>
    </motion.nav>
  )
}









