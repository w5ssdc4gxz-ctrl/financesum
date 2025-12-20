'use client'

import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { Check, Sparkles, Zap, Building2 } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { billingApi } from '@/lib/api-client'

const UPGRADE_INTENT_KEY = 'financesum.intent.checkout.plan'

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
  const [isHovered, setIsHovered] = useState(false)

  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)

  const springConfig = { damping: 25, stiffness: 300 }
  const xSpring = useSpring(mouseX, springConfig)
  const ySpring = useSpring(mouseY, springConfig)

  const rotateX = useTransform(ySpring, [-0.5, 0.5], [8, -8])
  const rotateY = useTransform(xSpring, [-0.5, 0.5], [-8, 8])

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    mouseX.set((e.clientX - centerX) / rect.width)
    mouseY.set((e.clientY - centerY) / rect.height)
  }

  const handleMouseLeave = () => {
    setIsHovered(false)
    mouseX.set(0)
    mouseY.set(0)
  }

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.7, delay, ease: [0.25, 0.46, 0.45, 0.94] }}
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={handleMouseLeave}
      style={{
        perspective: 1000,
      }}
      className="relative"
    >
      <motion.div
        style={{
          rotateX: isHovered ? rotateX : 0,
          rotateY: isHovered ? rotateY : 0,
          transformStyle: 'preserve-3d',
        }}
        animate={{
          scale: isHovered ? 1.02 : 1,
        }}
        transition={{ duration: 0.2 }}
        className={`relative h-full rounded-3xl p-8 ${
          highlighted
            ? 'bg-gradient-to-br from-[#0015ff] via-[#4338ca] to-[#7c3aed] text-white shadow-2xl'
            : 'bg-white border border-border shadow-card'
        }`}
      >
        {/* Animated gradient border for highlighted card */}
        {highlighted && (
          <div className="absolute inset-0 rounded-3xl overflow-hidden pointer-events-none" aria-hidden="true">
            <motion.div
              className="absolute inset-0 opacity-30"
              animate={{
                background: [
                  'radial-gradient(circle at 0% 0%, rgba(255,255,255,0.3) 0%, transparent 50%)',
                  'radial-gradient(circle at 100% 0%, rgba(255,255,255,0.3) 0%, transparent 50%)',
                  'radial-gradient(circle at 100% 100%, rgba(255,255,255,0.3) 0%, transparent 50%)',
                  'radial-gradient(circle at 0% 100%, rgba(255,255,255,0.3) 0%, transparent 50%)',
                  'radial-gradient(circle at 0% 0%, rgba(255,255,255,0.3) 0%, transparent 50%)',
                ],
              }}
              transition={{ duration: 8, repeat: Infinity, ease: 'linear' }}
            />
          </div>
        )}

        {/* Badge */}
        {badge && (
          <motion.div
            initial={{ opacity: 0, scale: 0.8, y: -10 }}
            whileInView={{ opacity: 1, scale: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: delay + 0.2 }}
            className="absolute -top-4 left-1/2 -translate-x-1/2"
          >
            <span className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-gradient-to-r from-amber-400 to-orange-500 text-white text-sm font-semibold shadow-lg">
              <Sparkles className="w-3.5 h-3.5" />
              {badge}
            </span>
          </motion.div>
        )}

        {/* Content */}
        <div className="relative z-10">
          {/* Icon */}
          <motion.div
            initial={{ scale: 0 }}
            whileInView={{ scale: 1 }}
            viewport={{ once: true }}
            transition={{
              duration: 0.5,
              delay: delay + 0.1,
              type: 'spring',
              stiffness: 200,
            }}
            className={`inline-flex items-center justify-center w-14 h-14 rounded-2xl mb-6 ${
              highlighted
                ? 'bg-white/20 text-white'
                : 'bg-secondary text-[#0015ff]'
            }`}
          >
            {icon}
          </motion.div>

          {/* Title */}
          <h3 className={`text-xl font-bold mb-2 ${
            highlighted ? 'text-white' : 'text-foreground'
          }`}>
            {title}
          </h3>

          {/* Price */}
          <div className="mb-4">
            <span className={`text-5xl font-bold tracking-tight ${
              highlighted ? 'text-white' : 'text-foreground'
            }`}>
              {price}
            </span>
            <span className={`text-lg ml-1 ${
              highlighted ? 'text-white/70' : 'text-muted-foreground'
            }`}>
              {period}
            </span>
          </div>

          {/* Description */}
          <p className={`text-base mb-8 ${
            highlighted ? 'text-white/80' : 'text-muted-foreground'
          }`}>
            {description}
          </p>

          {/* Features */}
          <ul className="space-y-4 mb-8">
            {features.map((feature, index) => (
              <motion.li
                key={feature}
                initial={{ opacity: 0, x: -20 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.4, delay: delay + 0.3 + index * 0.1 }}
                className="flex items-start gap-3"
              >
                <div className={`flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center mt-0.5 ${
                  highlighted
                    ? 'bg-white/20 text-white'
                    : 'bg-[#0015ff]/10 text-[#0015ff]'
                }`}>
                  <Check className="w-3 h-3" strokeWidth={3} />
                </div>
                <span className={highlighted ? 'text-white/90' : 'text-muted-foreground'}>
                  {feature}
                </span>
              </motion.li>
            ))}
          </ul>

          {/* CTA Button */}
          <motion.button
            onClick={onCta}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            className={`w-full py-4 px-6 rounded-xl font-semibold text-base transition-all duration-200 ${
              highlighted
                ? 'bg-white text-[#0015ff] hover:bg-white/90 shadow-lg'
                : 'bg-[#0015ff] text-white hover:bg-[#0012cc] shadow-md hover:shadow-lg'
            }`}
          >
            {ctaText}
          </motion.button>
        </div>

        {/* Subtle shine effect on hover */}
        <motion.div
          className="absolute inset-0 rounded-3xl pointer-events-none"
          animate={{
            opacity: isHovered ? 1 : 0,
          }}
          transition={{ duration: 0.3 }}
          style={{
            background: highlighted
              ? 'linear-gradient(135deg, rgba(255,255,255,0.1) 0%, transparent 50%, transparent 100%)'
              : 'linear-gradient(135deg, rgba(0,21,255,0.03) 0%, transparent 50%, transparent 100%)',
          }}
        />
      </motion.div>
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
      if (!url) throw new Error('Missing checkout URL')
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
        '1,000 summaries per month',
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
    <section className="py-24 md:py-32 bg-secondary/30 overflow-hidden">
      <div className="container-wide">
        {/* Section Header */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
          className="text-center mb-16"
        >
          <span className="text-sm font-medium text-[#0015ff] uppercase tracking-wider mb-4 block">
            Simple Pricing
          </span>
          <h2 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight text-foreground mb-6 font-serif italic">
            Choose your plan
          </h2>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            Start free and scale as you grow. No hidden fees, cancel anytime.
          </p>
        </motion.div>

        {/* Pricing Cards */}
        <div className="grid md:grid-cols-3 gap-8 max-w-6xl mx-auto">
          {plans.map((plan, index) => (
            <PricingCard
              key={plan.title}
              {...plan}
              delay={index * 0.15}
            />
          ))}
        </div>

        {/* Trust badges */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.5 }}
          className="flex flex-wrap items-center justify-center gap-8 mt-16 text-muted-foreground"
        >
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            <span className="text-sm font-medium">SSL Secured</span>
          </div>
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
            <span className="text-sm font-medium">SOC 2 Compliant</span>
          </div>
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
            </svg>
            <span className="text-sm font-medium">Cancel Anytime</span>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
