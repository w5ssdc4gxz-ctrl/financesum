'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import Navbar from '@/components/Navbar'
import { useAuth } from '@/contexts/AuthContext'
import { billingApi } from '@/lib/api-client'

const CHECKOUT_SESSION_STORAGE_KEY = 'financesum.checkout.session_id'

export default function BillingSuccessPage() {
  const router = useRouter()
  const params = useSearchParams()
  const { user, session, loading: authLoading } = useAuth()

  const sessionId = params.get('session_id')
  const [syncing, setSyncing] = useState(true)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (authLoading) return
    if (!user || !session) {
      router.push('/signin')
    }
  }, [authLoading, user, session, router])

  useEffect(() => {
    if (!user || !session) return
    if (!sessionId) {
      setSyncing(false)
      setMessage('Missing checkout session id.')
      return
    }

    let cancelled = false
    setSyncing(true)
    billingApi
      .syncCheckoutSession(sessionId, session.access_token)
      .then(() => {
        if (cancelled) return
        if (typeof window !== 'undefined') {
          window.localStorage.removeItem(CHECKOUT_SESSION_STORAGE_KEY)
        }
        setMessage('Subscription activated. Welcome to Pro.')
      })
      .catch((err) => {
        if (cancelled) return
        setMessage(err?.response?.data?.detail || err?.message || 'Subscription created, but syncing failed.')
      })
      .finally(() => {
        if (cancelled) return
        setSyncing(false)
      })

    return () => {
      cancelled = true
    }
  }, [user, session, sessionId])

  return (
    <div className="min-h-screen bg-white">
      <Navbar />
      <main className="container mx-auto max-w-2xl px-4 pt-28 pb-16">
        <h1 className="text-3xl font-black tracking-tight text-black">Payment successful</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          {syncing ? 'Finalizing your subscription…' : message}
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/dashboard"
            className="inline-flex items-center justify-center rounded-xl bg-[#0015ff] px-5 py-3 text-sm font-semibold text-white"
          >
            Go to Dashboard
          </Link>
          <Link
            href={sessionId ? `/billing?session_id=${encodeURIComponent(sessionId)}` : '/billing'}
            className="inline-flex items-center justify-center rounded-xl bg-black px-5 py-3 text-sm font-semibold text-white"
          >
            Manage Billing
          </Link>
        </div>
      </main>
    </div>
  )
}
