import type { CSSProperties, ReactNode } from 'react'

type CardProps = {
  children: ReactNode
  style?: CSSProperties
  className?: string
  hoverable?: boolean
}

/**
 * 基础卡片 — 平面背景, 细边框
 */
export function Card({ children, style, className, hoverable = false }: CardProps) {
  return (
    <div
      className={className}
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius-md)',
        transition: hoverable ? 'border-color var(--duration-fast) var(--ease-in-out)' : undefined,
        ...style,
      }}
      onMouseEnter={
        hoverable
          ? (e) => {
              e.currentTarget.style.borderColor = 'var(--border-default)'
            }
          : undefined
      }
      onMouseLeave={
        hoverable
          ? (e) => {
              e.currentTarget.style.borderColor = 'var(--border-subtle)'
            }
          : undefined
      }
    >
      {children}
    </div>
  )
}

/**
 * 强调卡片 — 渐变背景 (深色) / 阴影 (浅色), 默认边框, inset 高光
 */
export function CardElevated({ children, style, className }: CardProps) {
  return (
    <div
      className={className}
      style={{
        background:
          'linear-gradient(180deg, var(--bg-card) 0%, var(--bg-base) 100%)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--shadow-card)',
        ...style,
      }}
    >
      {children}
    </div>
  )
}

type SectionHeaderProps = {
  title: string
  subtitle?: string
  right?: ReactNode
  accentLine?: boolean
}

/**
 * 区块标题 — display 字体 + 副标题 mono caps
 */
export function SectionHeader({ title, subtitle, right, accentLine = false }: SectionHeaderProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 16,
        position: 'relative',
        paddingTop: accentLine ? 12 : 0,
        borderTop: accentLine ? '1px solid var(--border-subtle)' : undefined,
      }}
    >
      {accentLine && (
        <span
          style={{
            position: 'absolute',
            top: -1,
            left: 0,
            width: 40,
            height: 1,
            background: 'var(--accent-blood)',
          }}
        />
      )}
      <div>
        <h3
          style={{
            margin: 0,
            fontFamily: 'var(--font-display)',
            fontSize: 'var(--text-lg)',
            fontWeight: 600,
            letterSpacing: '0.04em',
            color: 'var(--text-primary)',
          }}
        >
          {title}
        </h3>
        {subtitle && (
          <p
            style={{
              margin: '2px 0 0',
              fontFamily: 'var(--font-mono)',
              fontSize: 13,
              letterSpacing: '0.06em',
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
            }}
          >
            {subtitle}
          </p>
        )}
      </div>
      {right}
    </div>
  )
}
