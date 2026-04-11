'use client'

import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { Check, Sparkles, Zap, Building2 } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { billingApi } from '@/lib/api-client'

const UPGRADE_INTENT_KEY = 'financesum.intent.checkout.plan'
const CHECKOUT_SESSION_STORAGE_KEY = 'financesum.checkout.session_id'

interface PricingCardProps {
  title: string
  price: string
  period: string
  description: string
  features: string[]
  highlighted?: boolean
  icon: React.ReactNode
  delay: number
  badge?: string
  ctaText: string
  onCta: () => void
}

function PricingCard({
  title,
  price,
  period,
  description,
  features,
  highlighted = false,
  icon,
  delay,
  badge,
  ctaText,
  onCta,
}: PricingCardProps) {
  const ref = useRef<HTMLDivElement>(null)

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "100px" }}
      transition={{ duration: 0.6, delay, ease: [0.25, 1, 0.5, 1] }}
      className={`relative h-full flex flex-col p-10 border transition-all duration-300 ${highlighted
        ? 'bg-black border-black text-white dark:bg-white dark:border-white dark:text-black shadow-lg'
        : 'bg-white border-zinc-200 text-black hover:border-zinc-300 dark:bg-zinc-950 dark:border-zinc-800 dark:text-white dark:hover:border-zinc-700 shadow-sm'
        }`}
    >
      {/* Badge */}
      {badge && (
        <div className="absolute -top-3 left-8 select-none z-20">
          <span className={`inline-flex items-center px-4 py-1.5 text-xs tracking-widest uppercase font-bold border ${highlighted
            ? 'bg-white border-black text-black dark:bg-black dark:border-white dark:text-white'
            : 'bg-black border-black text-white dark:bg-white dark:border-white dark:text-black'
            }`}>
            {badge}
          </span>
        </div>
      )}

      {/* Content */}
      <div className="relative z-10 flex-grow flex flex-col">
        {/* Header Section */}
        <div className="mb-10">
          <h3 className={`text-2xl font-bold tracking-tight mb-4 ${highlighted ? 'text-white dark:text-black' : 'text-black dark:text-white'
            }`}>
            {title}
          </h3>

          <div className="flex items-baseline">
            <span className={`text-5xl font-black tracking-tighter ${highlighted ? 'text-white dark:text-black' : 'text-black dark:text-white'
              }`}>
              {price}
            </span>
            <span className={`text-sm font-medium ml-2 ${highlighted ? 'text-zinc-400 dark:text-zinc-600' : 'text-zinc-500'
              }`}>
              {period}
            </span>
          </div>

          <p className={`mt-6 text-sm font-medium leading-relaxed ${highlighted ? 'text-zinc-300 dark:text-zinc-700' : 'text-zinc-600 dark:text-zinc-400'
            }`}>
            {description}
          </p>
        </div>

        {/* Divider */}
        <div className={`h-[1px] w-full mb-8 ${highlighted ? 'bg-white/20 dark:bg-black/20' : 'bg-black/10 dark:bg-white/10'
          }`} />

        {/* Features */}
        <ul className="space-y-5 mb-12 flex-grow">
          {features.map((feature, index) => (
            <motion.li
              key={feature}
              initial={{ opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: delay + 0.2 + index * 0.05 }}
              className="flex items-start gap-4"
            >
              <div className={`flex-shrink-0 mt-1 ${highlighted ? 'text-white dark:text-black' : 'text-black dark:text-white'
                }`}>
                <Check className="w-4 h-4" strokeWidth={3} />
              </div>
              <span className={`text-sm font-medium ${highlighted ? 'text-zinc-200 dark:text-zinc-800' : 'text-zinc-700 dark:text-zinc-300'
                }`}>
                {feature}
              </span>
            </motion.li>
          ))}
        </ul>

        {/* CTA Button */}
        <button
          onClick={onCta}
          className={`w-full py-4 px-6 text-sm font-bold tracking-widest uppercase transition-colors duration-200 border ${highlighted
            ? 'bg-white border-white text-black hover:bg-zinc-200 hover:border-zinc-200 dark:bg-black dark:border-black dark:text-white dark:hover:bg-zinc-800 dark:hover:border-zinc-800'
            : 'bg-black border-black text-white hover:bg-zinc-800 hover:border-zinc-800 dark:bg-white dark:border-white dark:text-black dark:hover:bg-zinc-200 dark:hover:border-zinc-200'
            }`}
        >
          {ctaText}
        </button>
      </div>
    </motion.div>


  )
}

