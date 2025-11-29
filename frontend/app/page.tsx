'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import { useAuth } from '@/contexts/AuthContext'
import Navbar from '@/components/Navbar'
import LayeredScrollBackground from '@/components/LayeredScrollBackground'
import LogoLoop from '@/components/LogoLoop'
import JourneySection from '@/components/JourneySection'
import ResearchMemoShowcase from '@/components/ResearchMemoShowcase'
import PersonaSelector from '@/components/PersonaSelector'
import MegaFooter from '@/components/MegaFooter'
import { Button } from '@/components/base/buttons/button'
import CubeBackground from '@/components/CubeBackground'
import Stack from '@/components/Stack'


export default function Home() {
    const { user, signIn } = useAuth()
    const router = useRouter()
    const logos = [
        { node: <div className="text-xl font-bold text-white">BlackRock</div> },
        { node: <div className="text-xl font-bold text-white">Goldman Sachs</div> },
        { node: <div className="flex items-center gap-3 text-xl font-bold text-white"><img src="/api/logo?ticker=JPM" alt="JPMorgan" className="h-8 w-auto object-contain brightness-0 invert" /> JPMorgan</div> },
        { node: <div className="text-xl font-bold text-white">Berkshire Hathaway</div> },
        { node: <div className="text-xl font-bold text-white">Morgan Stanley</div> },
        { node: <div className="text-xl font-bold text-white">Vanguard</div> },
    ]

    return (
        <main className="relative min-h-screen w-full overflow-x-hidden bg-[#050015]">
            <LayeredScrollBackground />
            <Navbar />

            {/* Hero Section */}
            <section className="relative z-10 flex min-h-screen flex-col items-center justify-center px-4 pt-20 pb-16 text-center sm:px-6 lg:px-8">
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8, ease: "easeOut" }}
                    className="max-w-5xl space-y-8"
                >
                    <div className="inline-flex items-center rounded-full border border-primary-500/30 bg-primary-500/10 px-3 py-1 text-sm font-medium text-primary-200 backdrop-blur-sm">
                        <span className="mr-2 flex h-2 w-2">
                            <span className="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-primary-400 opacity-75"></span>
                            <span className="relative inline-flex h-2 w-2 rounded-full bg-primary-500"></span>
                        </span>
                        Now in Public Beta
                    </div>

                    <h1 className="text-5xl font-black tracking-tight text-white sm:text-7xl lg:text-8xl">
                        Financial analysis, <br />
                        <span className="bg-gradient-to-r from-primary-400 via-accent-400 to-primary-400 bg-clip-text text-transparent bg-[length:200%_auto] animate-gradient">
                            reimagined by AI.
                        </span>
                    </h1>

                    <p className="mx-auto max-w-2xl text-lg text-gray-300 sm:text-xl">
                        FinanceSum digests 10-Ks, earnings calls, and market news into
                        executive-grade memos. Stop drowning in filings and start making decisions.
                    </p>

                    <div className="flex flex-col items-center justify-center gap-4 sm:flex-row">
                        <Button
                            size="lg"
                            className="min-w-[200px] text-lg h-14 rounded-2xl"
                            onClick={() => {
                                if (user) {
                                    router.push('/dashboard')
                                } else {
                                    router.push('/signup')
                                }
                            }}
                        >
                            Start Analyzing
                        </Button>
                        <Button
                            color="secondary"
                            size="lg"
                            className="min-w-[200px] text-lg h-14 rounded-2xl border-white/20 text-white hover:bg-white/10 hover:text-white"
                            onClick={() => {
                                document.getElementById('journey')?.scrollIntoView({ behavior: 'smooth' })
                            }}
                        >
                            See How It Works
                        </Button>
                    </div>
                </motion.div>

                {/* Hero Visual */}
                <motion.div
                    initial={{ opacity: 0, y: 40 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.4, duration: 0.8 }}
                    className="mt-20 w-full max-w-6xl"
                >
                    <div className="relative w-full">
                        <ResearchMemoShowcase />
                    </div>
                </motion.div>
            </section>

            {/* Social Proof */}
            <section className="relative z-10 py-12">
                <div className="mx-auto max-w-7xl px-6 lg:px-8">
                    <p className="text-center text-sm font-semibold uppercase tracking-widest text-gray-500 mb-8">
                        Get investing summaries like:
                    </p>
                    <LogoLoop logos={logos} speed={0.5} pauseOnHover={true} />
                </div>
            </section>

            {/* Journey Section */}
            <div id="journey" className="relative z-10 w-full">
                <JourneySection />
            </div>

            {/* Persona Section */}
            <section id="personas" className="relative z-10 py-24">
                <div className="absolute -top-[20%] right-0 w-full h-[140%] md:w-1/2 opacity-60 pointer-events-none mix-blend-screen [mask-image:linear-gradient(to_bottom,black_80%,transparent_100%)]">
                    <CubeBackground />
                </div>
                <div className="mx-auto max-w-7xl px-6 lg:px-8 relative z-10">
                    <div className="grid gap-12 lg:grid-cols-2 items-center">
                        <div>
                            <h2 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">
                                Analyze through the lens of legends.
                            </h2>
                            <p className="mt-4 text-lg text-gray-300">
                                Adopt the mental models of world-class investors. Toggle personas to see how Buffett, Lynch, or Dalio might view the same set of facts.
                            </p>
                            <div className="mt-8">
                                <PersonaSelector selectedPersonas={['buffett', 'munger']} onSelectionChange={() => { }} />
                            </div>
                        </div>
                        <div className="relative h-[400px] w-full flex items-center justify-center">
                            {/* Stack moved to separate section */}
                        </div>
                    </div>
                </div>
            </section>

            {/* Stack Showcase Section */}
            <section id="visuals" className="relative z-10 py-32 flex flex-col justify-center items-center overflow-hidden">
                <div className="text-center mb-16 max-w-3xl px-6">
                    <h2 className="text-4xl md:text-5xl font-bold tracking-tight text-white mb-6">
                        Visual Intelligence
                    </h2>
                    <p className="text-lg md:text-xl text-gray-400 leading-relaxed">
                        Experience data like never before. Our interface is designed for clarity, speed, and depth, giving you the insights you need at a glance.
                    </p>
                </div>
                <Stack
                    randomRotation={true}
                    sensitivity={180}
                    sendToBackOnClick={false}
                    cardDimensions={{ width: 600, height: 450 }}
                    cardsData={[
                        { id: 1, img: "/concept-1.png" },
                        { id: 2, img: "/concept-2.png" },
                        { id: 3, img: "/concept-3.png" },
                        { id: 4, img: "/concept-1.png" },
                        { id: 5, img: "/concept-2.png" },
                        { id: 6, img: "/concept-3.png" },
                    ]}
                />
            </section>

            {/* Footer */}
            {/* Footer */}
            <MegaFooter />
        </main>
    )
}
