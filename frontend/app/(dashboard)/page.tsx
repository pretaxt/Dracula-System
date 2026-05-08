'use client'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary } from '@/lib/api/dashboard'
import { getOpportunities } from '@/lib/api/funding'
import { getActivity } from '@/lib/api/system'
import { XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'
import { Wallet, TrendingUp, BarChart3, Shield, CheckCircle2, TrendingUp as TrUp, AlertTriangle, Zap, XCircle } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, StatusDot, type BadgeTone } from '@/components/ui/Button'
import { ProgressBar, KPICard } from '@/components/ui/Stats'
import { SkeletonKpiCard, Skeleton } from '@/components/ui/Skeleton'
import { ErrorBanner } from '@/components/ui/ErrorBanner'
import { useT } from '@/components/i18n/I18nProvider'

const ACTIVITY_ICON = {
  up:    <TrUp size={14} style={{ color: 'var(--accent-emerald)' }} />,
  check: <CheckCircle2 size={14} style={{ color: 'var(--accent-emerald)' }} />,
  warn:  <AlertTriangle size={14} style={{ color: 'var(--accent-gold)' }} />,
  zap:   <Zap size={14} style={{ color: 'var(--accent-azure)' }} />,
  x:     <XCircle size={14} style={{ color: 'var(--accent-blood)' }} />,
}

const OPP_TONE: Record<string, BadgeTone> = {
  funding_rate: 'active',
  triangular:   'info',
  spot_perp:    'info',
  cex_dex:      'info',
  basis_arb:    'info',
}

