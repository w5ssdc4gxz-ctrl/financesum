'use client'

import Link from 'next/link'
import { motion } from 'framer-motion'
import { FaUsers, FaChartLine, FaRobot } from 'react-icons/fa'
import Navbar from '@/components/Navbar'
import AnimatedBackground from '@/components/AnimatedBackground'
import LogoLoop from '@/components/LogoLoop'
import ResearchMemoShowcase from '@/components/ResearchMemoShowcase'
import JourneySection from '@/components/JourneySection'
import ColourfulText from '@/components/ui/colourful-text'
import { useAuth } from '@/contexts/AuthContext'
import { fadeInUp, zoomIn, staggerContainer } from '@/lib/animations'

export default function Home() {
  const { user } = useAuth()

  return (
    <div className="min-h-screen bg-gradient-to-br from-dark-900 via-primary-900 to-dark-900">
      <AnimatedBackground />
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Hero Section */}
        <motion.div 
          className="text-center py-20 md:py-32"
          initial="initial"
          animate="animate"
          variants={staggerContainer}
        >
          <motion.div className="mb-8" variants={zoomIn}>
            <span className="inline-block px-6 py-3 rounded-full bg-primary-500/10 border border-primary-500/20 text-primary-300 text-sm font-semibold backdrop-blur-sm shadow-glow">
              ✨ AI-Powered Financial Intelligence for Modern Investors
            </span>
          </motion.div>

          <motion.h1 
            className="hero-title text-6xl md:text-8xl font-black mb-8"
            variants={fadeInUp}
          >
            <span className="text-white block">Analyze Like</span>
            <ColourfulText text="The Legends" className="hero-highlight mt-2 block text-shadow-glow" />
          </motion.h1>

          <motion.p 
            className="text-2xl md:text-3xl text-gray-200 mb-16 max-w-4xl mx-auto leading-relaxed font-light"
            variants={fadeInUp}
          >
            Transform SEC filings into actionable insights with <span className="font-bold text-primary-300">AI-powered analysis</span> through the lens of legendary investors like Warren Buffett and Cathie Wood.
          </motion.p>

          <motion.div 
            className="flex flex-col sm:flex-row justify-center gap-6 mb-12"
            variants={fadeInUp}
          >
            <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
              <Link
                href="/dashboard"
                className="btn-premium text-xl px-12 py-5 shadow-glow-lg"
              >
                {user ? 'Go to Dashboard →' : 'Start Free Trial →'}
              </Link>
            </motion.div>
            <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
              <Link
                href="#journey"
                className="px-12 py-5 rounded-xl font-semibold text-white border-2 border-white/30 hover:border-primary-400 transition-all duration-300 hover:bg-white/5 backdrop-blur-sm text-xl"
              >
                See How It Works
              </Link>
            </motion.div>
          </motion.div>

          <motion.div 
            className="flex flex-wrap items-center justify-center gap-8 text-gray-300 text-base"
            variants={fadeInUp}
          >
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-green-400" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span className="font-medium">No credit card required</span>
            </div>
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-green-400" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span className="font-medium">Instant access</span>
            </div>
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-green-400" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span className="font-medium">10+ investor personas</span>
            </div>
          </motion.div>
        </motion.div>

        {/* Trusted By Section */}
        <motion.div 
          className="py-16"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
        >
          <div className="text-center mb-12">
            <h3 className="text-xl text-gray-400 font-medium mb-8">Trusted by investors at leading firms</h3>
          </div>
          <div className="w-full" style={{ height: '100px' }}>
            <LogoLoop
              logos={[
                'BlackRock', 'Vanguard', 'Fidelity', 'Goldman Sachs', 
                'J.P. Morgan', 'Morgan Stanley', 'Citadel', 'Bridgewater'
              ].map(name => ({
                node: <div className="text-4xl font-bold text-white/60">{name}</div>,
                title: name
              }))}
              speed={40}
              direction="left"
              logoHeight={48}
              gap={80}
              pauseOnHover={true}
              scaleOnHover={false}
              fadeOut={true}
            />
          </div>
        </motion.div>

        {/* Immersive Demo Section */}
        <section className="py-24">
          <div className="grid gap-12 lg:grid-cols-[0.95fr,1.15fr] items-center">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-100px' }}
              transition={{ duration: 0.6 }}
              className="space-y-6"
            >
              <p className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-white/10 bg-white/5 text-sm text-primary-200 font-semibold shadow-glow">
                <span className="w-2 h-2 rounded-full bg-primary-300 animate-pulse" />
                Live Product Preview
              </p>
              <h2 className="text-4xl md:text-5xl font-black text-white leading-tight">
                Research Memos that feel <span className="gradient-text">alive</span>.
              </h2>
              <p className="text-lg text-gray-300 leading-relaxed">
                Hover through our interactive memo card to see how FinanceSum blends AI narratives, investor-ready KPIs, and
                risk callouts inside a cinematic 3D workspace.
              </p>
              <ul className="space-y-4">
                {[
                  'Layered 3D surface keeps executive summary, metrics, and analyst bias in one view.',
                  'Sections mirror real equity research structure—Executive Summary, KPIs, Risks, Initiatives.',
                  'Designed for presentations: neon accents, glass morphism, and subtle depth cues.',
                ].map((item) => (
                  <li key={item} className="flex items-start gap-3 text-gray-200">
                    <svg className="w-6 h-6 text-primary-300 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                      <path
                        fillRule="evenodd"
                        d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.707a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                        clipRule="evenodd"
                      />
                    </svg>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap gap-4 pt-2">
                <Link href="/dashboard" className="btn-premium text-lg px-10 py-4 shadow-glow-lg">
                  Launch the Memo Experience →
                </Link>
                <Link
                  href="#journey"
                  className="px-10 py-4 rounded-xl font-semibold text-white border-2 border-white/30 hover:border-primary-400 transition-all duration-300 hover:bg-white/5 backdrop-blur-sm text-lg"
                >
                  Explore all features
                </Link>
              </div>
            </motion.div>

            <ResearchMemoShowcase />
          </div>
        </section>

        <section id="journey" className="py-28 bg-gradient-to-b from-transparent via-dark-900/40 to-dark-800/60">
          <JourneySection />
        </section>

        {/* How It Works Section */}
        <div className="py-24 bg-dark-900">
          <motion.div 
            className="text-center mb-20"
            initial={{ opacity: 0, y: 40 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-100px" }}
            transition={{ duration: 0.6 }}
          >
            <h2 className="text-5xl md:text-6xl font-black text-white mb-6">
              Get Started in <span className="gradient-text">Seconds</span>
            </h2>
            <p className="text-gray-300 text-xl">Simple, fast, and powerful - start analyzing in 4 easy steps</p>
          </motion.div>
          
          <motion.div 
            className="max-w-5xl mx-auto space-y-8"
            initial="initial"
            whileInView="animate"
            viewport={{ once: true, margin: "-100px" }}
            variants={staggerContainer}
          >
            {[
              {
                num: '1',
                title: 'Search for a Company',
                desc: 'Enter a ticker symbol, company name, or CIK to instantly fetch SEC filings and comprehensive financial data',
                color: 'from-blue-600 to-cyan-600'
              },
              {
                num: '2',
                title: 'Select Filings',
                desc: 'Choose which quarterly or annual reports to analyze with our intuitive, beautiful filing selector',
                color: 'from-purple-600 to-pink-600'
              },
              {
                num: '3',
                title: 'Get AI Analysis',
                desc: 'Our advanced AI extracts data, calculates comprehensive ratios, and generates professional investment analysis instantly',
                color: 'from-primary-600 to-accent-600'
              },
              {
                num: '4',
                title: 'Explore Investor Views',
                desc: 'See simulated perspectives from 10 legendary investors including Warren Buffett, Cathie Wood, and Ray Dalio',
                color: 'from-green-600 to-emerald-600'
              }
            ].map((step, index) => (
              <motion.div 
                key={index}
                className="flex items-start gap-8 p-10 rounded-3xl bg-gradient-to-r from-dark-800/50 to-dark-800/30 border border-primary-500/20 hover:border-primary-500/50 transition-all backdrop-blur-sm group"
                variants={fadeInUp}
                whileHover={{ x: 15, borderColor: 'rgba(168, 85, 247, 0.6)', boxShadow: '0 10px 40px rgba(168, 85, 247, 0.2)' }}
              >
                <div className={`flex-shrink-0 w-20 h-20 bg-gradient-to-br ${step.color} text-white rounded-3xl flex items-center justify-center font-black text-3xl shadow-glow group-hover:scale-110 transition-transform`}>
                  {step.num}
                </div>
                <div>
                  <h3 className="text-3xl font-bold mb-3 text-white group-hover:text-primary-300 transition-colors">{step.title}</h3>
                  <p className="text-gray-300 text-xl leading-relaxed">
                    {step.desc}
                  </p>
                </div>
              </motion.div>
            ))}
          </motion.div>
        </div>

        {/* Stats Section */}
        <motion.div 
          className="py-24"
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8 }}
        >
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {[
              { value: '10+', label: 'Investor Personas', icon: FaUsers },
              { value: '50K+', label: 'Companies Analyzed', icon: FaChartLine },
              { value: '99.9%', label: 'Accuracy Rate', icon: FaRobot }
            ].map((stat, index) => (
              <motion.div
                key={index}
                className="text-center p-10 rounded-3xl bg-gradient-to-br from-primary-900/30 to-accent-900/30 border border-primary-500/30 backdrop-blur-sm"
                initial={{ opacity: 0, scale: 0.8 }}
                whileInView={{ opacity: 1, scale: 1 }}
                viewport={{ once: true }}
                transition={{ delay: index * 0.2, duration: 0.6 }}
                whileHover={{ scale: 1.05, borderColor: 'rgba(168, 85, 247, 0.6)' }}
              >
                <stat.icon className="text-5xl text-primary-400 mx-auto mb-4" />
                <div className="text-6xl font-black gradient-text mb-2">{stat.value}</div>
                <div className="text-gray-300 text-xl font-medium">{stat.label}</div>
              </motion.div>
            ))}
          </div>
        </motion.div>

        {/* Disclaimer */}
        <div className="py-20 bg-dark-800/50">
          <motion.div 
            className="max-w-5xl mx-auto"
            initial={{ opacity: 0, scale: 0.95 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
          >
            <div className="bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border-2 border-yellow-500/30 rounded-3xl p-10 backdrop-blur-sm">
              <div className="flex items-start gap-6">
                <div className="flex-shrink-0 w-16 h-16 bg-yellow-500/20 rounded-2xl flex items-center justify-center">
                  <svg className="w-8 h-8 text-yellow-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-2xl font-bold text-yellow-300 mb-4">Important Disclaimer</h3>
                  <p className="text-gray-200 leading-relaxed text-lg">
                    All investor persona outputs are simulations based on publicly available writings and investment philosophies. 
                    They do not represent actual advice from these investors or constitute financial advice. 
                    Always conduct your own research and consult with financial professionals before making investment decisions.
                  </p>
                </div>
              </div>
            </div>
          </motion.div>
        </div>
        
        {/* CTA Section */}
        <div className="py-32 bg-gradient-to-br from-primary-900 via-accent-900 to-primary-900 rounded-3xl my-20">
          <motion.div 
            className="text-center max-w-5xl mx-auto px-8"
            initial={{ opacity: 0, scale: 0.9 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
          >
            <motion.h2 
              className="text-5xl md:text-7xl font-black text-white mb-8"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.2 }}
            >
              Ready to Invest Like a Legend?
            </motion.h2>
            <motion.p 
              className="text-2xl text-gray-200 mb-12"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.3 }}
            >
              Join thousands of investors making smarter decisions with AI-powered financial analysis
            </motion.p>
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: 0.4 }}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              <Link
                href="/dashboard"
                className="btn-premium text-2xl inline-block px-16 py-6 shadow-glow-lg"
              >
                Start Analyzing Now →
              </Link>
            </motion.div>
          </motion.div>
        </div>
      </main>
    </div>
  )
}
