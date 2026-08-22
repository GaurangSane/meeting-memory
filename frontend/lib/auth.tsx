/**
 * lib/auth.ts
 *
 * React context providing authentication state and actions throughout the app.
 *
 * Access token: held in memory via apiClient.setToken() — not localStorage.
 * Refresh token: lives in the httpOnly cookie set by the backend; the browser
 *   sends it automatically on every /api/v1/auth/refresh call.
 * Session cookie: set by the backend on login/register — used by Next.js
 *   middleware to protect dashboard routes without a client-side check.
 *
 * On mount: the AuthProvider attempts a silent token refresh so that a user
 * who refreshes the page does not get logged out (their refresh cookie is
 * still valid). If the refresh fails, they are treated as logged out.
 */

'use client'

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  ReactNode,
} from 'react'
import { apiClient } from './api-client'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string
  email: string
  org_id: string
  role: string
}

interface AuthContextValue {
  user: AuthUser | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (orgName: string, email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

// ── Context ───────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null)

// ── Provider ──────────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // On mount: attempt silent refresh to restore session after page reload
  useEffect(() => {
    ;(async () => {
      try {
        const data = await fetch('/api/v1/auth/refresh', {
          method: 'POST',
          credentials: 'include',
        })
        if (data.ok) {
          const json = await data.json()
          apiClient.setToken(json.access_token)
          // Fetch the current user profile
          const me = await apiClient.get('/api/v1/users/me')
          setUser(me)
        }
      } catch {
        // No valid session — user will be redirected by middleware
      } finally {
        setIsLoading(false)
      }
    })()
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const data = await apiClient.post('/api/v1/auth/login', { email, password })
    apiClient.setToken(data.access_token)
    const me = await apiClient.get('/api/v1/users/me')
    setUser(me)
  }, [])

  const register = useCallback(
    async (orgName: string, email: string, password: string) => {
      const data = await apiClient.post('/api/v1/auth/register', {
        org_name: orgName,
        email,
        password,
      })
      apiClient.setToken(data.access_token)
      const me = await apiClient.get('/api/v1/users/me')
      setUser(me)
    },
    []
  )

  const logout = useCallback(async () => {
    try {
      await apiClient.post('/api/v1/auth/logout')
    } catch {
      // Ignore — clear local state regardless
    }
    apiClient.clearToken()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return ctx
}
