import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react'

// ============================================================
// Button
// ============================================================

type ButtonVariant = 'primary' | 'secondary'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  fullWidth?: boolean
}

const buttonBase: CSSProperties = {
  padding: '8px 16px',
  borderRadius: 'var(--radius-sm)',
  fontSize: 13,
  fontWeight: 500,
  cursor: 'pointer',
  transition: 'all var(--duration-fast) var(--ease-in-out)',
  fontFamily: 'var(--font-sans)',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
}

export function Button({
  variant = 'secondary',
  fullWidth,
  style,
  disabled,
  ...rest
}: ButtonProps) {
  const variantStyle: CSSProperties =
    variant === 'primary'
      ? {
          background: 'var(--accent-blood)',
          color: '#fff',
          border: '1px solid var(--accent-blood)',
        }
      : {
          background: 'transparent',
          color: 'var(--text-secondary)',
          border: '1px solid var(--border-strong)',
        }

  return (
    <button
      {...rest}
      disabled={disabled}
      style={{
        ...buttonBase,
        ...variantStyle,
        width: fullWidth ? '100%' : undefined,
        opacity: disabled ? 0.4 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
        ...style,
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        if (variant === 'primary') {
          e.currentTarget.style.background = 'var(--accent-blood-dim)'
          e.currentTarget.style.boxShadow = '0 0 24px var(--accent-blood-glow)'
        } else {
          e.currentTarget.style.color = 'var(--text-primary)'
          e.currentTarget.style.borderColor = 'var(--text-tertiary)'
          e.currentTarget.style.background = 'var(--bg-card)'
        }
      }}
      onMouseLeave={(e) => {
        if (variant === 'primary') {
          e.currentTarget.style.background = 'var(--accent-blood)'
          e.currentTarget.style.boxShadow = 'none'
        } else {
          e.currentTarget.style.color = 'var(--text-secondary)'
          e.currentTarget.style.borderColor = 'var(--border-strong)'
          e.currentTarget.style.background = 'transparent'
        }
      }}
    />
  )
}

// ============================================================
// Badge
// ============================================================

export type BadgeTone = 'active' | 'warn' | 'critical' | 'paused' | 'info'

const BADGE_STYLES: Record<BadgeTone, { bg: string; color: string; border: string }> = {
  active:   { bg: 'rgba(61, 220, 151, 0.12)',  color: 'var(--accent-emerald)', border: 'rgba(61, 220, 151, 0.3)' },
  warn:     { bg: 'rgba(240, 184, 80, 0.12)',  color: 'var(--accent-gold)',    border: 'rgba(240, 184, 80, 0.3)' },
  critical: { bg: 'rgba(227, 64, 88, 0.12)',   color: 'var(--accent-blood)',   border: 'rgba(227, 64, 88, 0.3)' },
  paused:   { bg: 'rgba(108, 108, 133, 0.12)', color: 'var(--text-tertiary)',  border: 'var(--border-strong)' },
  info:     { bg: 'rgba(95, 176, 255, 0.12)',  color: 'var(--accent-azure)',   border: 'rgba(95, 176, 255, 0.3)' },
}

export function Badge({ tone = 'info', children, style }: { tone?: BadgeTone; children: ReactNode; style?: CSSProperties }) {
  const s = BADGE_STYLES[tone]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 8px',
        borderRadius: 'var(--radius-sm)',
        fontSize: 10,
        fontWeight: 500,
        letterSpacing: '0.05em',
        textTransform: 'uppercase',
        fontFamily: 'var(--font-mono)',
        background: s.bg,
        color: s.color,
        border: `1px solid ${s.border}`,
        ...style,
      }}
    >
      {children}
    </span>
  )
}

// ============================================================
// StatusDot
// ============================================================

export type StatusTone = 'active' | 'warn' | 'critical' | 'paused'

const DOT_COLORS: Record<StatusTone, string> = {
  active:   'var(--status-active)',
  warn:     'var(--status-warn)',
  critical: 'var(--status-critical)',
  paused:   'var(--status-paused)',
}

export function StatusDot({ tone = 'active', size = 6 }: { tone?: StatusTone; size?: number }) {
  const color = DOT_COLORS[tone]
  return (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        background: color,
        boxShadow: tone === 'paused' ? undefined : `0 0 8px ${color}`,
        animation: tone === 'critical' ? 'pulse-blood 1.5s infinite' : undefined,
        flexShrink: 0,
      }}
    />
  )
}
