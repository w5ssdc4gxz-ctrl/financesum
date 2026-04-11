'use client'

import React, { useEffect, useRef, useState, useMemo } from "react";
import { motion, useReducedMotion, useScroll, useTransform, useVelocity, useSpring, MotionValue } from "framer-motion";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { Wallet, CreditCard, PieChart, TrendingUp, PiggyBank, Receipt } from "lucide-react";
import { TextRotator } from "@/components/fancy";

// Rotating words for the hero
const rotatingWords = ['reimagined.', 'simplified.', 'automated.', 'transformed.'];

// Helper: clamp to keep parallax subtle
const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

export default function FloatingObjectsHero() {
    const reduceMotion = useReducedMotion();
    const wrapRef = useRef<HTMLDivElement>(null);
    const router = useRouter();
    const { user } = useAuth();

    // normalized mouse position in [-0.5, 0.5]
    const [mouse, setMouse] = useState({ x: 0, y: 0 });

    useEffect(() => {
        const el = wrapRef.current;
        if (!el) return;

        const onMove = (e: MouseEvent) => {
            const r = el.getBoundingClientRect();
            const nx = (e.clientX - (r.left + r.width / 2)) / r.width;
            const ny = (e.clientY - (r.top + r.height / 2)) / r.height;
            setMouse({ x: clamp(nx, -0.5, 0.5), y: clamp(ny, -0.5, 0.5) });
        };

        window.addEventListener("mousemove", onMove, { passive: true });
        return () => window.removeEventListener("mousemove", onMove);
    }, []);

    const { scrollYProgress } = useScroll({
        target: wrapRef,
        offset: ["start start", "end center"],
    });

    const scrollVelocity = useVelocity(scrollYProgress);
    const smoothVelocity = useSpring(scrollVelocity, {
        damping: 50,
        stiffness: 400
    });
    // scale velocity to just the magnitude
    const velocityMagnitude = useTransform(smoothVelocity, [-0.1, 0, 0.1], [4, 0, 4]);
    const blurAmount = useTransform(velocityMagnitude, (v: number) => `blur(${v}px)`);

    // As we scroll down, objects move to the center bottom (gather)
    const gatherProgress = useTransform(scrollYProgress, [0, 1], [0, 1]);

    const items = useMemo(
        () => [
            { id: "wallet", icon: <Wallet size={32} className="text-black dark:text-white" />, x: 12, y: 22, depth: 28, delay: 0.05 },
            { id: "chart", icon: <PieChart size={40} className="text-black dark:text-white" />, x: 82, y: 15, depth: 18, delay: 0.12 },
            { id: "card", icon: <CreditCard size={36} className="text-black dark:text-white" />, x: 18, y: 65, depth: 22, delay: 0.18 },
            { id: "piggy", icon: <PiggyBank size={48} className="text-black dark:text-white" />, x: 88, y: 70, depth: 14, delay: 0.24 },
            { id: "trend", icon: <TrendingUp size={28} className="text-black dark:text-white" />, x: 75, y: 40, depth: 20, delay: 0.30 },
            { id: "receipt", icon: <Receipt size={34} className="text-black dark:text-white" />, x: 25, y: 85, depth: 25, delay: 0.15 },
        ],
        []
    );

    return (
        <section
            ref={wrapRef}
            className="relative overflow-hidden min-h-[90vh] flex flex-col items-center justify-center py-24 px-6 border-b border-zinc-200 dark:border-zinc-800"
        >
            {/* Background Grid */}
            <div className="absolute inset-0 pointer-events-none opacity-[0.03] dark:opacity-[0.05]"
                style={{ backgroundImage: 'linear-gradient(to right, currentColor 1px, transparent 1px), linear-gradient(to bottom, currentColor 1px, transparent 1px)', backgroundSize: '4rem 4rem' }}
            />

            {/* Floating objects layer */}
            <div
                aria-hidden
                className="absolute inset-0 pointer-events-none"
            >
                {items.map((it, i) => (
                    <FloatingItem key={it.id} it={it} i={i} reduceMotion={reduceMotion} mouse={mouse} gatherProgress={gatherProgress} blurAmount={blurAmount} />
                ))}
            </div>

            {/* Hero content */}
            <div className="relative text-center max-w-4xl z-10 space-y-8">
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8 }}
                    className="inline-flex items-center gap-2 px-3 py-1 bg-white dark:bg-black border border-black dark:border-white text-xs font-bold tracking-widest uppercase mb-4 shadow-sm"
                >
                    <span className="relative flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full bg-black dark:bg-white opacity-75"></span>
                        <span className="relative inline-flex pl-1 h-2 w-2 bg-black dark:bg-white"></span>
                    </span>
                    Now in Public Beta
                </motion.div>

                <motion.h1
                    initial={{ opacity: 0, y: 30 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8, delay: 0.2, ease: [0.25, 1, 0.5, 1] }}
                    className="text-6xl sm:text-7xl md:text-8xl lg:text-9xl font-black tracking-tighter text-black dark:text-white uppercase leading-[0.9] -mb-2"
                >
                    <span>Financial analysis,</span>
                    <br />
                    <span className="text-zinc-400 dark:text-zinc-600 block mt-2">
                        <TextRotator
                            words={rotatingWords}
                            interval={3000}
                            animationType="blur"
                        />
                    </span>
                </motion.h1>

                <motion.p
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8, delay: 0.4, ease: [0.25, 1, 0.5, 1] }}
                    className="text-lg md:text-xl text-zinc-600 dark:text-zinc-400 font-medium max-w-2xl mx-auto mb-16 text-balance leading-relaxed"
                >
                    FinanceSum digests 10-Ks, earnings calls, and market news into
                    executive-grade memos. Stop drowning in filings.
                </motion.p>

                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8, delay: 0.6, ease: [0.25, 1, 0.5, 1] }}
                    className="flex flex-col sm:flex-row items-center justify-center gap-4 relative z-20"
                >
                    <button
                        onClick={() => router.push(user ? '/dashboard' : '/signup')}
                        className="w-full sm:w-auto bg-black text-white hover:bg-zinc-800 dark:bg-white dark:text-black dark:hover:bg-zinc-200 border border-black dark:border-white text-sm font-bold tracking-widest uppercase px-10 py-5 transition-colors"
                    >
                        Start Analyzing
                    </button>
                    <button
                        onClick={() => {
                            document.getElementById('walkthrough')?.scrollIntoView({ behavior: 'smooth' });
                        }}
                        className="w-full sm:w-auto bg-white text-black hover:bg-zinc-50 dark:bg-black dark:text-white dark:hover:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-sm font-bold tracking-widest uppercase px-10 py-5 transition-colors"
                    >
                        Watch Demo
                    </button>
                </motion.div>
            </div>
        </section>
    );
}

