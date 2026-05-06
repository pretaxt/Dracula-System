'use client'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary } from '@/lib/api/dashboard'
import { getOpportunities } from '@/lib/api/funding'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

function KpiCard({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean }) {
  const color = positive === true ? 'var(--color-positive)' : positive === false ? 'var(--color-negative)' : 'var(--color-text)'
  return (
    <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '1.25rem 1.5rem' }}>
      <div style={{ fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em', marginBottom: '0.5rem' }}>{label}</div>
      <div style={{ fontSize: '1.75rem', fontFamily: 'var(--font-mono)', fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: '0.75rem', color: 'var(--color-text-dim)', marginTop: '0.4rem' }}>{sub}</div>}
    </div>
  )
}

export default function DashboardPage() {
  const { data: summary, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: opps } = useQuery({ queryKey: ['opportunities'], queryFn: getOpportunities, refetchInterval: 15_000 })

  if (isLoading) return <div style={{ color: 'var(--color-text-dim)' }}>Loading…</div>

  const netPnl = parseFloat(summary?.net_pnl_usd || '0')
  const chartData = summary?.pnl_series_30d?.map((p: { date: string; net_pnl_usd: string }) => ({ date: p.date.slice(5), pnl: parseFloat(p.net_pnl_usd) })) || []

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <div style={{ color: 'var(--color-accent)', fontFamily: 'var(--font-mono)', fontSize: '0.7rem', letterSpacing: '0.15em', marginBottom: '0.25rem' }}>OVERVIEW</div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--color-text)' }}>Dashboard</h1>
      </div>

      {/* KPI Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '2rem' }}>
        <KpiCard label="NET PNL" value={`$${parseFloat(summary?.net_pnl_usd || '0').toFixed(2)}`} positive={netPnl >= 0} />
        <KpiCard label="TODAY FUNDING" value={`$${parseFloat(summary?.today_funding_usd || '0').toFixed(4)}`} positive />
        <KpiCard label="OPEN POSITIONS" value={summary?.open_positions ?? '—'} />
        <KpiCard label="AVG APR" value={`${parseFloat(summary?.avg_apr_pct || '0').toFixed(2)}%`} sub="across open positions" />
      </div>

      {/* PnL Chart + Opportunities */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '1rem' }}>
        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '1.5rem' }}>
          <div style={{ fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em', marginBottom: '1.25rem' }}>30-DAY PNL</div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData}>
              <XAxis dataKey="date" tick={{ fill: 'var(--color-text-dim)', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'var(--color-text-dim)', fontSize: 11 }} axisLine={false} tickLine={false} width={50} />
              <Tooltip contentStyle={{ background: 'var(--color-surface-elev)', border: '1px solid var(--color-border)', borderRadius: 6, color: 'var(--color-text)' }} />
              <Line type="monotone" dataKey="pnl" stroke="var(--color-accent)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '1.5rem' }}>
          <div style={{ fontSize: '0.7rem', color: 'var(--color-text-dim)', letterSpacing: '0.1em', marginBottom: '1rem' }}>LIVE OPPORTUNITIES</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {(opps?.data || []).slice(0, 6).map((o: { symbol: string; apr_pct: string; exchange: string }) => (
              <div key={o.symbol} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.5rem 0.625rem', background: 'var(--color-surface-elev)', borderRadius: 'var(--radius-sm)' }}>
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', fontWeight: 600 }}>{o.symbol}</div>
                  <div style={{ fontSize: '0.65rem', color: 'var(--color-text-dim)' }}>{o.exchange}</div>
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: 'var(--color-positive)', fontWeight: 700 }}>
                  {parseFloat(o.apr_pct).toFixed(2)}%
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
