'use client'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Menu, Power } from 'lucide-react'
import { useT } from '../i18n/I18nProvider'
import { StatusDot, type StatusTone } from '../ui/Button'
import ThemeToggle from '../theme/ThemeToggle'
import LangToggle from '../i18n/LangToggle'
import { stopStrategy } from '@/lib/api/strategies'
import { getExchangeHealth, type ExchangeHealth } from '@/lib/api/system'

const TITLE_MAP: Record<string, string> = {
  '/':              '总览',
  '/strategies':    '策略中心',
  '/positions':     '持仓与订单',
  '/risk':          '风控中心',
  '/settings':      '设置',
  '/funding-rates': '机会扫描',
}

const EXCHANGE_DISPLAY_NAMES: Record<string, string> = {
  binance: 'Binance',
  bybit: 'Bybit',
  okx: 'OKX',
  htx: 'HTX',
  bitget: 'Bitget',
  hyperliquid: 'Hyperliquid',
}

const FALLBACK_EXCHANGES: ExchangeHealth[] = [
  { name: 'binance',     status: 'unconfigured', ping_ms: null },
  { name: 'bybit',       status: 'unconfigured', ping_ms: null },
  { name: 'okx',         status: 'unconfigured', ping_ms: null },
  { name: 'htx',         status: 'unconfigured', ping_ms: null },
  { name: 'bitget',      status: 'unconfigured', ping_ms: null },
  { name: 'hyperliquid', status: 'unconfigured', ping_ms: null },
]

const STATUS_TO_TONE: Record<ExchangeHealth['status'], StatusTone> = {
  active: 'active',
  warn: 'warn',
  critical: 'critical',
  unconfigured: 'paused',
}

function UtcClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () => {
      const utc = new Date().toISOString().substring(11, 19)
      setTime(`${utc} UTC`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>{time}</span>
  )
}

type TopBarProps = {
  onMenuClick?: () => void
  onNotifClick?: () => void
}

export default function TopBar({ onMenuClick, onNotifClick }: TopBarProps = {}) {
  const pathname = usePathname()
  const { t } = useT()
  const qc = useQueryClient()
  const stopMut = useMutation({
    mutationFn: stopStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }),
  })
  const { data: healthData } = useQuery({
    queryKey: ['exchange-health'],
    queryFn: getExchangeHealth,
    refetchInterval: 60_000,
  })
  const exchanges: ExchangeHealth[] = healthData?.data ?? FALLBACK_EXCHANGES

  const handleEmergencyStop = () => {
    if (confirm('⚠️ 紧急停止 EMERGENCY STOP\n\n确认要立即停止所有运行中的策略？\n现有持仓不会自动平仓。')) {
      stopMut.mutate()
    }
  }

  const titleZh =
    TITLE_MAP[pathname] ??
    Object.entries(TITLE_MAP).find(
      ([k]) => k !== '/' && pathname.startsWith(k),
    )?.[1] ??
    '总览'

  return (
    <>
      <div
        style={{
          height: 2,
          background:
            'linear-gradient(90deg, transparent 0%, var(--accent-blood) 30%, var(--accent-blood) 70%, transparent 100%)',
          opacity: 0.6,
        }}
      />
      <header
        style={{
          height: 'var(--topbar-height)',
          borderBottom: '1px solid var(--border-default)',
          background: 'var(--bg-deepest)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 24px',
          position: 'sticky',
          top: 0,
          zIndex: 9,
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, minWidth: 0, flex: 1 }}>
          <button
            type="button"
            className="topbar-menu-btn"
            onClick={onMenuClick}
            aria-label={t('打开导航')}
            style={{
              display: 'none',
              alignItems: 'center',
              justifyContent: 'center',
              width: 36,
              height: 36,
              background: 'transparent',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            <Menu size={18} />
          </button>
          <h2
            className="topbar-title"
            style={{
              margin: 0,
              fontFamily: 'var(--font-display)',
              fontSize: 'var(--text-2xl)',
              fontWeight: 600,
              letterSpacing: '0.04em',
              color: 'var(--text-primary)',
              whiteSpace: 'nowrap',
            }}
          >
            {t(titleZh)}
          </h2>
          <div
            className="topbar-exchanges"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 16,
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              color: 'var(--text-tertiary)',
              flexWrap: 'wrap',
            }}
          >
            {exchanges.map((ex) => {
              const display = EXCHANGE_DISPLAY_NAMES[ex.name] ?? ex.name
              const tone = STATUS_TO_TONE[ex.status] ?? 'paused'
              const tooltip =
                ex.ping_ms !== null
                  ? `${display}: ${ex.ping_ms}ms (${ex.status})`
                  : `${display}: ${ex.status}`
              return (
                <span
                  key={ex.name}
                  title={tooltip}
                  style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                >
                  <StatusDot tone={tone} />
                  <span>{display}</span>
                </span>
              )
            })}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
          <span className="topbar-clock"><UtcClock /></span>
          <ThemeToggle />
          <LangToggle />

          <button
            type="button"
            onClick={handleEmergencyStop}
            disabled={stopMut.isPending}
            title="紧急停止所有策略 / Emergency stop"
            style={{
              background: 'rgba(227, 64, 88, 0.10)',
              color: 'var(--accent-blood)',
              padding: '6px 12px',
              borderRadius: 'var(--radius-sm)',
              fontSize: 11,
              fontFamily: 'var(--font-mono)',
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              border: '1px solid var(--accent-blood)',
              cursor: stopMut.isPending ? 'not-allowed' : 'pointer',
              opacity: stopMut.isPending ? 0.5 : 1,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              transition: 'all var(--duration-fast)',
            }}
            onMouseEnter={(e) => {
              if (!stopMut.isPending) {
                e.currentTarget.style.background = 'var(--accent-blood)'
                e.currentTarget.style.color = '#fff'
                e.currentTarget.style.boxShadow = '0 0 16px var(--accent-blood-glow)'
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(227, 64, 88, 0.10)'
              e.currentTarget.style.color = 'var(--accent-blood)'
              e.currentTarget.style.boxShadow = 'none'
            }}
          >
            <Power size={12} />
            <span className="topbar-emergency-label">EMERGENCY STOP</span>
          </button>

          <button
            type="button"
            onClick={onNotifClick}
            title="通知 / Notifications"
            style={{
              background: 'transparent',
              color: 'var(--text-secondary)',
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
              fontSize: 12,
              border: '1px solid var(--border-strong)',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              transition: 'all var(--duration-fast)',
            }}
          >
            <Bell size={14} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>3</span>
          </button>
        </div>
      </header>
    </>
  )
}
