'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { isSupabaseConfigured, supabase } from '@/lib/supabase'
import { billingApi } from '@/lib/api-client'

const UPGRADE_INTENT_KEY = 'financesum.intent.checkout.plan'
const CHECKOUT_SESSION_STORAGE_KEY = 'financesum.checkout.session_id'

export default function AuthCallback() {
  const router = useRouter()

  useEffect(() => {
    const handleCallback = async () => {
      if (!isSupabaseConfigured) {
        router.push('/signin')
        return
      }

      const code =
        typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('code') : null

      if (code) {
        const { error } = await supabase.auth.exchangeCodeForSession(code)
        if (error) {
          console.error('Supabase auth code exchange failed', error)
        } else if (typeof window !== 'undefined') {
          const url = new URL(window.location.href)
          url.searchParams.delete('code')
          window.history.replaceState(window.history.state, '', url.toString())
        }
      }

      // Supabase will parse the auth redirect URL automatically, but we still
      // refresh once to ensure we have a valid access token before calling our backend.
      const {
        data: { session: initialSession },
      } = await supabase.auth.getSession()

      const {
        data: { session: refreshedSession },
      } = initialSession ? await supabase.auth.refreshSession() : { data: { session: null } }

      const session = refreshedSession ?? initialSession

      if (session) {
        const intent = typeof window !== 'undefined' ? window.localStorage.getItem(UPGRADE_INTENT_KEY) : null
        if (intent === 'pro') {
          try {
            const response = await billingApi.createCheckoutSession({ plan: 'pro' }, session.access_token)
            const url = response.data?.url as string | undefined
            const sessionId = response.data?.id as string | undefined
            if (!url) throw new Error('Missing checkout URL')
            if (sessionId && typeof window !== 'undefined') {
              window.localStorage.setItem(CHECKOUT_SESSION_STORAGE_KEY, sessionId)
            }
            window.localStorage.removeItem(UPGRADE_INTENT_KEY)
            window.location.href = url
            return
          } catch (error: any) {
            console.error('Unable to start checkout after sign-in', error)
            alert(
              error?.response?.data?.detail ||
                error?.message ||
                'Signed in successfully, but unable to start checkout. Please try again from Billing.'
            )
            router.push('/billing')
            return
          }
        }

        router.push('/dashboard')
      } else {
        router.push('/signin')
      }
    }

    handleCallback()
  }, [router])

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600 mx-auto mb-4"></div>
        <p className="text-gray-600">Completing sign in...</p>
      </div>
    </div>
  )
}









