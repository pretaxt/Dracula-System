'use client'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getStrategyStatus, startStrategy, stopStrategy } from '@/lib/api/strategies'

export default function StrategiesPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['strategy'], queryFn: getStrategyStatus, refetchInterval: 10_000 })
  const startMut = useMutation({ mutationFn: startStrategy, onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }) })
  const stopMut = useMutation({ mutationFn: stopStrategy, onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }) })

  const running = data?.paper_running

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.7rem', letterSpacing: '0.15em', marginBottom: '0.25rem' }}>CONTROL</div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>Strategies</h1>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
        {/* Status card */}
        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '1.5rem' }}>
          <div style={{ fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em', marginBottom: '1rem' }}>FUNDING RATE STRATEGY</div>
          {isLoading ? <div style={{ color: 'var(--color-text-dim)' }}>Loading…</div> : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem' }}>
                <div style={{ width: 12, height: 12, borderRadius: '50%', background: running ? 'var(--color-positive)' : 'var(--color-text-muted)', boxShadow: running ? '0 0 8px var(--color-positive)' : 'none' }} />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.875rem', fontWeight: 700 }}>
                  {running ? 'RUNNING' : 'STOPPED'}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '0.75rem' }}>
                <button onClick={() => startMut.mutate()} disabled={running || startMut.isPending} style={{ flex: 1, padding: '0.625rem', background: 'var(--color-positive)', color: '#111', border: 'none', borderRadius: 'var(--radius-sm)', fontWeight: 700, cursor: running ? 'not-allowed' : 'pointer', opacity: running ? 0.4 : 1, fontSize: '0.875rem' }}>
                  Start
                </button>
                <button onClick={() => { if (confirm('Stop the strategy? Open positions will remain.')) stopMut.mutate() }} disabled={!running || stopMut.isPending} style={{ flex: 1, padding: '0.625rem', background: 'transparent', color: 'var(--color-negative)', border: '1px solid var(--color-negative)', borderRadius: 'var(--radius-sm)', fontWeight: 700, cursor: !running ? 'not-allowed' : 'pointer', opacity: !running ? 0.4 : 1, fontSize: '0.875rem' }}>
                  Stop
                </button>
              </div>
            </>
          )}
        </div>

        {/* Config card */}
        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '1.5rem' }}>
          <div style={{ fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em', marginBottom: '1rem' }}>CURRENT CONFIG</div>
          {data?.current_config && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {Object.entries(data.current_config).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.875rem' }}>
                  <span style={{ color: 'var(--color-text-dim)' }}>{k.replace(/_/g, ' ')}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{String(v)}</span>
                </div>
              ))}
            </div>
          )}
          {data?.last_scan_at && (
            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--color-border)', fontSize: '0.75rem', color: 'var(--color-text-dim)', fontFamily: 'var(--font-mono)' }}>
              Last scan: {new Date(data.last_scan_at).toLocaleTimeString()}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
