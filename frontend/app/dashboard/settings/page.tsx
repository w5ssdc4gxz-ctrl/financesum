"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/contexts/AuthContext"
import { useTheme } from "next-themes"
import { Button } from "@/components/base/buttons/button"
import { IconUser, IconLock, IconMoon, IconSun, IconLogout, IconDeviceDesktop } from "@tabler/icons-react"
import { motion } from "framer-motion"

export default function SettingsPage() {
    const router = useRouter()
    const { user, signOut } = useAuth()
    const { theme, setTheme } = useTheme()
    const [activeTab, setActiveTab] = useState("appearance")

    const tabs = [
        { id: "appearance", label: "Appearance", icon: IconSun },
        { id: "security", label: "Security", icon: IconLock },
        { id: "profile", label: "Profile", icon: IconUser },
    ]

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
                    </motion.div>
                </div>
            </div>
        </div>
    )
}
