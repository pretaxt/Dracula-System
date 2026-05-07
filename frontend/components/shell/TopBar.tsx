'use client'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Bell } from 'lucide-react'
import { useT } from '../i18n/I18nProvider'
import { StatusDot, type StatusTone } from '../ui/Button'
import ThemeToggle from '../theme/ThemeToggle'
import LangToggle from '../i18n/LangToggle'

const TITLE_MAP: Record<string, string> = {
  '/':              '总览',
  '/strategies':    '策略中心',
  '/positions':     '持仓与订单',
  '/risk':          '风控中心',
  '/settings':      '设置',
  '/funding-rates': '机会扫描',
}

const EXCHANGES: { name: string; tone: StatusTone }[] = [
  { name: 'Binance',     tone: 'active' },
  { name: 'Bybit',       tone: 'active' },
  { name: 'OKX',         tone: 'active' },
  { name: 'HTX',         tone: 'warn' },
  { name: 'Bitget',      tone: 'active' },
  { name: 'Hyperliquid', tone: 'active' },
]

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

export default function TopBar() {
  const pathname = usePathname()
  const { t } = useT()

  // 当前页面标题 — 优先精确匹配, 其次找以 pathname 开头的 key
  const titleZh =
    TITLE_MAP[pathname] ??
    Object.entries(TITLE_MAP).find(
      ([k]) => k !== '/' && pathname.startsWith(k),
    )?.[1] ??
    '总览'

  return (
    <>
      {/* 顶部血色细线 */}
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
        }}
      >
        {/* 左: 页面标题 + 交易所状态 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
          <h2
            style={{
              margin: 0,
              fontFamily: 'var(--font-display)',
              fontSize: 'var(--text-2xl)',
              fontWeight: 600,
              letterSpacing: '0.04em',
              color: 'var(--text-primary)',
            }}
          >
            {t(titleZh)}
          </h2>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 16,
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              color: 'var(--text-tertiary)',
            }}
          >
            {EXCHANGES.map((ex) => (
              <span key={ex.name} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <StatusDot tone={ex.tone} />
                <span>{ex.name}</span>
              </span>
            ))}
          </div>
        </div>

        {/* 右: 时钟 + 主题 + 语言 + 通知 + 退出 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <UtcClock />
          <ThemeToggle />
          <LangToggle />

          <button
            type="button"
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
