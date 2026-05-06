'use client'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary } from '@/lib/api/dashboard'
import { getOpportunities } from '@/lib/api/funding'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

// Exchange badge colors — matches prototype v0.3
const EXCH_STYLE: Record<string, { bg: string; color: string }> = {
  binance:     { bg: 'rgba(240,185,11,0.12)',  color: '#f0b90b' },
  bybit:       { bg: 'rgba(247,165,1,0.12)',   color: '#f7a501' },
  okx:         { bg: 'rgba(74,158,255,0.12)',  color: '#4a9eff' },
  htx:         { bg: 'rgba(0,159,211,0.12)',   color: '#009fd3' },
  bitget:      { bg: 'rgba(0,246,196,0.12)',   color: '#00f6c4' },
  hyperliquid: { bg: 'rgba(177,108,255,0.15)', color: '#b16cff' },
  dydx:        { bg: 'rgba(108,92,231,0.15)',  color: '#6c5ce7' },
}

function ExchBadge({ name }: { name: string }) {
  const s = EXCH_STYLE[name.toLowerCase()] ?? { bg: 'rgba(255,255,255,0.06)', color: '#8b95a3' }
  return (
    <span style={{ display: 'inline-block', padding: '3px 8px', fontSize: '0.68rem', borderRadius: 3, fontWeight: 700, letterSpacing: '0.03em', fontFamily: 'var(--font-mono)', background: s.bg, color: s.color }}>
      {name.toUpperCase()}
    </span>
  )
}

function AprBar({ pct }: { pct: number }) {
  const width = Math.min(100, Math.max(0, pct / 50 * 100))
  const color = pct >= 20 ? 'var(--color-positive)' : pct >= 8 ? 'var(--color-warning)' : 'var(--color-text-dim)'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span style={{ color, fontWeight: 700, fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>{pct.toFixed(2)}%</span>
      <span style={{ display: 'inline-block', width: 56, height: 5, background: 'var(--color-border)', borderRadius: 2, overflow: 'hidden', verticalAlign: 'middle' }}>
        <span style={{ display: 'block', height: '100%', width: `${width}%`, background: 'linear-gradient(90deg, var(--color-accent), var(--color-blue))', borderRadius: 2 }} />
      </span>
    </span>
  )
}

function PanelHeader({ title, accent = 'green' }: { title: string; accent?: 'green' | 'blue' | 'yellow' }) {
  const barColor = accent === 'blue' ? 'var(--color-blue)' : accent === 'yellow' ? 'var(--color-warning)' : 'var(--color-accent)'
  return (
    <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', background: 'rgba(26,32,41,0.5)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: '0.8rem', fontWeight: 700, color: 'var(--color-text)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        <span style={{ display: 'inline-block', width: 3, height: 15, background: barColor, borderRadius: 2 }} />
        {title}
      </div>
    </div>
  )
}

interface KpiCardProps {
  label: string
  value: string
  meta?: string
  delta?: string
  deltaUp?: boolean
  accent?: 'green' | 'blue' | 'yellow' | 'default'
}

function KpiCard({ label, value, meta, delta, deltaUp, accent = 'default' }: KpiCardProps) {
  const valueColor =
    accent === 'green'  ? 'var(--color-positive)' :
    accent === 'yellow' ? 'var(--color-warning)'  :
    accent === 'blue'   ? 'var(--color-blue)'     : 'var(--color-text)'
  const topBar =
    accent === 'green'  ? 'linear-gradient(90deg, transparent, var(--color-accent), transparent)' :
    accent === 'yellow' ? 'linear-gradient(90deg, transparent, var(--color-warning), transparent)' :
    accent === 'blue'   ? 'linear-gradient(90deg, transparent, var(--color-blue), transparent)' :
                          'linear-gradient(90deg, transparent, var(--color-blue), transparent)'

  return (
    <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '18px 20px', position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: topBar, opacity: 0.7 }} />
      <div style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 10, fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '1.75rem', fontWeight: 600, letterSpacing: '-0.03em', lineHeight: 1.1, color: valueColor }}>{value}</div>
      {(delta || meta) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--color-text-muted)' }}>
          {delta && <span style={{ fontWeight: 600, color: deltaUp ? 'var(--color-positive)' : 'var(--color-negative)' }}>{deltaUp ? '+' : ''}{delta}</span>}
          {meta && <span>{meta}</span>}
        </div>
      )}
    </div>
  )
}

