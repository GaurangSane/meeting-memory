/**
 * lib/api-client.ts
 *
 * Thin fetch wrapper that:
 *  - Attaches the Bearer access token from memory (NOT localStorage — XSS risk).
 *  - On a 401 response, attempts one token refresh via /api/v1/auth/refresh,
 *    then re-issues the original request.
 *  - On a second 401 (refresh also failed), clears the token and redirects
 *    to /login.
 *
 * Access token is kept in a module-level variable — survives React re-renders
 * but is wiped on page reload (intentional: forces re-auth via the httpOnly
 * refresh cookie, which the browser sends automatically on each load).
 */

const configuredApiBase = process.env.NEXT_PUBLIC_API_URL || ''

function isLocalUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1'
  } catch {
    return false
  }
}

function getApiBase(): string {
  if (typeof window === 'undefined') return configuredApiBase

  const currentHostIsLocal =
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1'

  if (!configuredApiBase || (isLocalUrl(configuredApiBase) && !currentHostIsLocal)) {
    return ''
  }

  return configuredApiBase
}

// Module-level token storage — not localStorage, not sessionStorage.
let _accessToken: string | null = null
type FetchOptions = {
  redirectOnAuthFailure?: boolean
}

export const apiClient = {
  setToken(token: string): void {
    _accessToken = token
  },

  getToken(): string | null {
    return _accessToken
  },

  clearToken(): void {
    _accessToken = null
  },

  async _fetch(
    path: string,
    init: RequestInit = {},
    options: FetchOptions = {}
  ): Promise<any> {
    const redirectOnAuthFailure = options.redirectOnAuthFailure !== false
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(init.headers as Record<string, string> | undefined),
    }

    if (_accessToken) {
      headers['Authorization'] = `Bearer ${_accessToken}`
    }

    const apiBase = getApiBase()
    const res = await fetch(`${apiBase}${path}`, {
      ...init,
      headers,
      credentials: 'include', // send the httpOnly refresh cookie
    })

    // ── 401: attempt one token refresh ──────────────────────────────────
    if (res.status === 401 && !path.includes('/auth/refresh')) {
      const refreshed = await _tryRefresh()
      if (refreshed) {
        // Re-issue original request with the new token
        headers['Authorization'] = `Bearer ${_accessToken}`
        const retry = await fetch(`${apiBase}${path}`, {
          ...init,
          headers,
          credentials: 'include',
        })
        if (retry.status === 401) {
          _accessToken = null
          if (redirectOnAuthFailure && typeof window !== 'undefined') {
            window.location.href = '/login'
          }
          throw new Error('Authentication expired. Please log in again.')
        }
        return _parseResponse(retry)
      } else {
        _accessToken = null
        if (redirectOnAuthFailure && typeof window !== 'undefined') {
          window.location.href = '/login'
        }
        throw new Error('Authentication expired. Please log in again.')
      }
    }

    return _parseResponse(res)
  },

  async get(path: string, options?: FetchOptions): Promise<any> {
    return this._fetch(path, { method: 'GET' }, options)
  },

  async post(path: string, body?: unknown): Promise<any> {
    return this._fetch(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },

  async patch(path: string, body?: unknown): Promise<any> {
    return this._fetch(path, {
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  },

  async delete(path: string): Promise<any> {
    return this._fetch(path, { method: 'DELETE' })
  },
}

async function _tryRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${getApiBase()}/api/v1/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    })
    if (res.ok) {
      const data = await res.json()
      _accessToken = data.access_token
      return true
    }
    return false
  } catch {
    return false
  }
}

async function _parseResponse(res: Response): Promise<any> {
  const contentType = res.headers.get('Content-Type') || ''

  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      if (contentType.includes('application/json')) {
        const err = await res.json()
        message = err.detail || err.message || message
      } else {
        message = await res.text()
      }
    } catch {
      // ignore parse errors on error responses
    }
    throw new Error(message)
  }

  if (res.status === 204) return null

  if (contentType.includes('application/json')) {
    return res.json()
  }

  return res.text()
}
