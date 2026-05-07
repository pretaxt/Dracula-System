'use client'
import { useState, useEffect } from 'react'
import { usePathname } from 'next/navigation'
import SideNav from './SideNav'
import TopBar from './TopBar'
import NotificationDrawer from './NotificationDrawer'

export default function DashboardShell({ children }: { children: React.ReactNode }) {
  const [navOpen, setNavOpen] = useState(false)
  const [notifOpen, setNotifOpen] = useState(false)
  const pathname = usePathname()

  useEffect(() => {
    setNavOpen(false)
  }, [pathname])

  useEffect(() => {
    if (navOpen || notifOpen) {
      const prev = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      return () => {
        document.body.style.overflow = prev
      }
    }
  }, [navOpen, notifOpen])

  return (
    <div style={{ minHeight: '100vh', position: 'relative' }}>
      <SideNav isMobileOpen={navOpen} onClose={() => setNavOpen(false)} />
      <div
        className="sidenav-backdrop"
        data-open={navOpen}
        onClick={() => setNavOpen(false)}
        aria-hidden
      />
      <div
        className="layout-main-area"
        style={{
          marginLeft: 'var(--sidenav-width)',
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          position: 'relative',
          zIndex: 1,
        }}
      >
        <TopBar
          onMenuClick={() => setNavOpen(true)}
          onNotifClick={() => setNotifOpen(true)}
        />
        <main style={{ flex: 1, padding: '24px', overflowX: 'hidden' }}>{children}</main>
      </div>
      {notifOpen && <NotificationDrawer onClose={() => setNotifOpen(false)} />}
    </div>
  )
}
