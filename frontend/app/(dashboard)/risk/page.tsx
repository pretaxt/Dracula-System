'use client'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getRiskLimits, patchRiskLimits } from '@/lib/api/risk'
import { useState } from 'react'

export default function RiskPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['risk'], queryFn: getRiskLimits })
  const [editing, setEditing] = useState<Record<string, string>>({})
  const [confirmWidening, setConfirmWidening] = useState(false)
  const patchMut = useMutation({ mutationFn: (p: Record<string, unknown>) => patchRiskLimits(p, confirmWidening), onSuccess: () => { qc.invalidateQueries({ queryKey: ['risk'] }); setEditing({}); setConfirmWidening(false) } })

  if (isLoading) return <div style={{ color: 'var(--color-text-dim)' }}>Loading…</div>

  const limits = data || {}
  const fields = Object.entries(limits) as [string, string][]

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.7rem', letterSpacing: '0.15em', marginBottom: '0.25rem' }}>SAFETY</div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>Risk Limits</h1>
      </div>

      <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '1.5rem', maxWidth: 560 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {fields.map(([k, v]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
              <div style={{ flex: 1, fontSize: '0.875rem', color: 'var(--color-text-dim)' }}>{k.replace(/_/g, ' ')}</div>
              <input
                value={editing[k] ?? String(v)}
                onChange={(e) => setEditing((prev) => ({ ...prev, [k]: e.target.value }))}
                style={{ width: 140, padding: '0.375rem 0.625rem', background: 'var(--color-surface-elev)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)', color: 'var(--color-text)', fontFamily: 'var(--font-mono)', fontSize: '0.875rem', textAlign: 'right' }}
              />
            </div>
          ))}
        </div>

        <div style={{ marginTop: '1.5rem', paddingTop: '1rem', borderTop: '1px solid var(--color-border)' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8rem', color: 'var(--color-warning)', marginBottom: '1rem', cursor: 'pointer' }}>
            <input type="checkbox" checked={confirmWidening} onChange={(e) => setConfirmWidening(e.target.checked)} />
            I confirm I am widening risk limits
          </label>
          <button onClick={() => patchMut.mutate(editing)} disabled={patchMut.isPending || Object.keys(editing).length === 0} style={{ padding: '0.625rem 1.25rem', background: 'var(--color-accent)', color: '#111', border: 'none', borderRadius: 'var(--radius-sm)', fontWeight: 700, cursor: 'pointer', fontSize: '0.875rem', opacity: Object.keys(editing).length === 0 ? 0.5 : 1 }}>
            {patchMut.isPending ? 'Saving…' : 'Save Changes'}
          </button>
          {patchMut.isError && <div style={{ color: 'var(--color-negative)', fontSize: '0.8rem', marginTop: '0.5rem' }}>{String((patchMut.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Error saving')}</div>}
        </div>
      </div>
    </div>
  )
}
