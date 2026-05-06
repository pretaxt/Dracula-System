'use client'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getPositions, closePosition } from '@/lib/api/positions'
import { useState } from 'react'

const STATUS_COLOR: Record<string, string> = {
  open: 'var(--color-positive)',
  closed: 'var(--color-text-dim)',
  closing: 'var(--color-warning)',
}

export default function PositionsPage() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<string | undefined>(undefined)
  const { data, isLoading } = useQuery({ queryKey: ['positions', filter], queryFn: () => getPositions({ status: filter }), refetchInterval: 15_000 })
  const closeMut = useMutation({ mutationFn: (uuid: string) => closePosition(uuid), onSuccess: () => qc.invalidateQueries({ queryKey: ['positions'] }) })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.7rem', letterSpacing: '0.15em', marginBottom: '0.25rem' }}>MANAGEMENT</div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>Positions</h1>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {['', 'open', 'closed'].map((s) => (
            <button key={s} onClick={() => setFilter(s || undefined)} style={{ padding: '0.375rem 0.875rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-border)', background: filter === (s || undefined) ? 'var(--color-accent)' : 'transparent', color: filter === (s || undefined) ? '#111' : 'var(--color-text-dim)', fontSize: '0.8rem', cursor: 'pointer', fontWeight: 600 }}>
              {s || 'ALL'}
            </button>
          ))}
        </div>
      </div>

      <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
              {['Symbol', 'Status', 'Notional', 'APR %', 'Funding', 'PnL', 'Days', 'Action'].map((h) => (
                <th key={h} style={{ padding: '0.75rem 1rem', textAlign: 'left', fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em', fontWeight: 600 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={8} style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-dim)' }}>Loading…</td></tr>}
            {(data?.data || []).map((p: Record<string, string>) => (
              <tr key={p.uuid} style={{ borderBottom: '1px solid var(--color-border)', transition: 'background var(--duration-fast)' }}>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: '0.875rem' }}>{p.symbol}</td>
                <td style={{ padding: '0.75rem 1rem' }}>
                  <span style={{ color: STATUS_COLOR[p.status] || 'inherit', fontSize: '0.8rem', fontWeight: 600 }}>{p.status.toUpperCase()}</span>
                </td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>${parseFloat(p.notional_usd).toFixed(0)}</td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', color: 'var(--color-positive)', fontSize: '0.875rem' }}>{p.target_apr_pct ? `${parseFloat(p.target_apr_pct).toFixed(2)}%` : '—'}</td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>${parseFloat(p.funding_received).toFixed(4)}</td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.875rem', color: parseFloat(p.realized_pnl) + parseFloat(p.unrealized_pnl) >= 0 ? 'var(--color-positive)' : 'var(--color-negative)' }}>
                  ${(parseFloat(p.realized_pnl) + parseFloat(p.unrealized_pnl)).toFixed(4)}
                </td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>{parseFloat(p.days_held).toFixed(1)}d</td>
                <td style={{ padding: '0.75rem 1rem' }}>
                  {p.status === 'open' && (
                    <button onClick={() => { if (confirm('Close this position?')) closeMut.mutate(p.uuid) }} style={{ padding: '0.25rem 0.625rem', background: 'transparent', border: '1px solid var(--color-negative)', borderRadius: 'var(--radius-sm)', color: 'var(--color-negative)', fontSize: '0.75rem', cursor: 'pointer' }}>
                      Close
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!isLoading && data?.data?.length === 0 && (
              <tr><td colSpan={8} style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-dim)' }}>No positions found</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {data?.meta && (
        <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--color-text-dim)', fontFamily: 'var(--font-mono)' }}>
          {data.meta.total} total · page {data.meta.page}
        </div>
      )}
    </div>
  )
}
