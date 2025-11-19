import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { AuthProvider } from '@/contexts/AuthContext'
import { QueryProvider } from '@/components/QueryProvider'
import ClickSpark from '@/components/ClickSpark'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'FinanceSum - Financial Analysis Platform',
  description: 'Comprehensive financial analysis and investor insights',
  icons: {
    icon: '/icon.svg',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <ClickSpark
          sparkColor="#E6D4FF"
          sparkSize={14}
          sparkRadius={28}
          sparkCount={12}
          duration={520}
          className="min-h-screen w-full"
        >
          <AuthProvider>
            <QueryProvider>{children}</QueryProvider>
          </AuthProvider>
        </ClickSpark>
      </body>
    </html>
  )
}














