'use client'

import { useRef } from 'react'
import Link from 'next/link'
import Image from 'next/image'
import { useRouter } from 'next/navigation'
import { motion, useScroll, useTransform } from 'framer-motion'
import { useAuth } from '@/contexts/AuthContext'
import Navbar from '@/components/Navbar'
import MinimalFooter from '@/components/MinimalFooter'
import BottomPopup from '@/components/BottomPopup'
import { FAQChat } from '@/components/FAQChat'
import PricingSection from '@/components/PricingSection'
import { Float, TextRotator, StackingCards, StackingCardItem, LogoLoop } from '@/components/fancy'

// Rotating words for the hero
const rotatingWords = ['reimagined.', 'simplified.', 'automated.', 'transformed.']

// Enterprise customer logos
const enterpriseLogos = [
  { node: <span className="font-bold tracking-tight">BlackRock</span>, title: 'BlackRock' },
  { node: <span className="font-bold tracking-tight">JP Morgan</span>, title: 'JP Morgan' },
  { node: <span className="font-bold tracking-tight">Goldman Sachs</span>, title: 'Goldman Sachs' },
  { node: <span className="font-bold tracking-tight">Morgan Stanley</span>, title: 'Morgan Stanley' },
  { node: <span className="font-bold tracking-tight">Citadel</span>, title: 'Citadel' },
  { node: <span className="font-bold tracking-tight">Bridgewater</span>, title: 'Bridgewater' },
  { node: <span className="font-bold tracking-tight">Fidelity</span>, title: 'Fidelity' },
  { node: <span className="font-bold tracking-tight">Vanguard</span>, title: 'Vanguard' },
]

// Walkthrough card data
const walkthroughCards = [
  {
    step: 1,
    title: 'Customize Output',
    description: 'Pick your focus areas, tone, detail level, complexity, and target length to shape the brief exactly the way you want.',
    image: '/walkthrough/step-1.png',
    color: 'bg-[#0015ff]',
  },
  {
    step: 2,
    title: 'Health Analysis',
    description: 'Choose whether to include a detailed Financial Health Rating section in your brief.',
    image: '/walkthrough/step-3.png',
    color: 'bg-[#4338ca]',
  },
  {
    step: 3,
    title: 'Configure Health Score',
    description: 'If included, tune the framework, weighting, risk tolerance, and analysis depth for the health score.',
    image: '/walkthrough/step-2.png',
    color: 'bg-[#7c3aed]',
  },
  {
    step: 4,
    title: 'Select Investor Persona',
    description: 'Optionally apply an investor lens (e.g., Buffett, Lynch) so the brief is written in that style and decision framework.',
    image: '/walkthrough/step-4.png',
    color: 'bg-[#8b5cf6]',
  },
  {
    step: 5,
    title: 'Additional Instructions',
    description: 'Add any extra context or specific requests for the AI to incorporate into the brief.',
    image: '/walkthrough/step-5.png',
    color: 'bg-[#a855f7]',
  },
  {
    step: 6,
    title: 'Ready to Generate',
    description: 'Review your selections and click Complete to generate the brief.',
    image: '/walkthrough/step-6.png',
    color: 'bg-[#c084fc]',
  },
]

