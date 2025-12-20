'use client'

import Link from 'next/link'
import Navbar from '@/components/Navbar'

export default function BillingCancelPage() {
  return (
    <div className="min-h-screen bg-white">
      <Navbar />
      <main className="container mx-auto max-w-2xl px-4 pt-28 pb-16">
        <h1 className="text-3xl font-black tracking-tight text-black">Checkout canceled</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          No worries — you can upgrade anytime.
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/"
            className="inline-flex items-center justify-center rounded-xl bg-black px-5 py-3 text-sm font-semibold text-white"
          >
            Back to Home
          </Link>
          <Link
            href="/billing"
            className="inline-flex items-center justify-center rounded-xl bg-[#0015ff] px-5 py-3 text-sm font-semibold text-white"
          >
            Billing
          </Link>
        </div>
      </main>
    </div>
  )
}

