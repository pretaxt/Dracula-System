'use client'
import { useQuery } from '@tanstack/react-query'
import { getAllOpportunities, type AllOpportunity } from '@/lib/api/strategies'
import { getPositions } from '@/lib/api/positions'
import { Card, CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, StatusDot } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/Stats'
import { useT } from '@/components/i18n/I18nProvider'

export default function FundingRatesPage() {
  const { t } = useT()
  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['all-opportunities'],
    queryFn: getAllOpportunities,
    refetchInterval: 15_000,
  })
  const { data: posData } = useQuery({
    queryKey: ['positions-open'],
    queryFn: () => getPositions({ status: 'open' }),
    refetchInterval: 15_000,
  })

  const opps: AllOpportunity[] = data?.data ?? []
  const openSymbols = new Set<string>(
    (posData?.data ?? []).map((p: { symbol?: string }) => p.symbol ?? '').filter(Boolean),
  )
  const aprList = opps.map((o) => parseFloat(o.apr_pct)).filter((v) => !Number.isNaN(v))
  const avgApr = aprList.length ? aprList.reduce((s, v) => s + v, 0) / aprList.length : 0
  const maxApr = aprList.length ? Math.max(...aprList) : 0
  const totalCount = opps.length
  const stratSet = new Set(opps.map(o => o.strategy))
  const stratCount = stratSet.size
  const STRAT_LABEL: Record<string, string> = {
    funding_rate: '资金费率',
    perp_basis: '跨所基差',
    price_spread: '价差套利',
    spot_perp: '期现套利',
    cex_dex: 'CEX-DEX',
  }
  const STRAT_TONE: Record<string, 'active' | 'critical' | 'warn' | 'info'> = {
    funding_rate: 'active',
    perp_basis: 'critical',
    price_spread: 'warn',
    spot_perp: 'info',
    cex_dex: 'info',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 4 KPI */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16 }}>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>机会总数</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--text-primary)' }}>{totalCount}</div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>平均 APR</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--accent-emerald)' }}>
            {avgApr.toFixed(2)}%
          </div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>最高 APR</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--accent-emerald)' }}>
            {maxApr.toFixed(2)}%
          </div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>策略覆盖</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 8 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', color: 'var(--text-primary)' }}>{stratCount}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
              · 实时 {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—'}
            </div>
          </div>
        </Card>
      </div>

      {/* 完整机会列表 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="实时套利机会扫描器"
          subtitle="LIVE OPPORTUNITIES · ALL STRATEGIES · UPDATING"
          right={
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-tertiary)' }}>
              <StatusDot tone="active" />
              <span>{t('实时')} · 15s</span>
            </span>
          }
        />
        <div style={{ maxHeight: 500, overflowY: 'auto' }}>
        <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 14 }}>
          <thead>
            <tr>
              {[t('策略'), t('币对'), t('交易所'), t('次指标'), 'APR', t('备注'), ''].map((h, i) => (
                <th key={i} style={{
                  textAlign: i >= 3 && i <= 5 ? 'right' : 'left',
                  padding: '8px 12px',
                  color: 'var(--text-tertiary)',
                  fontSize: 12,
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
              const extra = parseFloat(o.extra_pct || '0')
              const aprBarPct = Math.min(100, Math.max(0, (apr / 50) * 100))
              const aprTone: 'success' | 'warn' | 'default' = apr >= 20 ? 'success' : apr >= 8 ? 'warn' : 'default'
              const stratLabel = STRAT_LABEL[o.strategy] || o.strategy
              const stratTone = STRAT_TONE[o.strategy] || 'info'
              return (
                <tr key={`${o.strategy}-${o.exchange}-${o.symbol}-${idx}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '10px 12px' }}>
                    <Badge tone={stratTone}>{t(stratLabel)}</Badge>
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)', fontWeight: 600 }}>{o.symbol}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{o.exchange}</td>
                  <td style={{
                    padding: '10px 12px',
                    textAlign: 'right',
                    color: extra >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)',
                  }}>
                    {extra >= 0 ? '+' : ''}{extra.toFixed(2)}%
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
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-tertiary)', fontSize: 12 }}>
                    {o.meta || '—'}
                  </td>
                  <td style={{ padding: '10px 12px', fontSize: 12, color: openSymbols.has(o.symbol) ? 'var(--accent-emerald)' : 'var(--text-tertiary)' }}>
                    {openSymbols.has(o.symbol) ? t('已建仓') : '—'}
                  </td>
                </tr>
              )
            })}
            {!isLoading && opps.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>暂无机会</td></tr>
            )}
          </tbody>
        </table>
        </div>

      </CardElevated>
    </div>
  )
}
