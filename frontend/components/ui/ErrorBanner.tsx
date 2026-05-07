'use client'
import { AlertOctagon, RefreshCw } from 'lucide-react'

type ErrorLike = unknown

function extractMessage(err: ErrorLike): string {
  if (!err) return '未知错误'
  if (typeof err === 'string') return err
  const e = err as { response?: { data?: { detail?: string } }; message?: string; code?: string }
  return e.response?.data?.detail || e.message || e.code || '请求失败'
}

type ErrorBannerProps = {
  message?: ErrorLike
  onRetry?: () => void
  compact?: boolean
}

/**
 * API 错误友好横幅 — 在 isError 分支顶部渲染
 */
export function ErrorBanner({ message, onRetry, compact = false }: ErrorBannerProps) {
  const text = extractMessage(message)
  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        padding: compact ? '8px 12px' : '12px 16px',
        background: 'rgba(227, 64, 88, 0.08)',
        border: '1px solid rgba(227, 64, 88, 0.3)',
        borderRadius: 'var(--radius-sm)',
        color: 'var(--accent-blood)',
        fontSize: 13,
      }}
    >
      <AlertOctagon size={compact ? 14 : 16} style={{ marginTop: 2, flexShrink: 0 }} />
      <div style={{ flex: 1 }}>
        <div style={{ color: 'var(--text-primary)', fontWeight: 500, marginBottom: 2 }}>API 请求失败</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-blood)' }}>{text}</div>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          title="重试 / Retry"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '4px 10px',
            background: 'transparent',
            color: 'var(--accent-blood)',
            border: '1px solid rgba(227,64,88,0.4)',
            borderRadius: 'var(--radius-sm)',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            cursor: 'pointer',
            transition: 'all var(--duration-fast)',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(227,64,88,0.12)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        >
          <RefreshCw size={11} />
          <span>重试</span>
        </button>
      )}
    </div>
  )
}
