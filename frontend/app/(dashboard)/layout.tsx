'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth'
import toast from 'react-hot-toast'
import { useRouter } from 'next/navigation'
import { Menu, X } from 'lucide-react'
import { useState } from 'react'

// ── Navigation items ──────────────────────────────────────────────────────────
const NAV_ITEMS = [
  {
    href: '/meetings',
    label: 'Past Meetings',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor">
        <path d="M9 2a1 1 0 000 2h2a1 1 0 100-2H9z"/>
        <path fillRule="evenodd" d="M4 5a2 2 0 012-2 3 3 0 003 3h2a3 3 0 003-3 2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V5zm3 4a1 1 0 000 2h.01a1 1 0 100-2H7zm3 0a1 1 0 000 2h3a1 1 0 100-2h-3zm-3 4a1 1 0 100 2h.01a1 1 0 100-2H7zm3 0a1 1 0 100 2h3a1 1 0 100-2h-3z" clipRule="evenodd"/>
      </svg>
    ),
  },
  {
    href: '/meetings/live',
    label: 'Live Recording',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor">
        <path fillRule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clipRule="evenodd"/>
      </svg>
    ),
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor">
        <path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd"/>
      </svg>
    ),
  },
]

// ── Sidebar inner (needs auth context) ───────────────────────────────────────
function NavigationLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname()

  return (
    <nav className="sidebar-nav" aria-label="Main navigation">
      <div className="sidebar-section-label">Workspace</div>
      {NAV_ITEMS.map((item) => {
        const isActive =
          item.href === '/meetings'
            ? pathname === '/meetings' || (pathname.startsWith('/meetings/') && !pathname.startsWith('/meetings/live'))
            : pathname === item.href || pathname.startsWith(item.href + '/')

        return (
          <Link
            key={item.href}
            href={item.href}
            className={`nav-link${isActive ? ' nav-link-active' : ''}`}
            aria-current={isActive ? 'page' : undefined}
            onClick={onNavigate}
          >
            {item.icon}
            {item.label}
          </Link>
        )
      })}
    </nav>
  )
}

function UserFooter({ onLogout }: { onLogout?: () => void }) {
  const { user, logout } = useAuth()
  const router = useRouter()

  const handleLogout = async () => {
    try {
      await logout()
      toast.success('Signed out')
      onLogout?.()
      router.push('/login')
    } catch {
      toast.error('Logout failed')
    }
  }

  const avatarChar = user?.email?.charAt(0)?.toUpperCase() || '?'

  return (
    <div className="sidebar-footer">
      <div className="sidebar-user">
        <div className="sidebar-avatar" aria-hidden="true">
          {avatarChar}
        </div>
        <div className="sidebar-user-info">
          <div className="sidebar-user-email" title={user?.email}>
            {user?.email || 'Loading...'}
          </div>
        </div>
      </div>
      <button
        id="logout-btn"
        onClick={handleLogout}
        className="btn-secondary"
        style={{ width: '100%', justifyContent: 'center' }}
      >
        <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M3 3a1 1 0 00-1 1v12a1 1 0 102 0V4a1 1 0 00-1-1zm10.293 9.293a1 1 0 001.414 1.414l3-3a1 1 0 000-1.414l-3-3a1 1 0 10-1.414 1.414L14.586 9H7a1 1 0 100 2h7.586l-1.293 1.293z" clipRule="evenodd"/>
        </svg>
        Sign out
      </button>
    </div>
  )
}

function SidebarContent() {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-name">Meeting Memory</div>
        <div className="sidebar-brand-tagline">AI-Powered MOM Generator</div>
      </div>

      <NavigationLinks />
      <UserFooter />
    </aside>
  )
}

function MobileTopNav() {
  const pathname = usePathname()
  const [isOpen, setIsOpen] = useState(false)

  const currentLabel =
    NAV_ITEMS.find((item) =>
      item.href === '/meetings'
        ? pathname === '/meetings' || (pathname.startsWith('/meetings/') && !pathname.startsWith('/meetings/live'))
        : pathname === item.href || pathname.startsWith(item.href + '/')
    )?.label || 'Workspace'

  return (
    <>
      <header className="mobile-topbar">
        <button
          type="button"
          className="mobile-menu-btn"
          aria-label="Open workspace navigation"
          aria-expanded={isOpen}
          onClick={() => setIsOpen(true)}
        >
          <Menu size={20} />
        </button>
        <div className="mobile-title">
          <div className="mobile-title-current">{currentLabel}</div>
          <div className="mobile-title-brand">Meeting Memory</div>
        </div>
      </header>

      {isOpen && (
        <div className="mobile-drawer-layer" role="presentation">
          <button
            className="mobile-drawer-backdrop"
            aria-label="Close workspace navigation"
            onClick={() => setIsOpen(false)}
          />
          <aside className="mobile-drawer" aria-label="Workspace navigation">
            <div className="mobile-drawer-header">
              <div>
                <div className="sidebar-brand-name">Meeting Memory</div>
                <div className="sidebar-brand-tagline">AI-Powered MOM Generator</div>
              </div>
              <button
                type="button"
                className="mobile-menu-btn"
                aria-label="Close workspace navigation"
                onClick={() => setIsOpen(false)}
              >
                <X size={20} />
              </button>
            </div>
            <NavigationLinks onNavigate={() => setIsOpen(false)} />
            <UserFooter onLogout={() => setIsOpen(false)} />
          </aside>
        </div>
      )}
    </>
  )
}

// ── Layout ────────────────────────────────────────────────────────────────────
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="app-layout">
      <SidebarContent />
      <MobileTopNav />
      <main className="main-content">
        <div className="page-container">{children}</div>
      </main>
    </div>
  )
}
