'use client'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { X, AlertTriangle, ShieldAlert, Activity } from 'lucide-react'
import { getRiskEvents } from '@/lib/api/risk'
import { useT } from '../i18n/I18nProvider'

const TIER_TONE: Record<string, string> = {
  TIER1: 'var(--accent-gold)',
  TIER2: 'var(--accent-blood)',
  TIER3: 'var(--accent-blood-bright)',
}

export default function NotificationDrawer({ onClose }: { onClose: () => void }) {
  const { t } = useT()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['risk-events', 7],
    queryFn: () => getRiskEvents(7),
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const events = data?.data ?? []

  return (
    <>
      <div className="notif-backdrop" onClick={onClose} aria-hidden />
      <aside className="notif-drawer" role="dialog" aria-label={t('通知中心')}>
        <header
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--border-default)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-deepest)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ShieldAlert size={18} style={{ color: 'var(--accent-blood)' }} />
            <h3
              style={{
                margin: 0,
                fontFamily: 'var(--font-display)',
                fontSize: 16,
                letterSpacing: '0.04em',
                color: 'var(--text-primary)',
              }}
            >
              {t('通知中心')}
            </h3>
            {events.length > 0 && (
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  padding: '2px 6px',
                  borderRadius: 'var(--radius-xs)',
                  background: 'rgba(227,64,88,0.15)',
                  color: 'var(--accent-blood)',
                }}
              >
                {events.length}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            type="button"
            aria-label={t('关闭')}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-tertiary)',
              padding: 4,
              display: 'inline-flex',
            }}
          >
            <X size={18} />
          </button>
        </header>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {isLoading && (
            <div
              style={{
                padding: 24,
                textAlign: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                color: 'var(--text-tertiary)',
              }}
            >
              {t('加载中…')}
            </div>
          )}
          {isError && (
            <div
              style={{
                padding: 24,
                textAlign: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                color: 'var(--accent-blood)',
              }}
            >
              {t('加载失败')}
            </div>
          )}
          {!isLoading && !isError && events.length === 0 && (
            <div style={{ padding: 32, textAlign: 'center' }}>
              <Activity size={28} style={{ color: 'var(--text-muted)', marginBottom: 8 }} />
              <div
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--text-tertiary)',
                }}
              >
                {t('近 7 日无风控事件')}
              </div>
            </div>
          )}
          {events.map((ev, i) => (
            <div
              key={i}
              style={{
                padding: '14px 20px',
                borderBottom: '1px solid var(--border-subtle)',
                display: 'flex',
                gap: 12,
              }}
            >
              <AlertTriangle
                size={14}
                style={{
                  color: TIER_TONE[ev.tier] || 'var(--text-tertiary)',
                  flexShrink: 0,
                  marginTop: 4,
                }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'baseline',
                    gap: 8,
                    marginBottom: 4,
                  }}
                >
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 10,
                      color: TIER_TONE[ev.tier] || 'var(--text-tertiary)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.08em',
                    }}
                  >
                    {ev.tier}
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: 10,
                      color: 'var(--text-muted)',
                    }}
                  >
                    {ev.time}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 13,
                    color: 'var(--text-primary)',
                    marginBottom: 4,
                    fontWeight: 500,
                  }}
                >
                  {ev.event}
                </div>
                <div
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--text-tertiary)',
                    lineHeight: 1.5,
                  }}
                >
                  {ev.trigger} → <span style={{ color: 'var(--text-secondary)' }}>{ev.value}</span>
                </div>
                <div
                  style={{
                    marginTop: 6,
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    color: ev.auto_recovered ? 'var(--accent-emerald)' : 'var(--text-tertiary)',
                  }}
                >
                  {ev.action}
                  {ev.auto_recovered ? ' · 已自动恢复' : ''}
                </div>
              </div>
            </div>
          ))}
        </div>

        <footer
          style={{
            padding: 12,
            borderTop: '1px solid var(--border-default)',
            textAlign: 'center',
          }}
        >
          <Link
            href="/risk"
            onClick={onClose}
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              color: 'var(--accent-blood)',
              textDecoration: 'none',
              letterSpacing: '0.04em',
            }}
          >
            {t('查看完整风控日志 →')}
          </Link>
        </footer>
      </aside>
    </>
  )
}