export default function DashboardPage() {
  const { t } = useT()
  const { data: summary, isLoading, isError, error, refetch } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: opps, refetch: refetchOpps } = useQuery({ queryKey: ['opportunities'], queryFn: getOpportunities, refetchInterval: 15_000 })
  const { data: activityData } = useQuery({ queryKey: ['activity'], queryFn: () => getActivity(8), refetchInterval: 30_000 })

  if (isLoading) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div className="kpi-grid">
          <SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard />
        </div>
        <div className="row-21">
          <Skeleton height={300} rounded="md" />
          <Skeleton height={300} rounded="md" />
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <ErrorBanner message={error} onRetry={() => { refetch(); refetchOpps() }} />
    )
  }

  const todayFund    = parseFloat(summary?.today_funding_usd || '0')
  const netPnl       = parseFloat(summary?.net_pnl_usd || '0')
  const openPos      = summary?.open_positions ?? 0
  const series       = summary?.pnl_series_30d ?? []
  const totalEquity  = parseFloat(summary?.total_equity_usd || '0')
  const monthlyPnl   = parseFloat(summary?.monthly_pnl_usd || '0')
  const dailyDDPct   = parseFloat(summary?.daily_drawdown_pct || '0')

  const chartData = series.map((p: { date: string; net_pnl_usd: string }) => ({
    date: p.date.slice(5),
    pnl:  parseFloat(p.net_pnl_usd),
  }))

  const oppList: Array<Record<string, string | number>> = opps?.data ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* ========== 4 KPI ========== */}
      <div className="kpi-grid">
        <KPICard
          label={t('总资本')}
          value={`$${totalEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          meta={t('USDT 等值')}
          icon={<Wallet size={14} />}
          animationDelay="0s"
        />
        <KPICard
          label={t('今日 PnL')}
          value={`${todayFund >= 0 ? '+' : ''}$${todayFund.toFixed(2)}`}
          accent={todayFund >= 0 ? 'positive' : 'negative'}
          icon={<TrendingUp size={14} style={{ color: 'var(--accent-emerald)' }} />}
          animationDelay="0.05s"
        />
        <KPICard
          label={t('月度 PnL')}
          value={`${monthlyPnl >= 0 ? '+' : ''}$${monthlyPnl.toFixed(2)}`}
          accent={monthlyPnl >= 0 ? 'positive' : 'negative'}
          meta={totalEquity > 0 ? `${monthlyPnl >= 0 ? '+' : ''}${(monthlyPnl / totalEquity * 100).toFixed(2)}% MTD` : '— MTD'}
          icon={<BarChart3 size={14} style={{ color: 'var(--accent-emerald)' }} />}
          animationDelay="0.1s"
        />
        <KPICard
          label={t('单日回撤')}
          value={
            <>
              {dailyDDPct >= 0 ? '+' : ''}{dailyDDPct.toFixed(2)}
              <span style={{ color: 'var(--text-tertiary)', fontSize: '70%' }}>%</span>
            </>
          }
          accent={dailyDDPct < 0 ? 'negative' : 'default'}
          icon={<Shield size={14} style={{ color: dailyDDPct > -2 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }} />}
          footer={
            <>
              <ProgressBar pct={Math.min(100, Math.abs(dailyDDPct) / 3 * 100)} tone={Math.abs(dailyDDPct) < 2 ? 'success' : 'warn'} />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 6, color: 'var(--text-tertiary)' }}>
                <span>{t('距 Tier 3 红线')}</span>
                <span style={{ color: 'var(--accent-emerald)' }}>{(3 - Math.abs(dailyDDPct)).toFixed(2)}%</span>
              </div>
            </>
          }
          animationDelay="0.15s"
        />
      </div>

      {/* ========== 权益曲线 + 策略表现 ========== */}
      <div className="row-21">
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title={t('权益曲线')}
            subtitle="EQUITY CURVE · 30 DAYS"
            right={
              <div style={{ display: 'flex', gap: 4 }}>
                {['1D', '7D', '30D', 'ALL'].map((p) => (
                  <span key={p} style={{
                    fontFamily: 'var(--font-mono)', fontSize: 10,
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-sm)',
                    background: p === '30D' ? 'var(--accent-blood)' : 'var(--bg-card)',
                    color: p === '30D' ? '#fff' : 'var(--text-tertiary)',
                    cursor: 'pointer',
                  }}>{p}</span>
                ))}
              </div>
            }
          />
          <div style={{ height: 200, marginTop: 8 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%"   stopColor="var(--accent-blood)" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="var(--accent-blood)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="date" tick={{ fill: 'var(--text-muted)', fontSize: 10, fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10, fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} width={48} />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                  }}
                  formatter={(v) => [`$${Number(v ?? 0).toFixed(4)}`, 'PnL']}
                />
                <Area type="monotone" dataKey="pnl" stroke="var(--accent-blood)" strokeWidth={1.5} fill="url(#equityGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border-subtle)' }}>
            {[
              { l: t('起始'), v: '$34,215.37' },
              { l: t('最高'), v: '$34,891.20' },
              { l: t('最低'), v: '$34,082.51' },
              { l: 'Sharpe',  v: '1.83', accent: 'positive' },
            ].map((s, i) => (
              <div key={i}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{s.l}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, marginTop: 4, color: s.accent === 'positive' ? 'var(--accent-emerald)' : 'var(--text-primary)' }}>{s.v}</div>
              </div>
            ))}
          </div>
        </CardElevated>

        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title={t('策略表现')}
            subtitle="STRATEGY PERFORMANCE"
            right={<Badge tone="info">MTD</Badge>}
          />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {(() => {
              type PerfRow = { instance: string; label: string; total_pnl: string; open_positions: number; closed_positions: number }
              const perfData: PerfRow[] =
                (summary?.strategy_performance as PerfRow[] | undefined) ?? []
              if (perfData.length === 0) {
                return (
                  <div style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                    {t('暂无策略数据')}
                  </div>
                )
              }
              const maxAbs = Math.max(
                1,
                ...perfData.map((p) => Math.abs(parseFloat(p.total_pnl))),
              )
              return perfData.map((p) => {
                const total = parseFloat(p.total_pnl)
                const pct = Math.min(100, (Math.abs(total) / maxAbs) * 100)
                const tone: 'active' | 'warn' | 'paused' =
                  total > 0 ? 'active' : total < 0 ? 'warn' : 'paused'
                const pnlColor =
                  total > 0
                    ? 'var(--accent-emerald)'
                    : total < 0
                    ? 'var(--accent-blood)'
                    : 'var(--text-tertiary)'
                return (
                  <div key={p.instance}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <StatusDot tone={tone} />
                        <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>
                          {t(p.label)}
                        </span>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-tertiary)' }}>
                          {p.open_positions}/{p.open_positions + p.closed_positions}
                        </span>
                      </div>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: pnlColor }}>
                        {total === 0
                          ? t('持平')
                          : `${total > 0 ? '+' : '-'}$${Math.abs(total).toFixed(2)}`}
                      </span>
                    </div>
                    <ProgressBar
                      pct={pct}
                      tone={tone === 'warn' ? 'warn' : tone === 'paused' ? 'default' : 'success'}
                    />
                  </div>
                )
              })
            })()}
          </div>
        </CardElevated>
      </div>

      {/* ========== 风控状态 + 实时机会 ========== */}
      <div className="row-12">
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader title={t('风控状态')} right={<Badge tone={Math.abs(dailyDDPct) >= 3 ? 'critical' : Math.abs(dailyDDPct) >= 2 ? 'warn' : 'active'}>{Math.abs(dailyDDPct) >= 3 ? 'HALT' : Math.abs(dailyDDPct) >= 2 ? 'WARN' : 'SAFE'}</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {(() => {
              const weeklyDD    = parseFloat(summary?.weekly_dd_pct          || '0')
              const marginUsage = parseFloat(summary?.margin_usage_pct       || '0')
              const apiErr5m    = parseFloat(summary?.api_error_rate_5m_pct  || '0')
              const wsStab      = parseFloat(summary?.ws_stability_pct       || '100')
              const marginAvail = Math.max(0, 100 - marginUsage)
              return [
                { l: t('单日回撤'),       v: `${dailyDDPct >= 0 ? '+' : ''}${dailyDDPct.toFixed(2)}%`, cap: '/ -3.00%' },
                { l: t('周回撤'),         v: `${weeklyDD >= 0 ? '+' : ''}${weeklyDD.toFixed(2)}%`,    cap: '/ -8.00%' },
                { l: t('最低保证金率'),   v: `${marginAvail.toFixed(1)}%`,                            cap: '/ 50%' },
                { l: t('API 错误率 (5m)'),v: `${apiErr5m.toFixed(1)}%`,                               cap: '/ 5.0%' },
                { l: t('WS 连接稳定性'),  v: `${wsStab.toFixed(0)}%`,                                 cap: '',          accent: 'positive' as const },
              ]
            })().map((row, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{row.l}</span>
                <div style={{ display: 'flex', gap: 6, fontFamily: 'var(--font-mono)' }}>
                  <span style={{ color: row.accent === 'positive' ? 'var(--accent-emerald)' : 'var(--text-primary)' }}>{row.v}</span>
                  {row.cap && <span style={{ color: 'var(--text-tertiary)' }}>{row.cap}</span>}
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border-subtle)', fontSize: 12 }}>
            <CheckCircle2 size={14} style={{ color: 'var(--accent-emerald)' }} />
            <span style={{ color: 'var(--text-secondary)' }}>{t('三层风控全部正常')}</span>
          </div>
        </CardElevated>

        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title={t('实时套利机会')}
            subtitle="LIVE OPPORTUNITIES · UPDATING"
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                  <StatusDot tone="active" />
                  <span>{t('实时')}</span>
                </span>
                <Link
                  href="/funding-rates"
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--accent-blood)',
                    textDecoration: 'none',
                    transition: 'color var(--duration-fast)',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--accent-blood-bright)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--accent-blood)' }}
                >
                  查看全部 →
                </Link>
              </div>
            }
          />
          <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            <thead>
              <tr>
                {[t('策略'), t('币对'), t('交易所'), t('指标'), 'APR', t('规模'), ''].map((h, i) => (
                  <th key={i} style={{
                    textAlign: i >= 3 && i <= 5 ? 'right' : 'left',
                    padding: '8px 12px',
                    color: 'var(--text-tertiary)',
                    fontWeight: 500,
                    fontSize: 10,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    borderBottom: '1px solid var(--border-default)',
                    background: 'var(--bg-deepest)',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {oppList.slice(0, 5).map((o, idx) => {
                const apr = parseFloat(String(o.apr_pct))
                const fr  = parseFloat(String(o.funding_rate)) * 100
                const stratKey = String(o.instrument_type || 'funding_rate')
                const stratLabel = stratKey === 'funding_rate' ? t('资金费率') : stratKey
                return (
                  <tr key={`${o.symbol}-${idx}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 12px' }}><Badge tone={OPP_TONE[stratKey] || 'info'}>{stratLabel}</Badge></td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{String(o.symbol)}</td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{String(o.exchange)}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: fr >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>
                      {fr >= 0 ? '+' : ''}{fr.toFixed(4)}%
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500, color: apr > 0 ? 'var(--accent-emerald)' : 'var(--text-tertiary)' }}>
                      {apr > 0 ? `+${apr.toFixed(2)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>$500</td>
                    <td style={{ padding: '10px 12px', fontSize: 10, color: 'var(--accent-emerald)' }}>{t('已建仓')}</td>
                  </tr>
                )
              })}
              {oppList.length === 0 && (
                <tr><td colSpan={7} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('加载中…')}</td></tr>
              )}
            </tbody>
          </table>
          {openPos > 0 && (
            <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              ↳ {openPos} 个活跃仓位 · 累计净盈亏 ${netPnl.toFixed(2)}
            </div>
          )}
        </CardElevated>
      </div>

      {/* ========== 系统活动 ========== */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title={t('系统活动')}
          right={<span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-tertiary)' }}>LAST 1H</span>}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {(activityData?.data ?? []).length === 0 && (
            <div style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
              {t('暂无活动记录')}
            </div>
          )}
          {(activityData?.data ?? []).map((a, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 12px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-card)' }}>
              <div style={{ marginTop: 2 }}>{ACTIVITY_ICON[a.icon as keyof typeof ACTIVITY_ICON] ?? ACTIVITY_ICON.up}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>{a.text}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 2, color: 'var(--text-tertiary)' }}>{a.time}</div>
              </div>
            </div>
          ))}
        </div>
      </CardElevated>

      {opps?.snapshot_at && (
        <div style={{ marginTop: 8, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', textAlign: 'right' }}>
          LAST UPDATE · {new Date(opps.snapshot_at).toLocaleTimeString()}
        </div>
      )}
    </div>
  )
}
