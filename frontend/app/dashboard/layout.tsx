'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { motion } from 'framer-motion'
import {
    IconChartHistogram,
    IconWorld,
    IconActivity,
    IconBuilding,
    IconSettings,
    IconBrandTabler,
} from '@tabler/icons-react'
import { Sidebar, SidebarBody, SidebarLink } from '@/components/ui/sidebar'
import { useAuth } from '@/contexts/AuthContext'

const navLinks = [
    {
        label: 'Overview',
        href: '/dashboard#overview',
        icon: <IconChartHistogram className="h-5 w-5 shrink-0 text-slate-600" />,
    },
    {
        label: 'Coverage',
        href: '/dashboard#coverage',
        icon: <IconWorld className="h-5 w-5 shrink-0 text-slate-600" />,
    },
    {
        label: 'Activity',
        href: '/dashboard#activity',
        icon: <IconActivity className="h-5 w-5 shrink-0 text-slate-600" />,
    },
    {
        label: 'Top Companies',
        href: '/dashboard#companies',
        icon: <IconBuilding className="h-5 w-5 shrink-0 text-slate-600" />,
    },
    {
        label: 'Settings',
        href: '/dashboard/settings',
        icon: <IconSettings className="h-5 w-5 shrink-0 text-slate-600" />,
    },
]

const SidebarLogo = ({ open }: { open: boolean }) => {
    return (
        <Link href="/dashboard" className="flex items-center gap-2 py-1">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-white">
                <IconBrandTabler className="h-5 w-5" />
            </div>
            <motion.span
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="whitespace-pre text-lg font-semibold text-neutral-900 dark:text-white"
            >
                FinanceSum
            </motion.span>
        </Link>
    )
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
    const { user, loading } = useAuth()
    const router = useRouter()
    const [sidebarOpen, setSidebarOpen] = useState(false)

    useEffect(() => {
        if (!loading && !user) {
            router.push('/')
        }
    }, [user, loading, router])

    if (loading) {
        return (
            <div className="h-screen w-full flex items-center justify-center bg-gray-50 dark:bg-gray-950">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
            </div>
        )
    }

    if (!user) return null

    return (
        <div className="h-screen w-full overflow-hidden bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-50 flex">
            <Sidebar open={sidebarOpen} setOpen={setSidebarOpen}>
                <SidebarBody className="justify-between gap-10">
                    <div className="flex flex-col flex-1 overflow-y-auto overflow-x-hidden">
                        <SidebarLogo open={sidebarOpen} />
                        <div className="mt-8 flex flex-col gap-2">
                            {navLinks.map((link) => (
                                <SidebarLink
                                    key={link.label}
                                    link={{
                                        ...link,
                                        icon: (
                                            <div className="h-6 w-6 flex-shrink-0 text-neutral-500 dark:text-neutral-400">
                                                {link.icon}
                                            </div>
                                        ),
                                    }}
                                />
                            ))}
                        </div>
                    </div>
                    <div className="mt-auto">
                        <SidebarLink
                            link={{
                                label: user?.user_metadata?.full_name ?? user?.email ?? 'Investor',
                                href: '/dashboard/settings',
                                icon: (
                                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-600 text-xs font-bold text-white">
                                        {(user?.user_metadata?.full_name ?? user?.email ?? 'FS').slice(0, 2).toUpperCase()}
                                    </div>
                                ),
                            }}
                        />
                    </div>
                </SidebarBody>
            </Sidebar>

            <div className="flex-1 flex flex-col h-full overflow-y-auto">
                <div className="mx-auto w-full max-w-[1600px] p-4 md:p-8">
                    {children}
                </div>
            </div>
        </div>
    )
}
