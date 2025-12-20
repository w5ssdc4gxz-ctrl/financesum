'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import Navbar from '@/components/Navbar'
import { useAuth } from '@/contexts/AuthContext'
import { billingApi } from '@/lib/api-client'

const CHECKOUT_SESSION_STORAGE_KEY = 'financesum.checkout.session_id'

type SubscriptionResponse = {
  is_pro?: boolean
  customer_id?: string | null
  subscription?: {
    status?: string | null
    current_period_end?: string | null
    cancel_at_period_end?: boolean | null
    price_id?: string | null
  } | null
}

export default function BillingPage() {
  const router = useRouter()
  const { user, session, loading: authLoading } = useAuth()

  const [loading, setLoading] = useState(true)
  const [payload, setPayload] = useState<SubscriptionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stripeConfigured, setStripeConfigured] = useState<boolean | null>(null)
  const [portalLoading, setPortalLoading] = useState(false)
  const [checkoutLoading, setCheckoutLoading] = useState(false)

  const refreshStripeConfig = async () => {
    try {
      const res = await billingApi.getConfig()
      const configured = Boolean(res.data?.secret_key_configured)
      setStripeConfigured(configured)
      return configured
    } catch {
      setStripeConfigured(null)
      return null
    }
  }

  useEffect(() => {
    if (authLoading) return
    if (!user || !session) {
      router.push('/signup')
    }
  }, [authLoading, user, session, router])

  useEffect(() => {
    let cancelled = false
    billingApi
      .getConfig()
      .then((res) => {
        if (cancelled) return
        setStripeConfigured(Boolean(res.data?.secret_key_configured))
      })
      .catch(() => {
        if (cancelled) return
        setStripeConfigured(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!user || !session) return

    let cancelled = false
    setLoading(true)
    setError(null)
    if (stripeConfigured === false) {
      setPayload({ is_pro: false, customer_id: null, subscription: null })
      setError("Billing isn't configured on the backend yet. Add STRIPE_SECRET_KEY to the repo-root `.env` and refresh.")
      setLoading(false)
      return
    }
    billingApi
      .getSubscription(session.access_token)
      .then((res) => {
        if (cancelled) return
        setPayload(res.data ?? null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err?.response?.data?.detail || err?.message || 'Unable to load billing status.')
      })
      .finally(() => {
        if (cancelled) return
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [user, session, stripeConfigured])

  const isPro = Boolean(payload?.is_pro)
  const subscription = payload?.subscription ?? null
  const isCanceling = Boolean(
    subscription?.cancel_at_period_end || (subscription?.status === 'canceled' && isPro)
  )

  const statusLabel = useMemo(() => {
    const status = subscription?.status
    if (!status) return '—'
    const formatted = status.replace(/_/g, ' ')
    return subscription?.cancel_at_period_end ? `${formatted} (canceling)` : formatted
  }, [subscription?.status, subscription?.cancel_at_period_end])

  const cancellationLabel = useMemo(() => {
    if (!subscription?.current_period_end) return 'the current period ends'
    const parsed = new Date(subscription.current_period_end)
    return Number.isNaN(parsed.valueOf()) ? 'the current period ends' : parsed.toLocaleDateString()
  }, [subscription?.current_period_end])

  const handleOpenPortal = async () => {
    if (!session?.access_token) {
      router.push('/signup')
      return
    }
    const configured = stripeConfigured === true ? true : await refreshStripeConfig()
    if (!configured) {
      setError("Billing isn't configured on the backend yet. Add STRIPE_SECRET_KEY to the repo-root `.env` and refresh.")
      return
    }
    if (portalLoading) return
    setPortalLoading(true)
    try {
      const res = await billingApi.createPortalSession(session.access_token)
      const url = res.data?.url as string | undefined
      if (!url) throw new Error('Missing portal URL')
      window.location.href = url
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Unable to open billing portal.')
    } finally {
      setPortalLoading(false)
    }
  }

  const handleUpgrade = async () => {
    if (!session?.access_token) {
      router.push('/signup')
      return
    }
    const configured = stripeConfigured === true ? true : await refreshStripeConfig()
    if (!configured) {
      setError("Billing isn't configured on the backend yet. Add STRIPE_SECRET_KEY to the repo-root `.env` and refresh.")
      return
    }
    if (checkoutLoading) return
    setCheckoutLoading(true)
    setError(null)
    try {
      const res = await billingApi.createCheckoutSession({ plan: 'pro' }, session.access_token)
      const url = res.data?.url as string | undefined
      const sessionId = res.data?.id as string | undefined
      if (!url) throw new Error('Missing checkout URL')
      if (sessionId && typeof window !== 'undefined') {
        window.localStorage.setItem(CHECKOUT_SESSION_STORAGE_KEY, sessionId)
      }
      window.location.href = url
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Unable to start checkout.')
    } finally {
      setCheckoutLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-white">
      <Navbar />
      <main className="container mx-auto max-w-3xl px-4 pt-28 pb-16">
        <h1 className="text-3xl font-black tracking-tight text-black">Billing</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Manage your subscription and payment details.
        </p>

        <div className="mt-8 rounded-2xl border border-border bg-white p-6 shadow-card">
          {loading ? (
            <div className="text-sm text-muted-foreground">Loading billing status…</div>
          ) : (
            <>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="text-sm text-muted-foreground">Plan status</div>
                  <div className="text-xl font-bold text-black">{isPro ? 'Pro' : 'Free'}</div>
                </div>
                <div className="text-sm text-muted-foreground">
                  Stripe status: <span className="font-medium text-black">{statusLabel}</span>
                </div>
              </div>

              {subscription?.current_period_end && (
                <div className="mt-3 text-sm text-muted-foreground">
                  Current period ends:{' '}
                  <span className="font-medium text-black">
                    {new Date(subscription.current_period_end).toLocaleDateString()}
                  </span>
                </div>
              )}

              {isCanceling && (
                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                  Cancellation scheduled. You keep Pro access until {cancellationLabel}. Go to{' '}
                  <Link href="/dashboard/settings?tab=billing" className="font-semibold underline">
                    Billing settings
                  </Link>{' '}
                  to confirm your status.
                </div>
              )}

              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <button
                  onClick={handleOpenPortal}
                  disabled={portalLoading || !payload?.customer_id}
                  className="inline-flex items-center justify-center rounded-xl bg-black px-5 py-3 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {portalLoading ? 'Opening…' : 'Open Billing Portal'}
                </button>

                {!isPro && (
                  <button
                    onClick={handleUpgrade}
                    disabled={checkoutLoading}
                    className="inline-flex items-center justify-center rounded-xl bg-[#0015ff] px-5 py-3 text-sm font-semibold text-white disabled:opacity-50"
                  >
                    {checkoutLoading ? 'Redirecting…' : 'Upgrade to Pro'}
                  </button>
                )}
              </div>

              {error && (
                <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {error}
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}
