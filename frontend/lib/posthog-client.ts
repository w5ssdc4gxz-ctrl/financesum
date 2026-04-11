'use client'

import posthog from 'posthog-js'

const POSTHOG_KEY = (process.env.NEXT_PUBLIC_POSTHOG_KEY ?? '').trim()

const isClient = () => typeof window !== 'undefined'

export const isPostHogEnabled = (): boolean => isClient() && POSTHOG_KEY.length > 0

export const trackPostHogEvent = (
  eventName: string,
  properties?: Record<string, string | number | boolean | null | undefined>
): void => {
  if (!isPostHogEnabled() || !eventName) return
  posthog.capture(eventName, properties ?? {})
}

export const identifyPostHogUser = (
  userId: string,
  properties?: Record<string, string | number | boolean | null | undefined>
): void => {
  if (!isPostHogEnabled() || !userId) return
  posthog.identify(userId, properties ?? {})
}

export const resetPostHogUser = (): void => {
  if (!isPostHogEnabled()) return
  posthog.reset()
}
