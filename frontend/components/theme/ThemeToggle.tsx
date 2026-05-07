'use client'
import { Moon, Sun } from 'lucide-react'
import { useTheme } from './ThemeProvider'

export default function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const Icon = theme === 'dark' ? Moon : Sun

  return (
    <button
      type="button"
      onClick={toggle}
      title="切换日夜模式 / Toggle theme"
      aria-label="Toggle theme"
      style={{
        background: 'transparent',
        color: 'var(--text-secondary)',
        padding: '6px 10px',
        borderRadius: 'var(--radius-sm)',
        fontSize: 'var(--text-xs)',
        border: '1px solid var(--border-strong)',
        cursor: 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        transition: 'all var(--duration-fast) var(--ease-in-out)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = 'var(--text-primary)'
        e.currentTarget.style.borderColor = 'var(--text-tertiary)'
        e.currentTarget.style.background = 'var(--bg-card)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = 'var(--text-secondary)'
        e.currentTarget.style.borderColor = 'var(--border-strong)'
        e.currentTarget.style.background = 'transparent'
      }}
    >
      <Icon size={14} />
    </button>
  )
}
