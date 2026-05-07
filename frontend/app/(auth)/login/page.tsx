'use client'
import { login } from '@/lib/api/auth'
import { useAuthStore } from '@/lib/auth/token-store'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { Button } from '@/components/ui/Button'

export default function LoginPage() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const setToken = useAuthStore((s) => s.setToken)
  const router = useRouter()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const { access_token, expires_at } = await login('admin', password)
      setToken(access_token, expires_at)
      router.replace('/')
    } catch {
      setError('Invalid password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="animate-in"
        style={{
          width: 380,
          padding: 36,
          background: 'linear-gradient(180deg, var(--bg-card) 0%, var(--bg-base) 100%)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--shadow-elevated)',
          position: 'relative',
        }}
      >
        {/* Logo + 标题 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 32 }}>
          <div
            className="logo-svg"
            style={{
              width: 56,
              height: 56,
              flexShrink: 0,
              borderRadius: 'var(--radius-md)',
              overflow: 'hidden',
              boxShadow: '0 0 16px var(--accent-blood-glow)',
              background: 'linear-gradient(135deg, var(--accent-blood), var(--accent-blood-dim))',
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/dgl_icon_square.PNG"
              alt="Dracula"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
            />
          </div>
          <div>
            <h1
              style={{
                margin: 0,
                fontFamily: 'var(--font-display)',
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: '0.08em',
                color: 'var(--text-primary)',
                lineHeight: 1.1,
              }}
            >
              DRACULA
            </h1>
            <p
              style={{
                margin: '6px 0 0',
                fontFamily: 'var(--font-mono)',
                fontSize: 9,
                letterSpacing: '0.35em',
                color: 'var(--accent-blood)',
              }}
            >
              ARBITRAGE SYSTEM
            </p>
          </div>
        </div>

        {/* 密码输入 */}
        <div style={{ marginBottom: 20 }}>
          <label
            style={{
              display: 'block',
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.15em',
              textTransform: 'uppercase',
              color: 'var(--text-tertiary)',
              marginBottom: 8,
            }}
          >
            Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoFocus
            style={{
              width: '100%',
              padding: '10px 14px',
              background: 'var(--bg-deepest)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              fontSize: 14,
              fontFamily: 'var(--font-mono)',
              outline: 'none',
              boxSizing: 'border-box',
              transition: 'border-color var(--duration-fast)',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent-blood)'
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-default)'
            }}
          />
        </div>

        {error && (
          <div
            style={{
              color: 'var(--accent-blood)',
              fontSize: 12,
              fontFamily: 'var(--font-mono)',
              marginBottom: 16,
              padding: '8px 12px',
              background: 'rgba(227, 64, 88, 0.08)',
              border: '1px solid rgba(227, 64, 88, 0.2)',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            {error}
          </div>
        )}

        <Button type="submit" variant="primary" fullWidth disabled={loading}>
          {loading ? 'Signing in…' : 'Sign In'}
        </Button>
      </form>
    </div>
  )
}
