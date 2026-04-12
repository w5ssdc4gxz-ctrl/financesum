"use client"

import { useEffect, useMemo, useState, useCallback } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "@/contexts/AuthContext"
import { useTheme } from "next-themes"
import { Button } from "@/components/base/buttons/button"
import { IconMoon, IconSun, IconLogout, IconDeviceDesktop, IconCreditCard, IconCheck, IconArrowRight } from "@tabler/icons-react"
import { motion, AnimatePresence } from "framer-motion"
import { billingApi } from "@/lib/api-client"

const CHECKOUT_SESSION_STORAGE_KEY = "financesum.checkout.session_id"

type UsageResponse = {
    plan?: "free" | "pro"
    limit?: number
    used?: number
    remaining?: number
    period_start?: string | null
    period_end?: string | null
    subscription_status?: string | null
    cancel_at_period_end?: boolean | null
    is_pro?: boolean
    billing_unavailable?: boolean
}

// Clean, minimal theme card
function ThemeCard({
    mode,
    label,
    description,
    isSelected,
    onClick,
}: {
    mode: "light" | "dark" | "system"
    label: string
    description: string
    isSelected: boolean
    onClick: () => void
}) {
    const icons = {
        light: IconSun,
        dark: IconMoon,
        system: IconDeviceDesktop,
    }
    const Icon = icons[mode]

    return (
        <motion.button
            onClick={onClick}
            className={`group relative w-full text-left p-5 rounded-2xl border-2 transition-all duration-200 ${
                isSelected
                    ? "border-foreground bg-foreground/[0.03]"
                    : "border-transparent bg-muted/50 hover:bg-muted hover:border-border"
            }`}
            whileTap={{ scale: 0.98 }}
            layout
        >
            <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-4">
                    {/* Icon */}
                    <div className={`flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center transition-colors ${
                        isSelected ? "bg-foreground text-background" : "bg-muted-foreground/10 text-muted-foreground"
                    }`}>
                        <Icon size={20} strokeWidth={1.5} />
                    </div>
                    
                    {/* Text */}
                    <div>
                        <div className="font-semibold text-foreground">{label}</div>
                        <div className="text-sm text-muted-foreground mt-0.5">{description}</div>
                    </div>
                </div>

                {/* Check indicator */}
                <AnimatePresence>
                    {isSelected && (
                        <motion.div
                            initial={{ scale: 0, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0, opacity: 0 }}
                            transition={{ type: "spring", stiffness: 500, damping: 30 }}
                            className="flex-shrink-0 w-6 h-6 rounded-full bg-foreground flex items-center justify-center"
                        >
                            <IconCheck size={14} className="text-background" strokeWidth={3} />
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </motion.button>
    )
}

export default function SettingsPage() {
    const router = useRouter()
    const searchParams = useSearchParams()
    const { session, signOut } = useAuth()
    const { theme, setTheme } = useTheme()
    const [activeTab, setActiveTab] = useState("appearance")
    const [usage, setUsage] = useState<UsageResponse | null>(null)
    const [usageLoading, setUsageLoading] = useState(false)
    const [usageError, setUsageError] = useState<string | null>(null)
    const [cancelLoading, setCancelLoading] = useState(false)
    const [portalMessage, setPortalMessage] = useState<string | null>(null)
    const [upgradeLoading, setUpgradeLoading] = useState(false)

    const tabs = [
        { id: "appearance", label: "Appearance", icon: IconSun },
        { id: "billing", label: "Billing", icon: IconCreditCard },
    ]

    useEffect(() => {
        const tabParam = searchParams.get("tab")
        if (tabParam && ["appearance", "billing"].includes(tabParam)) {
            setActiveTab(tabParam)
        }
    }, [searchParams])

    const refreshUsage = useCallback(async () => {
        if (!session?.access_token) return
        setUsageLoading(true)
        setUsageError(null)
        try {
            const response = await billingApi.getUsage(session.access_token)
            setUsage(response.data ?? null)
        } catch (error: any) {
            setUsageError(error?.response?.data?.detail || error?.message || "Unable to load usage.")
        } finally {
            setUsageLoading(false)
        }
    }, [session?.access_token])

    useEffect(() => {
        if (activeTab === "billing") refreshUsage()
    }, [activeTab, refreshUsage])

    useEffect(() => {
        if (activeTab !== "billing") return
        const handleFocus = () => {
            if (document.visibilityState === "visible") refreshUsage()
        }
        window.addEventListener("focus", handleFocus)
        document.addEventListener("visibilitychange", handleFocus)
        return () => {
            window.removeEventListener("focus", handleFocus)
            document.removeEventListener("visibilitychange", handleFocus)
        }
    }, [activeTab, refreshUsage])

    useEffect(() => {
        if (activeTab !== "billing" || !session?.access_token) return
        const sessionId = typeof window !== "undefined" 
            ? window.localStorage.getItem(CHECKOUT_SESSION_STORAGE_KEY) 
            : null
        if (!sessionId) return
        
        let cancelled = false
        const sync = async () => {
            try {
                await billingApi.syncCheckoutSession(sessionId, session.access_token)
                if (!cancelled) {
                    window.localStorage.removeItem(CHECKOUT_SESSION_STORAGE_KEY)
                    refreshUsage()
                }
            } catch { /* ignore */ }
        }
        sync()
        return () => { cancelled = true }
    }, [activeTab, session?.access_token, refreshUsage])

    const normalizedPlan = useMemo(() => (usage?.plan ?? "").toLowerCase(), [usage?.plan])
    const isProPlan = normalizedPlan === "pro"
    const remainingCount = useMemo(() => {
        const raw = usage?.remaining ?? 0
        return Number.isFinite(Number(raw)) ? Number(raw) : 0
    }, [usage?.remaining])
    const showUpgrade = !isProPlan
    const isCanceling = isProPlan && (usage?.cancel_at_period_end || usage?.subscription_status === "canceled")

    const usagePercent = useMemo(() => {
        if (!usage?.limit || !usage.used) return 0
        return Math.min(100, Math.round((usage.used / usage.limit) * 100))
    }, [usage?.limit, usage?.used])

    const cancellationLabel = useMemo(() => {
        if (!usage?.period_end) return "the current period ends"
        const parsed = new Date(usage.period_end)
        return Number.isNaN(parsed.valueOf()) ? "the current period ends" : parsed.toLocaleDateString()
    }, [usage?.period_end])

    const handleOpenStripePortal = async () => {
        if (!session?.access_token || cancelLoading) return
        setCancelLoading(true)
        setPortalMessage(null)
        setUsageError(null)
        try {
            const response = await billingApi.createPortalSession(session.access_token)
            const url = response.data?.url as string | undefined
            if (!url) throw new Error("Missing portal URL")
            setPortalMessage("Redirecting to Stripe…")
            window.location.href = url
        } catch (error: any) {
            setUsageError(error?.response?.data?.detail || error?.message || "Unable to open portal.")
        } finally {
            setCancelLoading(false)
        }
    }

    const handleUpgradeToPro = async () => {
        if (!session?.access_token || upgradeLoading) return
        setUpgradeLoading(true)
        setPortalMessage(null)
        setUsageError(null)
        try {
            const response = await billingApi.createCheckoutSession({ plan: "pro" }, session.access_token)
            const url = response.data?.url as string | undefined
            const sid = response.data?.id as string | undefined
            if (!url) throw new Error("Missing checkout URL")
            if (sid) window.localStorage.setItem(CHECKOUT_SESSION_STORAGE_KEY, sid)
            window.location.href = url
        } catch (error: any) {
            setUsageError(error?.response?.data?.detail || error?.message || "Unable to start checkout.")
        } finally {
            setUpgradeLoading(false)
        }
    }

    return (
        <div className="min-h-screen bg-background">
            <div className="max-w-3xl mx-auto px-6 py-12">
                {/* Header */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex items-center justify-between mb-12"
                >
                    <div>
                        <h1 className="text-2xl font-bold text-foreground">Settings</h1>
                        <p className="text-muted-foreground mt-1">Manage your preferences</p>
                    </div>
                    <Button
                        color="ghost"
                        onClick={async () => {
                            await signOut()
                            router.push("/")
                        }}
                        className="text-muted-foreground hover:text-foreground"
                    >
                        <IconLogout size={18} />
                        <span className="ml-2">Log out</span>
                    </Button>
                </motion.div>

                {/* Tabs */}
                <motion.div
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.05 }}
                    className="flex gap-1 p-1 bg-muted/50 rounded-xl mb-8 w-fit"
                >
                    {tabs.map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`relative px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                                activeTab === tab.id
                                    ? "text-foreground"
                                    : "text-muted-foreground hover:text-foreground"
                            }`}
                        >
                            {activeTab === tab.id && (
                                <motion.div
                                    layoutId="activeSettingsTab"
                                    className="absolute inset-0 bg-background rounded-lg shadow-sm"
                                    transition={{ type: "spring", stiffness: 400, damping: 30 }}
                                />
                            )}
                            <span className="relative z-10 flex items-center gap-2">
                                <tab.icon size={16} />
                                {tab.label}
                            </span>
                        </button>
                    ))}
                </motion.div>

                {/* Content */}
                <AnimatePresence mode="wait">
                    {activeTab === "appearance" && (
                        <motion.div
                            key="appearance"
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            transition={{ duration: 0.2 }}
                        >
                            <div className="mb-6">
                                <h2 className="text-lg font-semibold text-foreground">Theme</h2>
                                <p className="text-sm text-muted-foreground mt-1">
                                    Choose how FinanceSum looks on your device
                                </p>
                            </div>

                            <div className="space-y-3">
                                <ThemeCard
                                    mode="light"
                                    label="Light"
                                    description="Clean and bright interface"
                                    isSelected={theme === "light"}
                                    onClick={() => setTheme("light")}
                                />
                                <ThemeCard
                                    mode="dark"
                                    label="Dark"
                                    description="Easy on the eyes in low light"
                                    isSelected={theme === "dark"}
                                    onClick={() => setTheme("dark")}
                                />
                                <ThemeCard
                                    mode="system"
                                    label="System"
                                    description="Follows your device settings"
                                    isSelected={theme === "system"}
                                    onClick={() => setTheme("system")}
                                />
                            </div>
                        </motion.div>
                    )}

                    {activeTab === "billing" && (
                        <motion.div
                            key="billing"
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            transition={{ duration: 0.2 }}
                            className="space-y-6"
                        >
                            {/* Plan Card */}
                            <div className="p-6 rounded-2xl border border-border bg-card">
                                {usageLoading ? (
                                    <div className="text-muted-foreground text-sm">Loading…</div>
                                ) : (
                                    <div className="space-y-6">
                                        {/* Plan Header */}
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <div className="text-sm text-muted-foreground">Current plan</div>
                                                <div className="text-2xl font-bold text-foreground mt-1">
                                                    {isProPlan ? "Pro" : "Free"}
                                                </div>
                                            </div>
                                            {!isProPlan && (
                                                <span className="text-xs px-2.5 py-1 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 font-medium">
                                                    Trial
                                                </span>
                                            )}
                                            {isProPlan && (
                                                <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 font-medium">
                                                    Active
                                                </span>
                                            )}
                                        </div>

                                        {/* Usage */}
                                        <div>
                                            <div className="flex items-center justify-between text-sm mb-2">
                                                <span className="text-muted-foreground">Usage this period</span>
                                                <span className="font-medium text-foreground">
                                                    {usage?.used ?? 0} / {usage?.limit ?? 0}
                                                </span>
                                            </div>
                                            <div className="h-2 bg-muted rounded-full overflow-hidden">
                                                <motion.div
                                                    className="h-full bg-foreground rounded-full"
                                                    initial={{ width: 0 }}
                                                    animate={{ width: `${usagePercent}%` }}
                                                    transition={{ duration: 0.5, ease: "easeOut" }}
                                                />
                                            </div>
                                            <div className="text-xs text-muted-foreground mt-2">
                                                {remainingCount} summaries remaining
                                                {usage?.period_end && isProPlan && (
                                                    <> · Resets {new Date(usage.period_end).toLocaleDateString()}</>
                                                )}
                                            </div>
                                        </div>

                                        {/* Cancellation notice */}
                                        {isCanceling && (
                                            <div className="text-sm p-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800">
                                                Your subscription ends on {cancellationLabel}
                                            </div>
                                        )}

                                        {/* Actions */}
                                        <div className="flex gap-3 pt-2">
                                            <Button
                                                onClick={refreshUsage}
                                                color="ghost"
                                                disabled={usageLoading}
                                                className="text-sm"
                                            >
                                                Refresh
                                            </Button>
                                            {isProPlan && (
                                                <Button
                                                    onClick={handleOpenStripePortal}
                                                    color="ghost"
                                                    disabled={cancelLoading}
                                                    className="text-sm"
                                                >
                                                    {cancelLoading ? "Opening…" : "Manage subscription"}
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Upgrade Card */}
                            {showUpgrade && (
                                <motion.div
                                    initial={{ opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: 0.1 }}
                                    className="p-6 rounded-2xl bg-foreground text-background"
                                >
                                    <div className="flex items-center justify-between gap-6">
                                        <div>
                                            <div className="font-semibold text-lg">Upgrade to Pro</div>
                                            <div className="text-sm opacity-70 mt-1">
                                                100 summaries/month, priority support, all export formats
                                            </div>
                                        </div>
                                        <Button
                                            onClick={handleUpgradeToPro}
                                            disabled={upgradeLoading}
                                            className="flex-shrink-0 bg-background text-foreground hover:bg-background/90 font-medium"
                                        >
                                            {upgradeLoading ? "Loading…" : (
                                                <>
                                                    Upgrade
                                                    <IconArrowRight size={16} className="ml-1" />
                                                </>
                                            )}
                                        </Button>
                                    </div>
                                </motion.div>
                            )}

                            {/* Error/Success Messages */}
                            <AnimatePresence>
                                {portalMessage && (
                                    <motion.div
                                        initial={{ opacity: 0, y: 5 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0 }}
                                        className="text-sm p-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800"
                                    >
                                        {portalMessage}
                                    </motion.div>
                                )}
                                {usageError && (
                                    <motion.div
                                        initial={{ opacity: 0, y: 5 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0 }}
                                        className="text-sm p-3 rounded-lg bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800"
                                    >
                                        {usageError}
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    )
}
