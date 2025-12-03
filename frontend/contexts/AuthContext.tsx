'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { User, Session } from '@supabase/supabase-js'
import { supabase } from '@/lib/supabase'

interface AuthContextType {
  user: User | null
  session: Session | null
  loading: boolean
  signIn: () => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  session: null,
  loading: true,
  signIn: async () => {},
  signOut: async () => {},
})

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}

type AuthMode = 'demo' | 'supabase'
const AUTH_MODE = (process.env.NEXT_PUBLIC_AUTH_MODE as AuthMode | undefined) ?? 'supabase'
const ALLOW_DEMO_FALLBACK =
  (process.env.NEXT_PUBLIC_ALLOW_DEMO_FALLBACK ?? 'true').toLowerCase() !== 'false'
const DEMO_STORAGE_KEY = 'financesum.demo.user'
const DEMO_ACTIVE_KEY = 'financesum.demo.active'
const SUPABASE_URL = (process.env.NEXT_PUBLIC_SUPABASE_URL ?? '').replace(/\/$/, '')
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? ''

const createDemoUser = (): User => {
  const timestamp = new Date().toISOString()
  return {
    id: 'demo-user',
    aud: 'authenticated',
    role: 'authenticated',
    email: 'demo@financesum.com',
    email_confirmed_at: timestamp,
    phone: '',
    confirmation_sent_at: timestamp,
    confirmed_at: timestamp,
    last_sign_in_at: timestamp,
    app_metadata: { provider: 'demo', providers: ['demo'] },
    user_metadata: { full_name: 'Demo User' },
    identities: [],
    created_at: timestamp,
    updated_at: timestamp,
    factors: undefined,
    phone_confirmed_at: timestamp,
    raw_user_meta_data: {},
  } as User
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [demoMode, setDemoMode] = useState<boolean>(AUTH_MODE === 'demo')
  const [providerStatus, setProviderStatus] = useState<{ checked: boolean; enabled: boolean | null }>({
    checked: false,
    enabled: null,
  })
  const resolvedDemoMode = useMemo(() => demoMode, [demoMode])

  useEffect(() => {
    if (typeof window === 'undefined') return

    const active = window.localStorage.getItem(DEMO_ACTIVE_KEY)
    if (active === 'true') {
      setDemoMode(true)
    }
  }, [])
  const fetchProviderStatus = useCallback(async () => {
    if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
      setProviderStatus({ checked: true, enabled: null })
      return null
    }

    try {
      const response = await fetch(`${SUPABASE_URL}/auth/v1/settings`, {
        headers: {
          apikey: SUPABASE_ANON_KEY,
          Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        },
      })

      if (!response.ok) {
        throw new Error(`Failed to fetch Supabase auth settings: ${response.status}`)
      }

      const data = await response.json()
      const googleProvider = data?.external?.google
      const enabled =
        typeof googleProvider === 'boolean'
          ? googleProvider
          : typeof googleProvider?.enabled === 'boolean'
            ? googleProvider.enabled
            : null
      setProviderStatus({ checked: true, enabled })
      return enabled
    } catch (error) {
      console.warn('Unable to verify Supabase auth provider status', error)
      setProviderStatus({ checked: true, enabled: null })
      return null
    }
  }, [])

  useEffect(() => {
    if (resolvedDemoMode) return
    fetchProviderStatus()
  }, [resolvedDemoMode, fetchProviderStatus])

  useEffect(() => {
    if (resolvedDemoMode) {
      const syncDemoUserFromStorage = () => {
        if (typeof window === 'undefined') return
        const stored = window.localStorage.getItem(DEMO_STORAGE_KEY)
        if (stored) {
          try {
            setUser(JSON.parse(stored))
          } catch {
            window.localStorage.removeItem(DEMO_STORAGE_KEY)
            setUser(null)
          }
        } else {
          setUser(null)
        }
        setSession(null)
        setLoading(false)
      }

      syncDemoUserFromStorage()

      const handleStorage = (event: StorageEvent) => {
        if (event.key === DEMO_STORAGE_KEY || event.key === DEMO_ACTIVE_KEY) {
          syncDemoUserFromStorage()
        }
      }

      if (typeof window !== 'undefined') {
        window.addEventListener('storage', handleStorage)
        return () => window.removeEventListener('storage', handleStorage)
      }
      return
    }

    // Supabase auth flow
    let isMounted = true
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!isMounted) return
      setSession(session)
      setUser(session?.user ?? null)
      setLoading(false)
    })

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session)
      setUser(session?.user ?? null)
      setLoading(false)
    })

    return () => {
      isMounted = false
      subscription.unsubscribe()
    }
  }, [resolvedDemoMode])

  const startDemoSession = () => {
    const demoUser = createDemoUser()
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(DEMO_ACTIVE_KEY, 'true')
      window.localStorage.setItem(DEMO_STORAGE_KEY, JSON.stringify(demoUser))
    }
    setDemoMode(true)
    setUser(demoUser)
    setSession(null)
    setLoading(false)
    return demoUser
  }

  const signInHandler = async () => {
    if (resolvedDemoMode) {
      startDemoSession()
      return
    }

    let isProviderEnabled = providerStatus.enabled
    if (!providerStatus.checked) {
      isProviderEnabled = await fetchProviderStatus()
    }

    const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY)
    const providerDisabled = isProviderEnabled === false

    if (!supabaseConfigured || providerDisabled) {
      if (ALLOW_DEMO_FALLBACK) {
        startDemoSession()
        throw new Error(
          'Supabase Google sign-in is disabled or not configured. Started a local demo session instead. Enable the Google provider in Supabase Auth when you are ready for real logins.'
        )
      }

      throw new Error('Supabase Google sign-in is disabled. Enable the provider in Supabase Auth.')
    }

    const envSiteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? '').replace(/\/$/, '')
    const runtimeOrigin =
      typeof window !== 'undefined' ? window.location.origin.replace(/\/$/, '') : ''
    const redirectBase = envSiteUrl || runtimeOrigin

    if (!redirectBase) {
      throw new Error('Unable to determine redirect URL for authentication.')
    }

    const redirectTo = new URL('/auth/callback', redirectBase).toString()

    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo,
      },
    })

    if (error) {
      const message = error.message || 'Failed to start Google sign-in.'
      const lower = message.toLowerCase()
      const providerDisabled =
        lower.includes('provider is not enabled') || lower.includes('validation_failed')

      if (providerDisabled && ALLOW_DEMO_FALLBACK) {
        startDemoSession()
        throw new Error(
          'Supabase Google sign-in is not enabled. Started a local demo session instead. Enable the Google provider in Supabase Auth to use real logins.'
        )
      }

      throw new Error(message)
    }
  }

  const signOutHandler = async () => {
    if (resolvedDemoMode) {
      if (typeof window !== 'undefined') {
        window.localStorage.removeItem(DEMO_STORAGE_KEY)
        window.localStorage.removeItem(DEMO_ACTIVE_KEY)
      }
      setUser(null)
      setSession(null)
      if (AUTH_MODE !== 'demo') {
        setDemoMode(false)
      }
      return
    }

    await supabase.auth.signOut()
  }

  const value = {
    user,
    session,
    loading,
    signIn: signInHandler,
    signOut: signOutHandler,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}







