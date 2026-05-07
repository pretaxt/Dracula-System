'use client'
import { useEffect, type ReactNode } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { Button } from './Button'

type ConfirmDialogProps = {
  open: boolean
  title: string
  message: ReactNode
  confirmText?: string
  cancelText?: string
  tone?: 'default' | 'danger'
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  tone = 'default',
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !loading) onCancel()
    }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [open, loading, onCancel])

  if (!open) return null

  const accentColor = tone === 'danger' ? 'var(--accent-blood)' : 'var(--accent-azure)'
  const accentBg    = tone === 'danger' ? 'rgba(227,64,88,0.10)' : 'rgba(95,176,255,0.10)'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0, 0, 0, 0.6)',
        backdropFilter: 'blur(4px)',
        WebkitBackdropFilter: 'blur(4px)',
        animation: 'slide-in-up 0.2s var(--ease-out-expo)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) onCancel()
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 420,
          margin: 24,
          background: 'linear-gradient(180deg, var(--bg-card) 0%, var(--bg-base) 100%)',
          border: `1px solid ${tone === 'danger' ? 'var(--accent-blood)' : 'var(--border-default)'}`,
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--shadow-elevated)',
          padding: 24,
          position: 'relative',
        }}
      >
        <button
          type="button"
          onClick={onCancel}
          disabled={loading}
          aria-label="Close"
          style={{
            position: 'absolute',
            top: 12,
            right: 12,
            background: 'transparent',
            border: 'none',
            color: 'var(--text-tertiary)',
            cursor: loading ? 'not-allowed' : 'pointer',
            padding: 4,
            display: 'inline-flex',
          }}
        >
          <X size={16} />
        </button>

        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
          {tone === 'danger' && (
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 'var(--radius-sm)',
                background: accentBg,
                color: accentColor,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <AlertTriangle size={18} />
            </div>
          )}
          <h3
            id="confirm-title"
            style={{
              margin: 0,
              fontFamily: 'var(--font-display)',
              fontSize: 18,
              fontWeight: 600,
              letterSpacing: '0.04em',
              color: 'var(--text-primary)',
              flex: 1,
              paddingTop: tone === 'danger' ? 6 : 0,
            }}
          >
            {title}
          </h3>
        </div>

        <div
          style={{
            fontSize: 13,
            lineHeight: 1.6,
            color: 'var(--text-secondary)',
            marginBottom: 20,
            whiteSpace: 'pre-wrap',
          }}
        >
          {message}
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            variant="primary"
            onClick={onConfirm}
            disabled={loading}
            style={
              tone === 'danger'
                ? undefined
                : { background: 'var(--accent-azure)', borderColor: 'var(--accent-azure)' }
            }
          >
            {loading ? '处理中…' : confirmText}
          </Button>
        </div>
      </div>
    </div>
  )
}
