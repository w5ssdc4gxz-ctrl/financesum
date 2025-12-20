"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "@/contexts/AuthContext"
import { useTheme } from "next-themes"
import { Button } from "@/components/base/buttons/button"
import { IconUser, IconLock, IconMoon, IconSun, IconLogout, IconDeviceDesktop, IconCreditCard } from "@tabler/icons-react"
import { motion } from "framer-motion"
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

export default function SettingsPage() {
    const router = useRouter()
    const searchParams = useSearchParams()
    const { user, session, signOut } = useAuth()
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
        { id: "security", label: "Security", icon: IconLock },
        { id: "profile", label: "Profile", icon: IconUser },
        { id: "billing", label: "Billing", icon: IconCreditCard },
    ]

    useEffect(() => {
        if (activeTab !== "appearance") return
        const tabParam = searchParams.get("tab")
        if (!tabParam) return
        if (["appearance", "security", "profile", "billing"].includes(tabParam)) {
            setActiveTab(tabParam)
        }
    }, [searchParams, activeTab])

    const refreshUsage = async () => {
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
    }

    useEffect(() => {
        if (activeTab !== "billing") return
        refreshUsage()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab, session?.access_token])

    useEffect(() => {
        if (activeTab !== "billing") return
        const handleFocus = () => {
            if (document.visibilityState === "visible") {
                refreshUsage()
            }
        }
        window.addEventListener("focus", handleFocus)
        document.addEventListener("visibilitychange", handleFocus)
        return () => {
            window.removeEventListener("focus", handleFocus)
            document.removeEventListener("visibilitychange", handleFocus)
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab])

    useEffect(() => {
        if (activeTab !== "billing") return
        if (!session?.access_token) return
        if (typeof window === "undefined") return
        const sessionId = window.localStorage.getItem(CHECKOUT_SESSION_STORAGE_KEY)
        if (!sessionId) return
        let cancelled = false
        const syncPendingCheckout = async () => {
            try {
                await billingApi.syncCheckoutSession(sessionId, session.access_token)
                if (cancelled) return
                window.localStorage.removeItem(CHECKOUT_SESSION_STORAGE_KEY)
                refreshUsage()
            } catch {
                if (cancelled) return
            }
        }
        syncPendingCheckout()
        return () => {
            cancelled = true
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab, session?.access_token])

    const normalizedPlan = useMemo(() => (usage?.plan ?? "").toLowerCase(), [usage?.plan])
    const isProPlan = normalizedPlan === "pro"
    const remainingCount = useMemo(() => {
        const raw = usage?.remaining ?? 0
        const parsed = typeof raw === "string" ? Number(raw) : raw
        return Number.isFinite(parsed) ? parsed : 0
    }, [usage?.remaining])
    const showUpgrade = !isProPlan
    const upgradeTitle = remainingCount <= 0 ? "Trial used" : "Upgrade to Pro"
    const upgradeDescription =
        remainingCount <= 0
            ? "Upgrade to Pro to keep generating summaries."
            : "Get 1,000 summaries per month, priority support, and all export formats."
    const isCanceling = Boolean(
        isProPlan && (usage?.cancel_at_period_end || usage?.subscription_status === "canceled")
    )

    const usagePercent = useMemo(() => {
        if (!usage?.limit || !usage.used) return 0
        const ratio = Math.min(1, usage.used / usage.limit)
        return Math.round(ratio * 100)
    }, [usage?.limit, usage?.used])

    const usageBarColor = useMemo(() => {
        if (!usage?.limit) return "hsl(120 45% 45%)"
        const ratio = Math.min(1, (usage.used ?? 0) / usage.limit)
        const hue = Math.max(0, Math.min(120, 120 - 120 * ratio))
        return `hsl(${hue} 70% 45%)`
    }, [usage?.limit, usage?.used])

    const cancellationLabel = useMemo(() => {
        if (!usage?.period_end) return "the current period ends"
        const parsed = new Date(usage.period_end)
        return Number.isNaN(parsed.valueOf()) ? "the current period ends" : parsed.toLocaleDateString()
    }, [usage?.period_end])

    const handleOpenStripePortal = async () => {
        if (!session?.access_token) return
        if (cancelLoading) return
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
            setUsageError(error?.response?.data?.detail || error?.message || "Unable to open the Stripe portal.")
        } finally {
            setCancelLoading(false)
        }
    }

    const handleUpgradeToPro = async () => {
        if (!session?.access_token) return
        if (upgradeLoading) return
        setUpgradeLoading(true)
        setPortalMessage(null)
        setUsageError(null)
        try {
            const response = await billingApi.createCheckoutSession({ plan: "pro" }, session.access_token)
            const url = response.data?.url as string | undefined
            const sessionId = response.data?.id as string | undefined
            if (!url) throw new Error("Missing checkout URL")
            if (sessionId && typeof window !== "undefined") {
                window.localStorage.setItem(CHECKOUT_SESSION_STORAGE_KEY, sessionId)
            }
            window.location.href = url
        } catch (error: any) {
            setUsageError(error?.response?.data?.detail || error?.message || "Unable to start checkout.")
        } finally {
            setUpgradeLoading(false)
        }
    }

    return (
        <div className="max-w-4xl mx-auto p-6 space-y-8">
            <div className="flex items-center justify-between">
                <h1 className="text-3xl font-bold text-foreground">Settings</h1>
                <Button
                    color="ghost"
                    onClick={async () => {
                        await signOut()
                        router.push('/')
                    }}
                    className="gap-2 text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/20 border border-red-200 dark:border-red-900"
                >
                    <IconLogout size={18} />
                    Log out
                </Button>
            </div>

            <div className="flex flex-col md:flex-row gap-8">
                {/* Sidebar Tabs */}
                <div className="w-full md:w-64 flex flex-col gap-2">
                    {tabs.map((tab) => (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${activeTab === tab.id
                                ? "bg-primary/10 text-primary"
                                : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                }`}
                        >
                            <tab.icon size={18} />
                            {tab.label}
                        </button>
                    ))}
                </div>

                {/* Content Area */}
                <div className="flex-1 space-y-6">
                    <motion.div
                        key={activeTab}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.2 }}
                        className="bg-card border border-border rounded-2xl p-6 shadow-sm"
                    >
                        {activeTab === "appearance" && (
                            <div className="space-y-6">
                                <div>
                                    <h2 className="text-xl font-semibold mb-1">Appearance</h2>
                                    <p className="text-sm text-muted-foreground">Customize how FinanceSum looks on your device.</p>
                                </div>

                                <div className="grid grid-cols-3 gap-4">
                                    <button
                                        onClick={() => setTheme("light")}
                                        className={`flex flex-col items-center gap-3 p-4 rounded-xl border-2 transition-all ${theme === "light"
                                            ? "border-primary bg-primary/5"
                                            : "border-border hover:border-primary/50"
                                            }`}
                                    >
                                        <div className="p-3 rounded-full bg-white shadow-sm border border-gray-100">
                                            <IconSun size={24} className="text-orange-500" />
                                        </div>
                                        <span className="text-sm font-medium">Light</span>
                                    </button>

                                    <button
                                        onClick={() => setTheme("dark")}
                                        className={`flex flex-col items-center gap-3 p-4 rounded-xl border-2 transition-all ${theme === "dark"
                                            ? "border-primary bg-primary/5"
                                            : "border-border hover:border-primary/50"
                                            }`}
                                    >
                                        <div className="p-3 rounded-full bg-slate-950 shadow-sm border border-slate-800">
                                            <IconMoon size={24} className="text-blue-400" />
                                        </div>
                                        <span className="text-sm font-medium">Dark</span>
                                    </button>

                                    <button
                                        onClick={() => setTheme("system")}
                                        className={`flex flex-col items-center gap-3 p-4 rounded-xl border-2 transition-all ${theme === "system"
                                            ? "border-primary bg-primary/5"
                                            : "border-border hover:border-primary/50"
                                            }`}
                                    >
                                        <div className="p-3 rounded-full bg-gradient-to-br from-white to-slate-950 shadow-sm border border-gray-200">
                                            <IconDeviceDesktop size={24} className="text-gray-500 mix-blend-difference" />
                                        </div>
                                        <span className="text-sm font-medium">System</span>
                                    </button>
                                </div>
                            </div>
                        )}

                        {activeTab === "security" && (
                            <div className="space-y-6">
                                <div>
                                    <h2 className="text-xl font-semibold mb-1">Security</h2>
                                    <p className="text-sm text-muted-foreground">Manage your password and account security.</p>
                                </div>

                                <div className="space-y-4">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">New Password</label>
                                        <input
                                            type="password"
                                            className="w-full px-4 py-2 rounded-lg border border-input bg-background focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                                            placeholder="Enter new password"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Confirm Password</label>
                                        <input
                                            type="password"
                                            className="w-full px-4 py-2 rounded-lg border border-input bg-background focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                                            placeholder="Confirm new password"
                                        />
                                    </div>
                                    <div className="pt-2">
                                        <Button>Update Password</Button>
                                    </div>
                                </div>
                            </div>
                        )}

                        {activeTab === "profile" && (
                            <div className="space-y-6">
                                <div>
                                    <h2 className="text-xl font-semibold mb-1">Profile</h2>
                                    <p className="text-sm text-muted-foreground">Manage your personal information.</p>
                                </div>

                                <div className="space-y-4">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Email</label>
                                        <input
                                            type="email"
                                            disabled
                                            value={user?.email || ""}
                                            className="w-full px-4 py-2 rounded-lg border border-input bg-muted text-muted-foreground cursor-not-allowed"
                                        />
                                        <p className="text-xs text-muted-foreground">Email cannot be changed.</p>
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Full Name</label>
                                        <input
                                            type="text"
                                            defaultValue={user?.user_metadata?.full_name || ""}
                                            className="w-full px-4 py-2 rounded-lg border border-input bg-background focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                                            placeholder="Your full name"
                                        />
                                    </div>

                                    <div className="pt-2">
                                        <Button>Save Changes</Button>
                                    </div>
                                </div>
                            </div>
                        )}

                        {activeTab === "billing" && (
                            <div className="space-y-6">
                                <div>
                                    <h2 className="text-xl font-semibold mb-1">Billing & Usage</h2>
                                    <p className="text-sm text-muted-foreground">
                                        Track your monthly summaries and manage your subscription.
                                    </p>
                                </div>

                                <div className="rounded-2xl border border-border bg-background p-5 space-y-4">
                                    {usageLoading ? (
                                        <div className="text-sm text-muted-foreground">Loading usage…</div>
                                    ) : (
                                        <>
                                            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                                <div>
                                                    <div className="text-sm text-muted-foreground">Plan</div>
                                                    <div className="text-xl font-semibold">
                                                        {isProPlan ? "Pro" : "Free"}
                                                    </div>
                                                </div>
                                                <div className="text-sm text-muted-foreground">
                                                    {!isProPlan ? (
                                                        <>Trial limit (no reset)</>
                                                    ) : usage?.period_end ? (
                                                        <>Resets {new Date(usage.period_end).toLocaleDateString()}</>
                                                    ) : (
                                                        <>Resets —</>
                                                    )}
                                                </div>
                                            </div>

                                            <div className="space-y-2">
                                                <div className="flex items-center justify-between text-sm">
                                                    <span className="text-muted-foreground">Summaries remaining</span>
                                                    <span className="font-semibold">{usage?.remaining ?? 0}</span>
                                                </div>
                                                <div className="h-3 w-full rounded-full bg-slate-100 overflow-hidden">
                                                    <div
                                                        className="h-full rounded-full transition-all"
                                                        style={{ width: `${usagePercent}%`, backgroundColor: usageBarColor }}
                                                    />
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                    Used {usage?.used ?? 0}/{usage?.limit ?? 0} this period
                                                </div>
                                            </div>

                                    {usage?.billing_unavailable && (
                                        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
                                            Billing status is temporarily unavailable. Usage may be delayed.
                                        </div>
                                    )}

                                    {showUpgrade && (
                                        <div className="rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 via-white to-blue-100 px-4 py-4 text-sm text-slate-900 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between shadow-soft dark:border-slate-700 dark:bg-gradient-to-br dark:from-slate-900 dark:via-slate-900 dark:to-slate-800 dark:text-white">
                                            <div>
                                                <div className="text-sm font-semibold text-slate-900 dark:text-white">{upgradeTitle}</div>
                                                <div className="text-xs text-slate-600 dark:text-slate-300">
                                                    {upgradeDescription}
                                                </div>
                                            </div>
                                            <Button
                                                onClick={handleUpgradeToPro}
                                                disabled={upgradeLoading}
                                                size="sm"
                                                color="ghost"
                                                className="bg-white text-slate-900 border border-blue-200 hover:bg-blue-50 shadow-soft px-4 dark:bg-slate-900 dark:text-white dark:border-slate-700 dark:hover:bg-slate-800"
                                            >
                                                {upgradeLoading ? "Redirecting…" : "Upgrade to Pro"}
                                            </Button>
                                        </div>
                                    )}

                                    {isCanceling && (
                                        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
                                            Cancellation scheduled. You keep Pro access until {cancellationLabel}.
                                        </div>
                                    )}
                                        </>
                                    )}
                                </div>

                                <div className="flex flex-col gap-3 sm:flex-row">
                                    <Button
                                        onClick={refreshUsage}
                                        color="ghost"
                                        disabled={usageLoading}
                                    >
                                        Refresh usage
                                    </Button>

                                    {isProPlan && (
                                        <Button
                                            onClick={handleOpenStripePortal}
                                            color="ghost"
                                            className="border-red-200 text-red-600 hover:text-red-700 hover:border-red-300"
                                            disabled={cancelLoading}
                                        >
                                            {cancelLoading
                                                ? "Opening Stripe…"
                                                : isCanceling
                                                    ? "Manage in Stripe"
                                                    : "Cancel in Stripe"}
                                        </Button>
                                    )}
                                </div>

                                {portalMessage && (
                                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-700">
                                        {portalMessage}
                                    </div>
                                )}

                                {usageError && (
                                    <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
                                        {usageError}
                                    </div>
                                )}
                            </div>
                        )}
                    </motion.div>
                </div>
            </div>
        </div>
    )
}
