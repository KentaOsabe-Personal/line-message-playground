import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router'

type AppLayoutProps = Readonly<{
  displayName: string
  onLogout: () => Promise<void>
  children: ReactNode
}>

const navigationItems = [
  { to: '/liff/channels', label: 'チャネル管理' },
  { to: '/liff/account', label: 'アカウント管理' },
  { to: '/liff/rich-menus', label: 'リッチメニュー管理' },
  { to: '/liff/deliveries', label: 'メッセージ配信' },
] as const

export default function AppLayout({ displayName, onLogout, children }: AppLayoutProps) {
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!menuOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      menuButtonRef.current?.focus()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [menuOpen])

  return (
    <div className="application-shell">
      <header className="application-header">
        <Link className="application-brand" to="/liff/channels">
          <span className="application-brand-mark" aria-hidden="true">L</span>
          <span className="application-brand-copy">
            <strong>LINE Message Playground</strong>
            <small>OWNER CONSOLE</small>
          </span>
        </Link>
        <button
          ref={menuButtonRef}
          type="button"
          className="navigation-disclosure"
          aria-controls="application-navigation"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          メニュー
        </button>
        <div id="application-navigation" className="application-navigation" data-open={menuOpen || undefined}>
          <nav aria-label="機能ナビゲーション">
            {navigationItems.map((item) => (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => isActive ? 'current' : undefined}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="owner-actions">
            <span className="owner-identity">
              <span className="owner-avatar" aria-hidden="true">{displayName.slice(0, 1).toUpperCase()}</span>
              <span className="owner-name">{displayName}</span>
            </span>
            <button type="button" className="secondary" onClick={() => void onLogout()}>
              この端末からログアウト
            </button>
          </div>
        </div>
      </header>
      {children}
    </div>
  )
}
