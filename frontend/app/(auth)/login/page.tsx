'use client'
import { login } from '@/lib/api/auth'
import { useAuthStore } from '@/lib/auth/token-store'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

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
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--color-bg)' }}>
      <form onSubmit={handleSubmit} style={{ width: 360, padding: '2.5rem', background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)' }}>
        <div style={{ marginBottom: '2rem' }}>
          <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.75rem', letterSpacing: '0.15em', marginBottom: '0.5rem' }}>DRACULA SYSTEM</div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--color-text)' }}>Admin Login</h1>
        </div>

        <div style={{ marginBottom: '1.25rem' }}>
          <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--color-text-dim)', marginBottom: '0.5rem', letterSpacing: '0.05em' }}>PASSWORD</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ width: '100%', padding: '0.625rem 0.875rem', background: 'var(--color-surface-elev)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)', color: 'var(--color-text)', fontSize: '0.875rem', outline: 'none', boxSizing: 'border-box' }}
          />
        </div>

        {error && <div style={{ color: 'var(--color-negative)', fontSize: '0.8rem', marginBottom: '1rem' }}>{error}</div>}

        <button
          type="submit"
          disabled={loading}
          style={{ width: '100%', padding: '0.625rem', background: 'var(--color-accent)', color: '#111', fontWeight: 700, border: 'none', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontSize: '0.875rem', opacity: loading ? 0.7 : 1 }}
        >
          {loading ? 'Signing in…' : 'Sign In'}
        </button>
      </form>
    </div>
  )
}
