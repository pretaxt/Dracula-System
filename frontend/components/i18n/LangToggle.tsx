'use client'
import { useT } from './I18nProvider'

export default function LangToggle() {
  const { lang, toggle } = useT()
  // 显示对方语言 (点击切到对方)
  const label = lang === 'zh' ? 'EN' : '中'

  return (
    <button
      type="button"
      onClick={toggle}
      title="Switch language / 切换语言"
      aria-label="Switch language"
      style={{
        background: 'transparent',
        color: 'var(--text-secondary)',
        padding: '6px 10px',
        borderRadius: 'var(--radius-sm)',
        fontSize: 'var(--text-xs)',
        fontFamily: 'var(--font-mono)',
        border: '1px solid var(--border-strong)',
        cursor: 'pointer',
        minWidth: 38,
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
      {label}
    </button>
  )
}
