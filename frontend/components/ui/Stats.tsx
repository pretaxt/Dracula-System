import type { CSSProperties, ReactNode } from 'react'

// ============================================================
// ProgressBar
// ============================================================

export type ProgressTone = 'default' | 'success' | 'warn' | 'blood'

const PROGRESS_FILL: Record<ProgressTone, string> = {
  default: 'linear-gradient(90deg, var(--accent-blood) 0%, var(--accent-blood-dim) 100%)',
  success: 'linear-gradient(90deg, var(--accent-emerald) 0%, #2cb87b 100%)',
  warn:    'linear-gradient(90deg, var(--accent-gold) 0%, #c79238 100%)',
  blood:   'linear-gradient(90deg, var(--accent-blood) 0%, var(--accent-blood-dim) 100%)',
}

export function ProgressBar({
  pct,
  tone = 'default',
  height = 4,
  style,
}: {
  pct: number
  tone?: ProgressTone
  height?: number
  style?: CSSProperties
}) {
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <div
      style={{
        height,
        background: 'var(--border-subtle)',
        borderRadius: 2,
        overflow: 'hidden',
        ...style,
      }}
    >
      <div
        style={{
          height: '100%',
          width: `${clamped}%`,
          background: PROGRESS_FILL[tone],
          transition: 'width var(--duration-normal) var(--ease-out-expo)',
        }}
      />
    </div>
  )
}

// ============================================================
// KPICard
// ============================================================

export type KpiAccent = 'default' | 'positive' | 'negative' | 'warn' | 'info'

const KPI_VALUE_COLOR: Record<KpiAccent, string> = {
  default:  'var(--text-primary)',
  positive: 'var(--accent-emerald)',
  negative: 'var(--accent-blood)',
  warn:     'var(--accent-gold)',
  info:     'var(--accent-azure)',
}

type KPICardProps = {
  label: string
  value: ReactNode
  meta?: ReactNode
  icon?: ReactNode
  accent?: KpiAccent
  footer?: ReactNode
  style?: CSSProperties
  animationDelay?: string
}

export function KPICard({
  label,
  value,
  meta,
  icon,
  accent = 'default',
  footer,
  style,
  animationDelay,
}: KPICardProps) {
  return (
    <div
      className="animate-in"
      style={{
        background: 'linear-gradient(180deg, var(--bg-card) 0%, var(--bg-base) 100%)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-md)',
        padding: '18px 20px',
        boxShadow: 'var(--shadow-card)',
        animationDelay,
        ...style,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 8,
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: 'var(--text-tertiary)',
          }}
        >
          {label}
        </span>
        {icon && <span style={{ color: 'var(--text-muted)' }}>{icon}</span>}
      </div>
      <div
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-3xl)',
          fontWeight: 500,
          letterSpacing: '-0.02em',
          lineHeight: 1.1,
          color: KPI_VALUE_COLOR[accent],
        }}
      >
        {value}
      </div>
      {meta && (
        <div
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--text-xs)',
            marginTop: 6,
            color: 'var(--text-tertiary)',
          }}
        >
          {meta}
        </div>
      )}
      {footer && <div style={{ marginTop: 10 }}>{footer}</div>}
    </div>
  )
}