export default function PricingSection() {
  const router = useRouter()
  const { user, session } = useAuth()
  const [checkoutLoading, setCheckoutLoading] = useState(false)
  const [isPro, setIsPro] = useState<boolean | null>(null)
  const [stripeConfigured, setStripeConfigured] = useState<boolean | null>(null)

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
    if (!user || !session) {
      setIsPro(null)
      return
    }
    if (stripeConfigured === null) {
      setIsPro(null)
      return
    }
    if (stripeConfigured === false) {
      setIsPro(false)
      return
    }

    let cancelled = false
    billingApi
      .getSubscription(session.access_token)
      .then((res) => {
        if (cancelled) return
        setIsPro(Boolean(res.data?.is_pro))
      })
      .catch(() => {
        if (cancelled) return
        setIsPro(null)
      })

    return () => {
      cancelled = true
    }
  }, [user, session, stripeConfigured])

  const handleGetStarted = () => {
    router.push(user ? '/dashboard' : '/signup')
  }

  const handleUpgradeToPro = async () => {
    const configured = stripeConfigured === true ? true : await refreshStripeConfig()
    if (!configured) {
      alert(
        "Billing isn't configured on the backend yet. Add STRIPE_SECRET_KEY to the repo-root `.env` (same folder as `start.py`), save it, then refresh (or restart the backend)."
      )
      return
    }
    if (!user || !session) {
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(UPGRADE_INTENT_KEY, 'pro')
      }
      router.push('/signup')
      return
    }

    if (isPro) {
      router.push('/billing')
      return
    }

    if (checkoutLoading) return

    setCheckoutLoading(true)
    try {
      const response = await billingApi.createCheckoutSession({ plan: 'pro' }, session.access_token)
      const url = response.data?.url as string | undefined
      const sessionId = response.data?.id as string | undefined
      if (!url) throw new Error('Missing checkout URL')
      if (sessionId && typeof window !== 'undefined') {
        window.localStorage.setItem(CHECKOUT_SESSION_STORAGE_KEY, sessionId)
      }
      window.location.href = url
    } catch (error) {
      console.error('Unable to start Stripe Checkout', error)
      const message =
        (error as any)?.response?.data?.detail ||
        (error as any)?.message ||
        'Unable to start checkout right now. Please try again.'
      alert(message)
    } finally {
      setCheckoutLoading(false)
    }
  }

  const handleContactSales = () => {
    window.location.href = 'mailto:enterprise@financesum.com?subject=Enterprise%20Inquiry'
  }

  const plans = [
    {
      title: 'Free',
      price: '$0',
      period: '/month',
      description: 'Perfect for trying out FinanceSum',
      features: [
        '1 summary free trial',
        'Basic financial analysis',
        'Standard export formats',
        'Email support',
      ],
      icon: <Zap className="w-6 h-6" />,
      ctaText: 'Get Started Free',
      onCta: handleGetStarted,
    },
    {
      title: 'Pro',
      price: '$20',
      period: '/month',
      description: 'For serious investors and analysts',
      features: [
        '100 summaries per month',
        'Advanced AI analysis',
        'Financial health scores',
        'Investor persona insights',
        'Priority support',
        'All export formats',
      ],
      highlighted: true,
      badge: 'Most Popular',
      icon: <Sparkles className="w-6 h-6" />,
      ctaText: isPro ? 'Manage billing' : checkoutLoading ? 'Redirecting…' : 'Upgrade to Pro',
      onCta: handleUpgradeToPro,
    },
    {
      title: 'Enterprise',
      price: 'Custom',
      period: '',
      description: 'For teams and organizations',
      features: [
        'Unlimited summaries',
        'Custom AI models',
        'API access',
        'Team collaboration',
        'Dedicated support',
        'Custom integrations',
      ],
      icon: <Building2 className="w-6 h-6" />,
      ctaText: 'Contact Sales',
      onCta: handleContactSales,
    },
  ]

  return (
    <section className="py-24 md:py-32 bg-white dark:bg-zinc-950 border-t border-zinc-200 dark:border-zinc-800 overflow-hidden">
      <div className="container-wide">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "100px" }}
          transition={{ duration: 0.8, ease: [0.25, 1, 0.5, 1] }}
          className="text-center mb-20"
        >
          <span className="text-xs font-bold tracking-widest text-zinc-400 uppercase mb-4 block">
            Pricing Options
          </span>
          <h2 className="text-5xl md:text-7xl font-black tracking-tighter text-black dark:text-white mb-6 uppercase">
            Invest in clarity
          </h2>
          <p className="text-lg text-zinc-500 font-medium max-w-xl mx-auto">
            Transparent plans designed for independent analysts and enterprise teams. Secure, simple, scalable.
          </p>
        </motion.div>

        {/* Pricing Cards Grid */}
        <div className="grid md:grid-cols-3 gap-0 max-w-6xl mx-auto border-t border-l border-zinc-200 dark:border-zinc-800">
          {plans.map((plan, index) => (
            <div key={plan.title} className="border-r border-b border-zinc-200 dark:border-zinc-800 relative group">
              <PricingCard
                {...plan}
                delay={index * 0.15}
              />
            </div>
          ))}
        </div>

        {/* Trust badges - Flat Minimalist Style */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "100px" }}
          transition={{ duration: 0.6, delay: 0.4 }}
          className="flex flex-wrap items-center justify-center gap-12 mt-20"
        >
          <div className="flex items-center gap-3 grayscale opacity-60">
            <svg className="w-4 h-4 text-black dark:text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            <span className="text-xs font-bold tracking-widest uppercase text-black dark:text-white">SSL Secured</span>
          </div>
          <div className="flex items-center gap-3 grayscale opacity-60">
            <svg className="w-4 h-4 text-black dark:text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
            <span className="text-xs font-bold tracking-widest uppercase text-black dark:text-white">SOC 2 Compliant</span>
          </div>
          <div className="flex items-center gap-3 grayscale opacity-60">
            <svg className="w-4 h-4 text-black dark:text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
            </svg>
            <span className="text-xs font-bold tracking-widest uppercase text-black dark:text-white">Cancel Anytime</span>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