interface FloatingItemProps {
    it: { id: string, icon: React.ReactNode, x: number, y: number, depth: number, delay: number };
    i: number;
    reduceMotion: boolean | null;
    mouse: { x: number, y: number };
    gatherProgress: MotionValue<number>;
    blurAmount: MotionValue<string>;
}

const FloatingItem: React.FC<FloatingItemProps> = ({ it, i, reduceMotion, mouse, gatherProgress, blurAmount }) => {
    // Target point for objects to gather towards is middle bottom (50% X, 100% Y)
    // The distance to travel is derived from the starting position
    const travelX = `${(50 - it.x)}vw`;
    const travelY = `${(120 - it.y)}vh`;

    const parallaxX = reduceMotion ? 0 : mouse.x * it.depth;
    const parallaxY = reduceMotion ? 0 : mouse.y * it.depth;

    const gatherProgressSpring = useSpring(gatherProgress, {
        damping: 30,
        stiffness: 100,
        restDelta: 0.001
    });

    return (
        <motion.div
            key={it.id}
            className="absolute z-0"
            style={{
                left: `calc(${it.x}% - 3rem)`,
                top: `calc(${it.y}% - 3rem)`,
                willChange: "transform, filter, opacity",
                x: useTransform(gatherProgressSpring, [0, 1], ["0vw", travelX]),
                y: useTransform(gatherProgressSpring, [0, 1], ["0vh", travelY]),
                scale: useTransform(gatherProgressSpring, [0, 0.8, 1], [1, 0.8, 0]),
                opacity: useTransform(gatherProgressSpring, [0, 0.6, 1], [1, 1, 0]),
                filter: blurAmount,
            }}
            initial={{
                opacity: 0,
                scale: 0.9,
                y: 18,
            }}
            animate={{
                opacity: 1,
                scale: 1,
                y: 0,
            }}
            transition={{
                duration: 0.6,
                ease: "easeOut",
                delay: it.delay,
            }}
        >
            <motion.div
                className="flex items-center justify-center bg-white dark:bg-zinc-900 rounded-3xl"
                style={{
                    width: '6rem',
                    height: '6rem',
                    boxShadow: '0 20px 40px -10px rgba(0,0,0,0.1), 0 10px 20px -5px rgba(0,0,0,0.04), 0 0 0 1px rgba(0,0,0,0.02)',
                }}
                animate={
                    reduceMotion
                        ? { x: 0, y: 0, rotate: 0 }
                        : {
                            x: [parallaxX, parallaxX + (i % 2 === 0 ? 8 : -8), parallaxX],
                            y: [parallaxY, parallaxY + (i % 2 === 0 ? -12 : 12), parallaxY],
                            rotate: [0, i % 2 === 0 ? 4 : -4, 0],
                        }
                }
                transition={
                    reduceMotion
                        ? { duration: 0.5, ease: "easeOut", delay: it.delay }
                        : {
                            x: { duration: 6 + i * 0.4, repeat: Infinity, ease: "easeInOut", delay: it.delay },
                            y: { duration: 6.5 + i * 0.4, repeat: Infinity, ease: "easeInOut", delay: it.delay },
                            rotate: { duration: 7 + i * 0.3, repeat: Infinity, ease: "easeInOut", delay: it.delay },
                        }
                }
            >
                <motion.div
                    style={{
                        opacity: useTransform(gatherProgressSpring, [0, 0.5], [1, 0.5]) // fade icon out slightly on gather
                    }}
                >
                    {it.icon}
                </motion.div>
            </motion.div>
        </motion.div>
    );
}
