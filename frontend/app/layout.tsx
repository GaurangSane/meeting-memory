import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { Toaster } from 'react-hot-toast'
import { AuthProvider } from '@/lib/auth'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Meeting Memory — AI-Powered MOM Generator',
  description:
    'Intelligent meeting minutes generation with RAG-powered learning. Automatically captures, transcribes, and generates structured MOM with a memory that improves over time.',
  keywords: ['meeting minutes', 'MOM', 'AI', 'transcription', 'RAG', 'SaaS'],
  openGraph: {
    title: 'Meeting Memory — AI-Powered MOM Generator',
    description: 'Intelligent meeting minutes generation with RAG-powered learning.',
    type: 'website',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={inter.variable}>
      <head>
        <meta name="color-scheme" content="dark" />
        <meta name="theme-color" content="#0d0f1a" />
      </head>
      <body>
        <AuthProvider>
          {children}
        </AuthProvider>
        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: 'rgba(19,22,32,0.95)',
              color: '#f1f5f9',
              border: '1px solid rgba(255,255,255,0.1)',
              backdropFilter: 'blur(12px)',
              borderRadius: '10px',
              fontSize: '14px',
              fontFamily: 'Inter, sans-serif',
            },
            success: {
              iconTheme: { primary: '#10b981', secondary: '#f1f5f9' },
              duration: 4000,
            },
            error: {
              iconTheme: { primary: '#ef4444', secondary: '#f1f5f9' },
              duration: 5000,
            },
          }}
        />
      </body>
    </html>
  )
}
