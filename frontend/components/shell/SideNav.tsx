'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { LayoutGrid, Target, Layers, ShieldAlert, Settings, LogOut, TrendingUp, FlaskConical } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useT } from '../i18n/I18nProvider'
import { StatusDot } from '../ui/Button'
import { useAuthStore } from '@/lib/auth/token-store'
import { getHealth, formatUptime } from '@/lib/api/health'
import { getStrategyStatus } from '@/lib/api/strategies'

const NAV = [
  { href: '/',           labelZh: '总览',       Icon: LayoutGrid },
  { href: '/market',     labelZh: '行情中心',   Icon: TrendingUp },
  { href: '/strategies', labelZh: '策略中心',   Icon: Target,      badge: '12' },
  { href: '/positions',  labelZh: '持仓与订单', Icon: Layers },
  { href: '/risk',       labelZh: '风控中心',   Icon: ShieldAlert },
  { href: '/backtest',   labelZh: '历史回测',   Icon: FlaskConical },
  { href: '/settings',   labelZh: '设置',       Icon: Settings },
] as const

type SideNavProps = {
  isMobileOpen?: boolean
  onClose?: () => void
}

export default function SideNav({ isMobileOpen = false, onClose }: SideNavProps = {}) {
  const pathname = usePathname()
  const { t } = useT()
  const router = useRouter()
  const clearToken = useAuthStore((s) => s.clearToken)
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 60_000 })
  const { data: stratStatus } = useQuery({ queryKey: ['strategy-status'], queryFn: getStrategyStatus, refetchInterval: 30_000 })
  const positionsCount = stratStatus?.open_positions ?? 0
  const handleLogout = () => {
    clearToken()
    router.replace('/login')
  }

  return (
    <aside
      className="sidenav-aside"
      data-open={isMobileOpen ? 'true' : 'false'}
      style={{
        width: 256,
        background: 'var(--bg-deepest)',
        borderRight: '1px solid var(--border-default)',
        display: 'flex',
        flexDirection: 'column',
        position: 'fixed',
        left: 0,
        top: 0,
        bottom: 0,
        zIndex: 10,
      }}
    >
      {/* Logo 区 */}
      <div style={{ padding: 24, borderBottom: '1px solid var(--border-subtle)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            className="logo-svg"
            style={{
              width: 48,
              height: 48,
              flexShrink: 0,
              borderRadius: 'var(--radius-md)',
              overflow: 'hidden',
              boxShadow: '0 0 16px var(--accent-blood-glow)',
              background: 'linear-gradient(135deg, var(--accent-blood), var(--accent-blood-dim))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/dgl_icon_square.PNG"
              alt="Dracula"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            />
          </div>
          <div>
            <h1
              style={{
                margin: 0,
                fontFamily: 'var(--font-display)',
                fontSize: 16,
                fontWeight: 600,
                letterSpacing: '0.08em',
                color: 'var(--text-primary)',
                lineHeight: 1.1,
              }}
            >
              DRACULA
            </h1>
            <p
              style={{
                margin: '4px 0 0',
                fontFamily: 'var(--font-mono)',
                fontSize: 8,
                letterSpacing: '0.35em',
                color: 'var(--accent-blood)',
              }}
            >
              {t('ARBITRAGE SYSTEM')}
            </p>
          </div>
        </div>
      </div>

      {/* 系统状态卡 */}
      <div
        className="animate-in"
        style={{
          margin: 16,
          padding: 12,
          background: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-md)',
          animationDelay: '0.1s',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: 'var(--text-tertiary)',
            }}
          >
            {t('系统状态')}
          </span>
          <StatusDot tone="active" />
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-primary)' }}>{t('运行中')}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4, color: 'var(--text-tertiary)' }}>
          ↑ {formatUptime(health?.uptime_seconds ?? 0)}
        </div>
      </div>

      {/* 导航 */}
      <nav style={{ flex: 1, padding: '8px 0', overflowY: 'auto' }}>
        {NAV.map(({ href, labelZh, Icon, ...rest }) => {
          const active = href === '/' ? pathname === '/' : pathname.startsWith(href)
          const staticBadge: string | undefined = 'badge' in rest ? (rest as { badge?: string }).badge : undefined
          const staticBadgeColor: string | undefined =
            'badgeColor' in rest ? (rest as { badgeColor?: string }).badgeColor : undefined
          // /positions 徽章动态来自 API（开仓数）；其他保留静态值
          const badge: string | undefined = href === '/positions'
            ? (positionsCount > 0 ? String(positionsCount) : undefined)
            : staticBadge
          const badgeColor: string | undefined = href === '/positions'
            ? 'var(--accent-emerald)'
            : staticBadgeColor
          return (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              style={{
                position: 'relative',
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '10px 24px',
                fontSize: 14,
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                background: active ? 'var(--bg-card)' : 'transparent',
                borderLeft: `2px solid ${active ? 'var(--accent-blood)' : 'transparent'}`,
                textDecoration: 'none',
                transition: 'color var(--duration-fast), background var(--duration-fast)',
              }}
            >
              <Icon size={16} />
              <span>{t(labelZh)}</span>
              {badge && (
                <span
                  style={{
                    marginLeft: 'auto',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    color: badgeColor || 'var(--text-tertiary)',
                  }}
                >
                  {badge}
                </span>
              )}
            </Link>
          )
        })}
      </nav>

      {/* 底部用户信息 + 退出 */}
      <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-subtle)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--radius-md)',
              background: 'var(--accent-blood)',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {t('老')}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, color: 'var(--text-primary)' }}>{t('老虎')}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--accent-blood)' }}>SUPER ADMIN</div>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            title="退出登录 / Sign out"
            aria-label="Sign out"
            style={{
              flexShrink: 0,
              width: 32,
              height: 32,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'transparent',
              color: 'var(--text-tertiary)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              transition: 'all var(--duration-fast) var(--ease-in-out)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--accent-blood)'
              e.currentTarget.style.borderColor = 'rgba(227,64,88,0.4)'
              e.currentTarget.style.background = 'rgba(227,64,88,0.08)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-tertiary)'
              e.currentTarget.style.borderColor = 'var(--border-subtle)'
              e.currentTarget.style.background = 'transparent'
            }}
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </aside>
  )
}
