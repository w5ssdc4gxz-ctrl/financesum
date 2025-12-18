'use client'

import Link from 'next/link'
import { motion } from 'framer-motion'
import { LetterSwapForward } from '@/components/fancy'

const footerLinks = [
  { label: 'Dashboard', href: '/dashboard' },
  { label: 'Compare', href: '/compare' },
  { label: 'Sign Up', href: '/signup' },
]

export default function MinimalFooter() {
  return (
    <footer className="border-t border-border bg-white">
      <div className="container-wide py-16">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-8">
          {/* Brand */}
          <div className="space-y-4">
            <Link href="/" className="inline-block">
              <span className="text-2xl font-bold text-foreground tracking-tight">
                FinanceSum
              </span>
            </Link>
            <p className="text-sm text-muted-foreground max-w-xs">
              Financial analysis reimagined. AI-powered insights for smarter investment decisions.
            </p>
          </div>

          {/* Links */}
          <div className="flex items-center gap-8">
            {footerLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                <LetterSwapForward label={link.label} />
              </Link>
            ))}
          </div>
        </div>

        {/* Bottom */}
        <div className="mt-12 pt-8 border-t border-border flex flex-col sm:flex-row justify-between items-center gap-4">
          <p className="text-xs text-muted-foreground">
            {new Date().getFullYear()} FinanceSum. All rights reserved.
          </p>
          <div className="flex items-center gap-6 text-xs text-muted-foreground">
            <span>Made with precision</span>
          </div>
        </div>
      </div>

      {/* Large Background Text */}
      <div className="relative overflow-hidden py-8">
        <motion.div
          className="text-center"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
        >
          <span className="text-[10vw] md:text-[12vw] font-bold tracking-tighter text-muted/20 select-none font-flaviotte">
            FINANCESUM
          </span>
        </motion.div>
      </div>
    </footer>
  )
}