const IMAGE_BLUR_DATA_URL =
  'data:image/svg+xml;charset=utf-8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 20">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#f3f4f6"/>
          <stop offset="100%" stop-color="#e5e7eb"/>
        </linearGradient>
      </defs>
      <rect width="32" height="20" fill="url(#g)"/>
    </svg>`
  )

export default function Home() {
  const { user } = useAuth()
  const router = useRouter()
  const heroRef = useRef<HTMLDivElement>(null)
  const walkthroughRef = useRef<HTMLDivElement>(null)
  const walkthroughPanelHeight = "h-screen min-h-[720px]"

  const { scrollYProgress } = useScroll({
    target: heroRef,
    offset: ["start start", "end start"]
  })

  const heroOpacity = useTransform(scrollYProgress, [0, 0.5], [1, 0])
  const heroY = useTransform(scrollYProgress, [0, 0.5], [0, -50])

  return (
    <main className="relative min-h-screen bg-white">
      <Navbar />

      {/* Hero Section */}
      <section
        ref={heroRef}
        className="relative min-h-screen flex items-center justify-center pt-16 overflow-hidden"
      >
        {/* Floating Images - Behind Content */}
        <div className="absolute inset-0 pointer-events-none z-10">
          {/* Top Left */}
          <motion.div
            initial={{ opacity: 0, y: 60, scale: 0.8 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 1, delay: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="absolute top-[12%] left-[6%] sm:left-[8%] pointer-events-auto"
          >
            <Float rotationRange={[2, 2, 1]} amplitude={[3, 5, 0]} speed={0.3} timeOffset={0}>
              <div className="w-36 sm:w-52 md:w-64 rounded-2xl shadow-hero overflow-hidden bg-white">
                <Image
                  src="/hero/hero-1.png"
                  alt="Financial dashboard preview"
                  width={256}
                  height={180}
                  sizes="(min-width: 768px) 256px, (min-width: 640px) 208px, 144px"
                  placeholder="blur"
                  blurDataURL={IMAGE_BLUR_DATA_URL}
                  priority
                  className="w-full h-auto object-cover"
                />
              </div>
            </Float>
          </motion.div>

          {/* Top Right */}
          <motion.div
            initial={{ opacity: 0, y: 80, scale: 0.8 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 1, delay: 0.5, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="absolute top-[8%] right-[4%] sm:right-[6%] pointer-events-auto"
          >
            <Float rotationRange={[2, 3, 1]} amplitude={[4, 6, 0]} speed={0.25} timeOffset={2}>
              <div className="w-40 sm:w-56 md:w-72 rounded-2xl shadow-hero overflow-hidden bg-white">
                <Image
                  src="/hero/hero-2.png"
                  alt="Analysis interface"
                  width={288}
                  height={200}
                  sizes="(min-width: 768px) 288px, (min-width: 640px) 224px, 160px"
                  placeholder="blur"
                  blurDataURL={IMAGE_BLUR_DATA_URL}
                  priority
                  className="w-full h-auto object-cover"
                />
              </div>
            </Float>
          </motion.div>

          {/* Middle Left */}
          <motion.div
            initial={{ opacity: 0, y: 50, scale: 0.8 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 1, delay: 0.7, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="absolute top-[42%] left-[2%] sm:left-[4%] pointer-events-auto"
          >
            <Float rotationRange={[3, 3, 1]} amplitude={[4, 7, 0]} speed={0.28} timeOffset={4}>
              <div className="w-44 sm:w-60 md:w-80 rounded-2xl shadow-hero overflow-hidden bg-white">
                <Image
                  src="/hero/hero-3.png"
                  alt="Research memo"
                  width={320}
                  height={220}
                  sizes="(min-width: 768px) 320px, (min-width: 640px) 240px, 176px"
                  placeholder="blur"
                  blurDataURL={IMAGE_BLUR_DATA_URL}
                  loading="eager"
                  className="w-full h-auto object-cover"
                />
              </div>
            </Float>
          </motion.div>

          {/* Bottom Right */}
          <motion.div
            initial={{ opacity: 0, y: 70, scale: 0.8 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 1, delay: 0.9, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="absolute top-[58%] right-[2%] sm:right-[5%] pointer-events-auto"
          >
            <Float rotationRange={[2, 2, 1]} amplitude={[3, 5, 0]} speed={0.32} timeOffset={6}>
              <div className="w-48 sm:w-64 md:w-88 rounded-2xl shadow-hero overflow-hidden bg-white">
                <Image
                  src="/hero/hero-4.png"
                  alt="Summary generation"
                  width={352}
                  height={240}
                  sizes="(min-width: 768px) 352px, (min-width: 640px) 256px, 192px"
                  placeholder="blur"
                  blurDataURL={IMAGE_BLUR_DATA_URL}
                  loading="eager"
                  className="w-full h-auto object-cover"
                />
              </div>
            </Float>
          </motion.div>

          {/* Bottom Left (Desktop only) */}
          <motion.div
            initial={{ opacity: 0, y: 60, scale: 0.8 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 1, delay: 1.1, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="hidden lg:block absolute bottom-[8%] left-[8%] pointer-events-auto"
          >
            <Float rotationRange={[2, 2, 1]} amplitude={[3, 5, 0]} speed={0.35} timeOffset={8}>
              <div className="w-48 md:w-56 rounded-2xl shadow-hero overflow-hidden bg-white">
                <Image
                  src="/hero/hero-5.png"
                  alt="Key metrics"
                  width={224}
                  height={160}
                  sizes="(min-width: 1024px) 224px, 0px"
                  placeholder="blur"
                  blurDataURL={IMAGE_BLUR_DATA_URL}
                  className="w-full h-auto object-cover"
                />
              </div>
            </Float>
          </motion.div>
        </div>

        {/* Hero Content */}
        <motion.div
          className="relative z-20 container-tight text-center"
          style={{ opacity: heroOpacity, y: heroY }}
        >
          {/* Badge */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.1, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="mb-8"
          >
            <span className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-secondary text-sm font-medium text-muted-foreground">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#0015ff] opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[#0015ff]" />
              </span>
              Now in Public Beta
            </span>
          </motion.div>

          {/* Headline with Playfair Display */}
          <motion.h1
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.2, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="text-5xl sm:text-6xl md:text-7xl lg:text-8xl font-bold tracking-tight text-foreground mb-8"
          >
            <span className="font-serif italic">Financial analysis,</span>
            <br />
            <span className="text-[#0015ff] font-serif italic">
              <TextRotator 
                words={rotatingWords} 
                interval={3000} 
                animationType="blur"
              />
            </span>
          </motion.h1>

          {/* Subtitle */}
          <motion.p
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-12 text-balance leading-relaxed"
          >
            FinanceSum digests 10-Ks, earnings calls, and market news into 
            executive-grade memos. Stop drowning in filings.
          </motion.p>

          {/* CTA Buttons */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.6, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="flex flex-col sm:flex-row items-center justify-center gap-4 relative z-20"
          >
            <button
              onClick={() => router.push(user ? '/dashboard' : '/signup')}
              className="btn-primary text-base px-8 py-4 rounded-full"
            >
              Start Analyzing
            </button>
            <button
              onClick={() => walkthroughRef.current?.scrollIntoView({ behavior: 'smooth' })}
              className="btn-secondary text-base px-8 py-4 rounded-full"
            >
              See How It Works
            </button>
          </motion.div>
        </motion.div>

      </section>

      {/* Trusted By Section */}
      <section className="py-16 md:py-20 bg-white border-t border-secondary">
        <div className="container-wide">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="text-center mb-10"
          >
            <span className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Trusted by leading financial institutions
            </span>
          </motion.div>
          <motion.div
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8, delay: 0.2 }}
            className="h-12 overflow-hidden"
          >
            <LogoLoop
              logos={enterpriseLogos}
              speed={60}
              direction="left"
              logoHeight={24}
              gap={80}
              hoverSpeed={0}
              fadeOut
              fadeOutColor="#ffffff"
              ariaLabel="Trusted enterprise customers"
              className="text-muted-foreground/60"
            />
          </motion.div>
        </div>
      </section>

      {/* Walkthrough Section */}
      <section ref={walkthroughRef} className="py-16 md:py-20 bg-secondary/30 scroll-mt-24">
        <div className="container-wide max-w-[90rem]">
          {/* Section Header */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
            className="text-center mb-10"
          >
            <span className="text-sm font-medium text-[#0015ff] uppercase tracking-wider mb-4 block">
              How It Works
            </span>
            <h2 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight text-foreground mb-6 font-serif italic">
              Six steps to clarity
            </h2>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              From company search to actionable insights in minutes. Follow along as we transform complex filings into clear intelligence.
            </p>
          </motion.div>

          {/* Stacking Cards */}
          <StackingCards totalCards={walkthroughCards.length} scaleMultiplier={0.03} className="relative">
            {walkthroughCards.map((card, index) => (
              <StackingCardItem
                key={card.step}
                index={index}
                className={walkthroughPanelHeight}
                topPosition={`calc(4.5rem + 5vh + ${index * 3}vh)`}
              >
                <div className="flex items-start justify-center w-full h-full">
                  <div className={`${card.color} h-[80%] md:h-[78%] flex-col md:flex-row px-10 md:px-14 py-10 flex w-[96%] rounded-3xl mx-auto relative shadow-2xl`}>
                    <div className="flex flex-col md:flex-row gap-10 h-full items-center w-full">
                      {/* Content */}
                      <div className="flex-1 text-white flex flex-col justify-center">
                        <span className="inline-block px-3 py-1 bg-white/20 rounded-full text-sm font-medium mb-4">
                          Step {card.step}
                        </span>
                        <h3 className="text-3xl md:text-4xl font-bold mb-5 font-serif italic">
                          {card.title}
                        </h3>
                        <p className="text-white/80 text-lg md:text-xl leading-relaxed">
                          {card.description}
                        </p>
                      </div>

                      {/* Image */}
                      <div className="w-full md:w-1/2">
                        <div className="rounded-2xl overflow-hidden shadow-2xl bg-white aspect-video relative">
	                          <Image
	                            src={card.image}
	                            alt={card.title}
	                            fill
	                            sizes="(max-width: 768px) 100vw, 50vw"
	                            placeholder="blur"
	                            blurDataURL={IMAGE_BLUR_DATA_URL}
	                            className="object-cover"
	                          />
	                        </div>
	                      </div>
                    </div>
                  </div>
                </div>
              </StackingCardItem>
            ))}
            <div className="h-[45vh]" />
          </StackingCards>
        </div>
      </section>

      {/* FAQ Chat Section */}
      <FAQChat />

      {/* Pricing Section */}
      <PricingSection />

      {/* CTA Section */}
      <motion.section
        className="py-24 md:py-32 bg-white overflow-hidden"
        initial={{ opacity: 0 }}
        whileInView={{ opacity: 1 }}
        viewport={{ amount: 0.3 }}
        transition={{ duration: 0.6 }}
      >
        <div className="container-tight text-center">
          <motion.h2
            className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight text-foreground mb-6 font-serif italic"
            initial={{ opacity: 0, y: 50, scale: 0.95 }}
            whileInView={{ opacity: 1, y: 0, scale: 1 }}
            viewport={{ amount: 0.5 }}
            transition={{ duration: 0.7, ease: [0.25, 0.46, 0.45, 0.94] }}
          >
            Ready to start?
          </motion.h2>
          <motion.p
            className="text-lg text-muted-foreground max-w-xl mx-auto mb-10"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ amount: 0.5 }}
            transition={{ duration: 0.7, delay: 0.15, ease: [0.25, 0.46, 0.45, 0.94] }}
          >
            Join thousands of investors who save hours every week with AI-powered financial analysis.
          </motion.p>
          <motion.div
            initial={{ opacity: 0, y: 30, scale: 0.9 }}
            whileInView={{ opacity: 1, y: 0, scale: 1 }}
            viewport={{ amount: 0.5 }}
            transition={{
              duration: 0.5,
              delay: 0.3,
              ease: [0.25, 0.46, 0.45, 0.94],
              scale: { type: "spring", stiffness: 200, damping: 15 }
            }}
          >
            <Link
              href={user ? '/dashboard' : '/signup'}
              className="btn-primary text-lg px-10 py-5 rounded-full inline-flex"
            >
              Get Started Free
            </Link>
          </motion.div>
        </div>
      </motion.section>

      {/* Footer with reveal animation */}
      <motion.div
        initial={{ opacity: 0, y: 40 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ amount: 0.2 }}
        transition={{ duration: 0.8, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <MinimalFooter />
      </motion.div>

      {/* Scroll-triggered popup */}
      <BottomPopup threshold={80} />
    </main>
  )
}
