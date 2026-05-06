'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useAuthStore } from '@/lib/auth/token-store'
import { useRouter } from 'next/navigation'

const NAV = [
  { href: '/', label: 'Dashboard', icon: '▦' },
  { href: '/positions', label: 'Positions', icon: '◈' },
  { href: '/strategies', label: 'Strategies', icon: '⟳' },
  { href: '/funding-rates', label: 'Funding Rates', icon: '◎' },
  { href: '/risk', label: 'Risk Limits', icon: '⚠' },
]

export default function SideNav() {
  const pathname = usePathname()
  const clearToken = useAuthStore((s) => s.clearToken)
  const router = useRouter()

  return (
    <nav style={{ width: 'var(--nav-width)', height: '100vh', background: 'var(--color-surface)', borderRight: '1px solid var(--color-border)', display: 'flex', flexDirection: 'column', position: 'fixed', left: 0, top: 0, zIndex: 10 }}>
      <div style={{ padding: '1.25rem 1.25rem 1rem', borderBottom: '1px solid var(--color-border)' }}>
        <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.65rem', letterSpacing: '0.2em', marginBottom: '0.25rem' }}>DRACULA</div>
        <div style={{ color: 'var(--color-text)', fontWeight: 700, fontSize: '0.9rem' }}>System</div>
      </div>

      <div style={{ flex: 1, padding: '1rem 0.75rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
        {NAV.map(({ href, label, icon }) => {
          const active = pathname === href
          return (
            <Link key={href} href={href} style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', padding: '0.5rem 0.75rem', borderRadius: 'var(--radius-sm)', textDecoration: 'none', color: active ? 'var(--color-accent)' : 'var(--color-text-dim)', background: active ? 'color-mix(in oklch, var(--color-accent) 12%, transparent)' : 'transparent', fontSize: '0.875rem', fontWeight: active ? 600 : 400, transition: 'all var(--duration-fast)' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{icon}</span>
              {label}
            </Link>
          )
        })}
      </div>

      <div style={{ padding: '0.75rem', borderTop: '1px solid var(--color-border)' }}>
        <button onClick={() => { clearToken(); router.replace('/login') }} style={{ width: '100%', padding: '0.5rem', background: 'transparent', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)', color: 'var(--color-text-dim)', fontSize: '0.8rem', cursor: 'pointer' }}>
          Sign Out
        </button>
      </div>
    </nav>
  )
}