export default function DashboardPage() {
  const { data: summary, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: opps } = useQuery({ queryKey: ['opportunities'], queryFn: getOpportunities, refetchInterval: 15_000 })

  if (isLoading) return <div style={{ padding: '3rem', color: 'var(--color-text-dim)', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>Loading…</div>

  const netPnl     = parseFloat(summary?.net_pnl_usd      || '0')
  const todayFund  = parseFloat(summary?.today_funding_usd || '0')
  const avgApr     = parseFloat(summary?.avg_apr_pct       || '0')
  const openPos    = summary?.open_positions ?? 0
  const chartData  = (summary?.pnl_series_30d ?? []).map((p: { date: string; net_pnl_usd: string }) => ({
    date: p.date.slice(5),
    pnl:  parseFloat(p.net_pnl_usd),
  }))
  const oppList: Array<Record<string, string | number>> = opps?.data ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* 5 KPI cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
        <KpiCard label="账户净盈亏"   value={`$${netPnl.toFixed(2)}`}    accent={netPnl >= 0 ? 'green' : 'default'} delta={`$${Math.abs(netPnl).toFixed(2)}`} deltaUp={netPnl >= 0} meta="累计" />
        <KpiCard label="今日资金费收入" value={`$${todayFund.toFixed(4)}`}  accent="green"  meta="24H 收益" />
        <KpiCard label="开仓数量"     value={String(openPos)}              accent="blue"   meta="活跃持仓" />
        <KpiCard label="平均年化收益"  value={`${avgApr.toFixed(2)}%`}     accent="yellow" meta="加权均值 · 滚动 7 天" />
        <KpiCard label="今日最大回撤"  value="—"                            accent="default" meta="红线 -3.0% · 安全" />
      </div>

      {/* Chart + Top-6 opportunities */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
          <PanelHeader title="30 天净盈亏曲线" accent="blue" />
          <div style={{ padding: 16, height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <XAxis dataKey="date" tick={{ fill: '#5a6470', fontSize: 11, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#5a6470', fontSize: 11, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} width={52} />
                <Tooltip contentStyle={{ background: '#1a2029', border: '1px solid #2a3340', borderRadius: 6, color: '#e8ecef', fontFamily: 'JetBrains Mono', fontSize: 12 }} formatter={(v) => [`$${Number(v ?? 0).toFixed(4)}`, 'PnL']} />
                <Line type="monotone" dataKey="pnl" stroke="#00d68f" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
          <PanelHeader title="实时机会 TOP 6" accent="green" />
          {oppList.slice(0, 6).map((o) => (
            <div key={String(o.symbol)}
              style={{ padding: '12px 16px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', transition: 'background var(--duration-fast)', cursor: 'default' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-surface-high)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: '0.875rem', marginBottom: 4 }}>{String(o.symbol)}</div>
                <ExchBadge name={String(o.exchange)} />
              </div>
              <AprBar pct={parseFloat(String(o.apr_pct))} />
            </div>
          ))}
          {oppList.length === 0 && <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-muted)', fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>暂无数据</div>}
        </div>
      </div>

      {/* Full opportunity table */}
      <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
        <PanelHeader title="资金费率机会扫描器" accent="green" />
        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>
          <thead>
            <tr>
              {['币种', '交易所', '资金费率', '年化收益', '趋势'].map((h) => (
                <th key={h} style={{ padding: '11px 14px', textAlign: 'left', background: 'rgba(26,32,41,0.7)', color: 'var(--color-text-muted)', fontWeight: 600, fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.08em', borderBottom: '1px solid var(--color-border)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {oppList.map((o) => {
              const fr  = parseFloat(String(o.funding_rate)) * 100
              const apr = parseFloat(String(o.apr_pct))
              return (
                <tr key={String(o.symbol)}
                  style={{ borderBottom: '1px solid var(--color-border)', transition: 'background var(--duration-fast)', cursor: 'default' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-surface-high)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <td style={{ padding: '13px 14px', fontWeight: 600 }}>{String(o.symbol)}</td>
                  <td style={{ padding: '13px 14px' }}><ExchBadge name={String(o.exchange)} /></td>
                  <td style={{ padding: '13px 14px', color: fr >= 0 ? 'var(--color-positive)' : 'var(--color-negative)' }}>{fr >= 0 ? '+' : ''}{fr.toFixed(4)}%</td>
                  <td style={{ padding: '13px 14px' }}><AprBar pct={apr} /></td>
                  <td style={{ padding: '13px 14px', color: 'var(--color-text-dim)', fontSize: '0.8rem' }}>{o.history_positive ? `${o.history_positive}/10` : '—'}</td>
                </tr>
              )
            })}
            {oppList.length === 0 && <tr><td colSpan={5} style={{ padding: '2.5rem', textAlign: 'center', color: 'var(--color-text-muted)' }}>无数据</td></tr>}
          </tbody>
        </table>
      </div>

    </div>
  )
}
