'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { useAuthStore } from '@/lib/auth/token-store'

const NAV = [
  { href: '/', label: '仪表盘' },
  { href: '/funding-rates', label: '机会扫描' },
  { href: '/positions', label: '持仓管理' },
  { href: '/strategies', label: '策略控制' },
  { href: '/risk', label: '风控' },
]

function UtcClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      const y = now.getUTCFullYear()
      const mo = String(now.getUTCMonth() + 1).padStart(2, '0')
      const d = String(now.getUTCDate()).padStart(2, '0')
      const h = String(now.getUTCHours()).padStart(2, '0')
      const mi = String(now.getUTCMinutes()).padStart(2, '0')
      const s = String(now.getUTCSeconds()).padStart(2, '0')
      setTime(`${y}-${mo}-${d} ${h}:${mi}:${s} UTC`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>
      {time}
    </span>
  )
}

export default function TopNav() {
  const pathname = usePathname()
  const clearToken = useAuthStore((s) => s.clearToken)
  const router = useRouter()

  const handleEmergencyStop = () => {
    if (confirm('EMERGENCY STOP: 确认要立即停止所有策略？此操作不可撤销。')) {
      alert('已发送紧急停止指令')
    }
  }

  return (
    <nav style={{
      height: 'var(--topbar-height)',
      background: 'rgba(19, 24, 32, 0.92)',
      borderBottom: '1px solid var(--color-border)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 1.75rem',
      position: 'sticky',
      top: 0,
      zIndex: 100,
      backdropFilter: 'blur(10px)',
      WebkitBackdropFilter: 'blur(10px)',
    }}>
      {/* Brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.875rem', fontWeight: 700, fontSize: '1.0625rem', flexShrink: 0 }}>
        <div style={{
          width: 34, height: 34,
          background: 'linear-gradient(135deg, var(--color-accent), var(--color-blue))',
          borderRadius: 8,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: '1rem',
          color: '#0a0e14',
          boxShadow: '0 4px 12px rgba(0, 214, 143, 0.25)',
        }}>D</div>
        <span style={{ color: 'var(--color-text)' }}>Dracula</span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: '0.7rem',
          color: 'var(--color-text-muted)', fontWeight: 400,
          padding: '3px 8px', background: 'var(--color-surface-elev)', borderRadius: 3,
        }}>v0.3.0 · PHASE 0</span>
      </div>

      {/* Nav items */}
      <div style={{ display: 'flex', gap: 4, marginLeft: '2.75rem' }}>
        {NAV.map(({ href, label }) => {
          const active = pathname === href
          return (
            <Link
              key={href}
              href={href}
              style={{
                padding: '9px 16px',
                fontSize: '0.875rem',
                color: active ? 'var(--color-text)' : 'var(--color-text-dim)',
                background: active ? 'var(--color-surface-elev)' : 'transparent',
                borderRadius: 'var(--radius-sm)',
                textDecoration: 'none',
                fontWeight: active ? 600 : 400,
                transition: 'all var(--duration-fast)',
                whiteSpace: 'nowrap',
              }}
            >
              {label}
            </Link>
          )
        })}
      </div>

      {/* Right side */}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '1.125rem' }}>
        {/* System status pill */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 12px',
          background: 'var(--color-positive-dim)',
          border: '1px solid var(--color-accent-border)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--color-accent)',
          fontSize: '0.75rem', fontWeight: 600,
          fontFamily: 'var(--font-mono)',
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: 'var(--color-accent)',
            boxShadow: '0 0 8px var(--color-accent)',
            display: 'inline-block',
            animation: 'topnav-pulse 2s infinite',
          }} />
          SYSTEM ONLINE
        </div>

        <UtcClock />

        {/* Emergency stop */}
        <button
          onClick={handleEmergencyStop}
          style={{
            background: 'var(--color-negative-dim)',
            color: 'var(--color-negative)',
            border: '1px solid var(--color-negative)',
            padding: '7px 16px',
            borderRadius: 'var(--radius-sm)',
            fontFamily: 'var(--font-mono)',
            fontSize: '0.75rem',
            fontWeight: 700,
            cursor: 'pointer',
            letterSpacing: '0.05em',
            transition: 'all var(--duration-fast)',
          }}
          onMouseEnter={e => {
            const t = e.currentTarget
            t.style.background = 'var(--color-negative)'
            t.style.color = '#0a0e14'
            t.style.boxShadow = '0 0 16px rgba(255, 71, 87, 0.4)'
          }}
          onMouseLeave={e => {
            const t = e.currentTarget
            t.style.background = 'var(--color-negative-dim)'
            t.style.color = 'var(--color-negative)'
            t.style.boxShadow = 'none'
          }}
        >
          ⏻ EMERGENCY STOP
        </button>

        {/* Sign out */}
        <button
          onClick={() => { clearToken(); router.replace('/login') }}
          style={{
            background: 'transparent',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--color-text-muted)',
            fontSize: '0.75rem',
            padding: '7px 12px',
            cursor: 'pointer',
            fontFamily: 'var(--font-mono)',
          }}
        >
          Sign Out
        </button>
      </div>

      <style>{`
        @keyframes topnav-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(0.85); }
        }
      `}</style>
    </nav>
  )
}
