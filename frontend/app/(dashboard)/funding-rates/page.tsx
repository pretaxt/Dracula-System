'use client'
import { useQuery } from '@tanstack/react-query'
import { getOpportunities } from '@/lib/api/funding'
import { Card, CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, StatusDot } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/Stats'
import { useT } from '@/components/i18n/I18nProvider'

type Opportunity = {
  exchange: string
  symbol: string
  funding_rate: string
  apr_pct: string
  next_funding_time: string | null
  instrument_type: string
  history_positive: number | null
}

export default function FundingRatesPage() {
  const { t } = useT()
  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['opportunities'],
    queryFn: getOpportunities,
    refetchInterval: 15_000,
  })

  const opps: Opportunity[] = data?.data ?? []
  const aprList = opps.map((o) => parseFloat(o.apr_pct)).filter((v) => !Number.isNaN(v))
  const avgApr = aprList.length ? aprList.reduce((s, v) => s + v, 0) / aprList.length : 0
  const maxApr = aprList.length ? Math.max(...aprList) : 0
  const totalCount = opps.length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 4 KPI */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>机会总数</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--text-primary)' }}>{totalCount}</div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>平均 APR</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--accent-emerald)' }}>
            {avgApr.toFixed(2)}%
          </div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>最高 APR</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--accent-emerald)' }}>
            {maxApr.toFixed(2)}%
          </div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{t('实时')}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
            <StatusDot tone="active" />
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-primary)' }}>
              {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—'}
            </div>
          </div>
        </Card>
      </div>

      {/* 完整机会列表 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="资金费率机会扫描器"
          subtitle="FUNDING RATE OPPORTUNITIES · UPDATING"
          right={
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
              <StatusDot tone="active" />
              <span>{t('实时')} · 15s</span>
            </span>
          }
        />
        <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <thead>
            <tr>
              {[t('币对'), t('交易所'), t('类型'), '资金费率', 'APR', '历史正费率', ''].map((h, i) => (
                <th key={i} style={{
                  textAlign: i >= 3 && i <= 5 ? 'right' : 'left',
                  padding: '8px 12px',
                  color: 'var(--text-tertiary)',
                  fontSize: 10,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid var(--border-default)',
                  background: 'var(--bg-deepest)',
                  fontWeight: 500,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={7} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('加载中…')}</td></tr>
            )}
            {opps.map((o, idx) => {
              const apr = parseFloat(o.apr_pct)
              const fr  = parseFloat(o.funding_rate) * 100
              const aprBarPct = Math.min(100, Math.max(0, (apr / 50) * 100))
              const aprTone: 'success' | 'warn' | 'default' = apr >= 20 ? 'success' : apr >= 8 ? 'warn' : 'default'
              return (
                <tr key={`${o.exchange}-${o.symbol}-${idx}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)', fontWeight: 600 }}>{o.symbol}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{o.exchange}</td>
                  <td style={{ padding: '10px 12px' }}>
                    <Badge tone={o.instrument_type === 'funding_rate' ? 'active' : 'info'}>
                      {o.instrument_type === 'funding_rate' ? t('资金费率') : o.instrument_type}
                    </Badge>
                  </td>
                  <td style={{
                    padding: '10px 12px',
                    textAlign: 'right',
                    color: fr >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)',
                  }}>
                    {fr >= 0 ? '+' : ''}{fr.toFixed(4)}%
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      <span style={{
                        fontWeight: 500,
                        color: apr >= 20 ? 'var(--accent-emerald)' : apr >= 8 ? 'var(--accent-gold)' : 'var(--text-tertiary)',
                      }}>
                        {apr >= 0 ? '+' : ''}{apr.toFixed(2)}%
                      </span>
                      <ProgressBar pct={aprBarPct} tone={aprTone} style={{ width: 56 }} />
                    </div>
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-tertiary)' }}>
                    {o.history_positive !== null ? `${o.history_positive}/10` : '—'}
                  </td>
                  <td style={{ padding: '10px 12px', fontSize: 10, color: apr >= 15 ? 'var(--accent-emerald)' : 'var(--text-tertiary)' }}>
                    {apr >= 15 ? t('已建仓') : '—'}
                  </td>
                </tr>
              )
            })}
            {!isLoading && opps.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>暂无机会</td></tr>
            )}
          </tbody>
        </table>
        {data?.snapshot_at && (
          <div style={{ marginTop: 12, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', textAlign: 'right' }}>
            SNAPSHOT · {new Date(data.snapshot_at).toLocaleString()}
          </div>
        )}
      </CardElevated>
    </div>
  )
}
