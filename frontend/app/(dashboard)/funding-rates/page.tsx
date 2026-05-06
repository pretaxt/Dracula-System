'use client'
import { useQuery } from '@tanstack/react-query'
import { getOpportunities } from '@/lib/api/funding'

export default function FundingRatesPage() {
  const { data, isLoading, dataUpdatedAt } = useQuery({ queryKey: ['opportunities'], queryFn: getOpportunities, refetchInterval: 15_000 })

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.7rem', letterSpacing: '0.15em', marginBottom: '0.25rem' }}>LIVE</div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>Funding Rates</h1>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.7rem', color: 'var(--color-text-dim)' }}>
          Updated {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—'}
        </div>
      </div>

      <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
              {['Symbol', 'Exchange', 'Funding Rate', 'APR %', 'Type', 'History +'].map((h) => (
                <th key={h} style={{ padding: '0.75rem 1rem', textAlign: 'left', fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={6} style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-dim)' }}>Loading…</td></tr>}
            {(data?.data || []).map((o: Record<string, string | number>) => (
              <tr key={String(o.symbol)} style={{ borderBottom: '1px solid var(--color-border)' }}>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{String(o.symbol)}</td>
                <td style={{ padding: '0.75rem 1rem', fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>{String(o.exchange)}</td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>{(parseFloat(String(o.funding_rate)) * 100).toFixed(4)}%</td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.95rem', fontWeight: 700, color: parseFloat(String(o.apr_pct)) > 0 ? 'var(--color-positive)' : 'var(--color-negative)' }}>
                  {parseFloat(String(o.apr_pct)).toFixed(2)}%
                </td>
                <td style={{ padding: '0.75rem 1rem', fontSize: '0.75rem', color: 'var(--color-text-dim)' }}>{String(o.instrument_type)}</td>
                <td style={{ padding: '0.75rem 1rem', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>{o.history_positive ?? '—'}/10</td>
              </tr>
            ))}
            {!isLoading && data?.data?.length === 0 && (
              <tr><td colSpan={6} style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-dim)' }}>No opportunities</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
