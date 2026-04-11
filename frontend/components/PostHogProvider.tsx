'use client'

import { useEffect } from 'react'
import posthog from 'posthog-js'
import { PostHogProvider } from 'posthog-js/react'

const POSTHOG_KEY = (process.env.NEXT_PUBLIC_POSTHOG_KEY ?? '').trim()
const POSTHOG_HOST = (process.env.NEXT_PUBLIC_POSTHOG_HOST ?? 'https://eu.i.posthog.com').trim()
const POSTHOG_DEBUG = (process.env.NEXT_PUBLIC_POSTHOG_DEBUG ?? '').trim().toLowerCase() === 'true'

let hasInitializedPostHog = false

const isConfigured = POSTHOG_KEY.length > 0

export default function PostHogClientProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    if (!isConfigured || hasInitializedPostHog) return

    posthog.init(POSTHOG_KEY, {
      api_host: POSTHOG_HOST,
      person_profiles: 'identified_only',
      capture_pageview: true,
      capture_pageleave: true,
      autocapture: true,
      loaded: (instance) => {
        if (POSTHOG_DEBUG) {
          instance.debug(true)
        }
      },
    })

    hasInitializedPostHog = true
  }, [])

  if (!isConfigured) return <>{children}</>

  return <PostHogProvider client={posthog}>{children}</PostHogProvider>
}
