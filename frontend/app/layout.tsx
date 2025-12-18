import type { Metadata } from 'next'
import localFont from 'next/font/local'
import { Playfair_Display } from 'next/font/google'
import './globals.css'
import { AuthProvider } from '@/contexts/AuthContext'
import { QueryProvider } from '@/components/QueryProvider'
import { ThemeProvider } from '@/components/theme-provider'

const geistSans = localFont({
  src: [
    {
      path: './fonts/Geist-Regular.woff2',
      weight: '400',
      style: 'normal',
    },
    {
      path: './fonts/Geist-Medium.woff2',
      weight: '500',
      style: 'normal',
    },
    {
      path: './fonts/Geist-SemiBold.woff2',
      weight: '600',
      style: 'normal',
    },
    {
      path: './fonts/Geist-Bold.woff2',
      weight: '700',
      style: 'normal',
    },
  ],
  variable: '--font-geist-sans',
  display: 'swap',
})

const geistMono = localFont({
  src: './fonts/GeistMono-Regular.woff2',
  variable: '--font-geist-mono',
  display: 'swap',
})

const flaviotte = localFont({
  src: './fonts/Flaviotte.woff2',
  variable: '--font-flaviotte',
  display: 'swap',
})

const playfair = Playfair_Display({
  subsets: ['latin'],
  variable: '--font-playfair',
  display: 'swap',
})

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
    <html lang="en" suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable} ${flaviotte.variable} ${playfair.variable} font-sans antialiased`}>
        <AuthProvider>
          <QueryProvider>
            <ThemeProvider
              attribute="class"
              defaultTheme="light"
              enableSystem
              disableTransitionOnChange
            >
              {children}
            </ThemeProvider>
          </QueryProvider>
        </AuthProvider>
      </body>
    </html>
  )
}
