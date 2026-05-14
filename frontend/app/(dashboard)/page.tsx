'use client'
import Link from 'next/link'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary } from '@/lib/api/dashboard'
import { getRiskThresholds, type RiskThresholds } from '@/lib/api/risk'
import { getAllOpportunities, type AllOpportunity } from '@/lib/api/strategies'
// import { getOpportunities } from "@/lib/api/funding"  // removed: now using getAllOpportunities
import { getStrategyStatus } from '@/lib/api/strategies'
import { getActivity } from '@/lib/api/system'
import { XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart, ReferenceLine } from 'recharts'
import { Wallet, TrendingUp, BarChart3, Shield, CheckCircle2, TrendingUp as TrUp, TrendingDown, AlertTriangle, AlertOctagon, Zap, XCircle, DollarSign, PlayCircle } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, StatusDot, type BadgeTone } from '@/components/ui/Button'
import { ProgressBar, KPICard } from '@/components/ui/Stats'
import { SkeletonKpiCard, Skeleton } from '@/components/ui/Skeleton'
import { ErrorBanner } from '@/components/ui/ErrorBanner'
import { useT } from '@/components/i18n/I18nProvider'

const ACTIVITY_ICON = {
  up:    <TrUp size={14} style={{ color: 'var(--accent-emerald)' }} />,
  down:  <TrendingDown size={14} style={{ color: 'var(--accent-blood)' }} />,
  check: <CheckCircle2 size={14} style={{ color: 'var(--accent-emerald)' }} />,
  warn:  <AlertTriangle size={14} style={{ color: 'var(--accent-gold)' }} />,
  zap:   <Zap size={14} style={{ color: 'var(--accent-azure)' }} />,
  x:     <XCircle size={14} style={{ color: 'var(--accent-blood)' }} />,
  money: <DollarSign size={14} style={{ color: 'var(--accent-emerald)' }} />,
  alert: <AlertOctagon size={14} style={{ color: 'var(--accent-blood)' }} />,
  open:  <PlayCircle size={14} style={{ color: 'var(--accent-azure)' }} />,
}

const OPP_TONE: Record<string, BadgeTone> = {
  funding_rate: 'active',
  perp_basis:   'critical',
  price_spread: 'warn',
  triangular:   'info',
  spot_perp:    'info',
  cex_dex:      'info',
  basis_arb:    'info',
}
const OPP_LABEL: Record<string, string> = {
  funding_rate: '资金费率',
  perp_basis: '跨所基差',
  price_spread: '价差套利',
  triangular: '三角',
  spot_perp: '期现',
  cex_dex: 'CEX-DEX',
}

type EquityPeriod = '1D' | '7D' | '30D' | 'ALL'

export default function DashboardPage() {
  const { t } = useT()
  const [equityPeriod, setEquityPeriod] = useState<EquityPeriod>('30D')
  const { data: summary, isLoading, isError, error, refetch } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: thresholds } = useQuery<RiskThresholds>({ queryKey: ['risk-thresholds'], queryFn: getRiskThresholds, refetchInterval: 60_000 })
  // env var 阈值；fallback 老硬编码避免初始加载抖动
  const T_dailyDD = Math.abs(parseFloat(thresholds?.daily_dd_halt_pct ?? '-3'))
  const T_weeklyDD = Math.abs(parseFloat(thresholds?.weekly_dd_halt_pct ?? '-8'))
  const T_margin = parseFloat(thresholds?.min_margin_usage_pct ?? '50')
  const { data: allOpps, refetch: refetchOpps } = useQuery({ queryKey: ['all-opportunities'], queryFn: getAllOpportunities, refetchInterval: 15_000 })
  const { data: activityData } = useQuery({ queryKey: ['activity'], queryFn: () => getActivity(8), refetchInterval: 30_000 })
  const { data: stratStatus } = useQuery({ queryKey: ['strategy-status'], queryFn: getStrategyStatus, refetchInterval: 30_000 })

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
  const todayPnl     = parseFloat(summary?.today_pnl_usd || '0')
  const todayReal    = parseFloat(summary?.today_realized_usd || '0')
  const todayUnreal  = parseFloat(summary?.today_unrealized_usd || '0')
  const netPnl       = parseFloat(summary?.net_pnl_usd || '0')
  const openPos      = summary?.open_positions ?? 0
  const series       = summary?.pnl_series_30d ?? []
  const totalEquity  = parseFloat(summary?.total_equity_usd || '0')
  const monthlyPnl   = parseFloat(summary?.monthly_pnl_usd || '0')
  const dailyDDPct   = parseFloat(summary?.daily_drawdown_pct || '0')

  // cumsum: 把 daily PnL 转为累计权益曲线（起点定义为 0）
  let _cum = 0
  const cumulativeData = (series as { date: string; net_pnl_usd: string }[]).map((p) => {
    const daily = parseFloat(p.net_pnl_usd)
    _cum += daily
    return {
      date: p.date.slice(5),
      fullDate: p.date,
      dailyPnl: daily,
      cumPnl: _cum,
    }
  })
  // 按 period 切片；'ALL' 暂等同 '30D'（backend 暂未提供更长 series）
  const sliceCount = equityPeriod === '1D' ? 1 : equityPeriod === '7D' ? 7 : cumulativeData.length
  const chartData = cumulativeData.slice(-sliceCount)
  const currentCum = chartData.length > 0 ? chartData[chartData.length - 1].cumPnl : 0
  const cumValues = chartData.map((p) => p.cumPnl)
  const highPoint = chartData.length > 0
    ? chartData.reduce((acc, p) => (p.cumPnl > acc.cumPnl ? p : acc), chartData[0])
    : null
  const lowPoint = chartData.length > 0
    ? chartData.reduce((acc, p) => (p.cumPnl < acc.cumPnl ? p : acc), chartData[0])
    : null
  const yMin = cumValues.length > 0 ? Math.min(...cumValues, 0) : 0
  const yMax = cumValues.length > 0 ? Math.max(...cumValues, 0) : 0
  const yPad = Math.max(1, (yMax - yMin) * 0.1)

  const oppList: AllOpportunity[] = allOpps?.data ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* ========== 4 KPI ========== */}
      <div className="kpi-grid">
        <KPICard
          label={t('总资本')}
          value={`$${totalEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          meta={(() => {
            const byEx = (summary?.equity_by_exchange ?? {}) as Record<string, string>
            const entries = Object.entries(byEx)
            if (entries.length === 0) return t('USDT 等值')
            // Binance 金色 / OKX 蓝色 — 视觉区分，dot + label 同色
            const COLOR: Record<string, string> = {
              binance: 'var(--accent-gold)',
              okx:     'var(--accent-azure)',
            }
            return (
              <span style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span>{t('USDT 等值')}</span>
                <span style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                  {entries.map(([ex, v]) => (
                    <span key={ex} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: COLOR[ex] || 'var(--text-tertiary)' }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor', flexShrink: 0 }} />
                      <span>{ex.toUpperCase()} ${parseFloat(v).toFixed(2)}</span>
                    </span>
                  ))}
                </span>
              </span>
            )
          })()}
          icon={<Wallet size={14} />}
          animationDelay="0s"
        />
        <KPICard
          label={t('今日 PnL')}
          value={`${todayPnl >= 0 ? '+' : ''}$${todayPnl.toFixed(2)}`}
          accent={todayPnl >= 0 ? 'positive' : 'negative'}
          meta={(() => {
            const fmt = (n: number) => `${n >= 0 ? '+' : ''}$${n.toFixed(2)}`
            return (
              <span style={{ display: 'inline-flex', gap: 8, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>
                <span title="今日已平仓实现">已实现 {fmt(todayReal)}</span>
                <span style={{ opacity: 0.4 }}>·</span>
                <span title="当前持仓浮动">浮动 {fmt(todayUnreal)}</span>
                <span style={{ opacity: 0.4 }}>·</span>
                <span title="今日已结算 funding (含在已实现内)">funding {fmt(todayFund)}</span>
              </span>
            )
          })()}
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
          icon={<Shield size={14} style={{ color: dailyDDPct > -T_dailyDD * 0.4 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }} />}
          footer={
            <>
              <ProgressBar pct={Math.min(100, Math.abs(dailyDDPct) / 3 * 100)} tone={Math.abs(dailyDDPct) < 2 ? 'success' : 'warn'} />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 6, color: 'var(--text-tertiary)' }}>
                <span>{t('距 Tier 3 红线')}</span>
                {(() => {
                  const buffer = T_dailyDD - Math.abs(dailyDDPct)
                  const color = buffer > T_dailyDD * 0.5 ? 'var(--accent-emerald)' : buffer > 0 ? 'var(--accent-amber)' : 'var(--accent-blood)'
                  return <span style={{ color }}>{buffer >= 0 ? `+${buffer.toFixed(2)}%` : `${buffer.toFixed(2)}%`}</span>
                })()}
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
            subtitle={`EQUITY CURVE · ${equityPeriod === 'ALL' ? '30D (max available)' : equityPeriod}`}
            right={
              <div style={{ display: 'flex', gap: 4 }}>
                {(['1D', '7D', '30D', 'ALL'] as EquityPeriod[]).map((p) => {
                  const active = p === equityPeriod
                  return (
                    <button
                      key={p}
                      type="button"
                      onClick={() => setEquityPeriod(p)}
                      title={p === 'ALL' ? '后端暂未提供更长 series，等同 30D' : undefined}
                      style={{
                        fontFamily: 'var(--font-mono)', fontSize: 12,
                        padding: '4px 10px',
                        borderRadius: 'var(--radius-sm)',
                        background: active ? 'var(--accent-blood)' : 'var(--bg-card)',
                        color: active ? '#fff' : 'var(--text-tertiary)',
                        border: 'none',
                        cursor: 'pointer',
                        transition: 'background var(--duration-fast), color var(--duration-fast)',
                      }}
                    >{p}</button>
                  )
                })}
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
                <XAxis dataKey="date" tick={{ fill: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                  domain={[yMin - yPad, yMax + yPad]}
                  tickFormatter={(v: number) => `$${v.toFixed(1)}`}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)',
                    color: 'var(--text-primary)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 13,
                  }}
                  formatter={((value: unknown, _name: unknown, item: unknown) => {
                    const payload = (item as { payload?: { dailyPnl?: number; cumPnl?: number } } | undefined)?.payload ?? {}
                    const daily = payload.dailyPnl ?? 0
                    const cum = payload.cumPnl ?? Number(value)
                    return [
                      <span key="v" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <span style={{ color: 'var(--text-tertiary)' }}>当日 PnL: <span style={{ color: daily >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>{daily >= 0 ? '+' : ''}${daily.toFixed(4)}</span></span>
                        <span style={{ color: 'var(--text-tertiary)' }}>累计: <span style={{ color: cum >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>{cum >= 0 ? '+' : ''}${cum.toFixed(4)}</span></span>
                      </span>,
                      '',
                    ]
                  }) as never}
                />
                <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="3 3" />
                <Area type="monotone" dataKey="cumPnl" stroke="var(--accent-blood)" strokeWidth={1.5} fill="url(#equityGrad)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border-subtle)' }}>
            {(() => {
              const fmt = (n: number) => `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(2)}`
              const fmtSigned = (n: number) => (n === 0 ? '$0.00' : fmt(n))
              const colorOf = (n: number) =>
                n > 0 ? 'var(--accent-emerald)' : n < 0 ? 'var(--accent-blood)' : 'var(--text-tertiary)'
              const stats: { l: string; v: string; color?: string; title?: string }[] = [
                { l: t('开始'), v: '$0.00', color: 'var(--text-tertiary)', title: '30d 起点定义为 0' },
                { l: t('当前'), v: fmtSigned(currentCum), color: colorOf(currentCum) },
                {
                  l: t('区间高'),
                  v: highPoint ? fmtSigned(highPoint.cumPnl) : '—',
                  color: highPoint ? colorOf(highPoint.cumPnl) : 'var(--text-tertiary)',
                  title: highPoint ? `于 ${highPoint.fullDate}` : undefined,
                },
                {
                  l: t('区间低'),
                  v: lowPoint ? fmtSigned(lowPoint.cumPnl) : '—',
                  color: lowPoint ? colorOf(lowPoint.cumPnl) : 'var(--text-tertiary)',
                  title: lowPoint ? `于 ${lowPoint.fullDate}` : undefined,
                },
                {
                  l: 'Sharpe',
                  v: summary?.sharpe_30d != null
                    ? Number(summary.sharpe_30d).toFixed(2)
                    : '—',
                  title: '30 天年化 Sharpe（基于日 PnL/equity）',
                },
              ]
              return stats.map((s, i) => (
                <div key={i} title={s.title}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{s.l}</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, marginTop: 4, color: s.color ?? 'var(--text-primary)' }}>{s.v}</div>
                </div>
              ))
            })()}
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
                  <div style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-tertiary)' }}>
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
                        <span style={{ fontSize: 14, color: 'var(--text-primary)' }}>
                          {t(p.label)}
                        </span>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                          {p.open_positions}/{p.open_positions + p.closed_positions}
                        </span>
                      </div>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: pnlColor }}>
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
          <SectionHeader title={t('风控状态')} right={<Badge tone={Math.abs(dailyDDPct) >= T_dailyDD ? 'critical' : Math.abs(dailyDDPct) >= T_dailyDD * 0.7 ? 'warn' : 'active'}>{Math.abs(dailyDDPct) >= T_dailyDD ? 'HALT' : Math.abs(dailyDDPct) >= T_dailyDD * 0.7 ? 'WARN' : 'SAFE'}</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {(() => {
              const weeklyDD    = parseFloat(summary?.weekly_dd_pct          || '0')
              const marginUsage = parseFloat(summary?.margin_usage_pct       || '0')
              const apiErr5m    = parseFloat(summary?.api_error_rate_5m_pct  || '0')
              const wsStab      = parseFloat(summary?.ws_stability_pct       || '100')
              return [
                { l: t('单日回撤'),       v: `${dailyDDPct >= 0 ? '+' : ''}${dailyDDPct.toFixed(2)}%`, cap: `/ -${T_dailyDD.toFixed(2)}%` },
                { l: t('周回撤'),         v: `${weeklyDD >= 0 ? '+' : ''}${weeklyDD.toFixed(2)}%`,    cap: `/ -${T_weeklyDD.toFixed(2)}%` },
                { l: t('保证金占用率'),   v: `${marginUsage.toFixed(1)}%`,                            cap: `/ ${T_margin.toFixed(0)}%` },
                { l: t('API 错误率 (5m)'),v: `${apiErr5m.toFixed(1)}%`,                               cap: '/ 5.0%' },
                { l: t('WS 连接稳定性'),  v: `${wsStab.toFixed(0)}%`,                                 cap: '',          accent: 'positive' as const },
              ]
            })().map((row, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 14 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{row.l}</span>
                <div style={{ display: 'flex', gap: 6, fontFamily: 'var(--font-mono)' }}>
                  <span style={{ color: row.accent === 'positive' ? 'var(--accent-emerald)' : 'var(--text-primary)' }}>{row.v}</span>
                  {row.cap && <span style={{ color: 'var(--text-tertiary)' }}>{row.cap}</span>}
                </div>
              </div>
            ))}
          </div>
          {(() => {
            const halted = Math.abs(dailyDDPct) >= T_dailyDD
            const warning = !halted && Math.abs(dailyDDPct) >= T_dailyDD * 0.7
            const icon = halted ? '⛔' : warning ? '⚠️' : '✓'
            const color = halted ? 'var(--accent-blood)' : warning ? 'var(--accent-amber)' : 'var(--accent-emerald)'
            const text = halted ? t('熔断中 · 已阻止新开仓') : warning ? t('接近红线') : t('三层风控全部正常')
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border-subtle)', fontSize: 14 }}>
                <span style={{ color, fontSize: 14 }}>{icon}</span>
                <span style={{ color: 'var(--text-secondary)' }}>{text}</span>
              </div>
            )
          })()}
        </CardElevated>

        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title={t('实时套利机会')}
            subtitle="LIVE OPPORTUNITIES · UPDATING"
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-tertiary)' }}>
                  <StatusDot tone="active" />
                  <span>{t('实时')}</span>
                </span>
                <Link
                  href="/funding-rates"
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 13,
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
          <div style={{ maxHeight: 360, overflowY: 'auto' }}>
          <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 14 }}>
            <thead>
              <tr>
                {[t('策略'), t('币对'), t('交易所'), t('指标'), 'APR', t('规模'), ''].map((h, i) => (
                  <th key={i} style={{
                    textAlign: i >= 3 && i <= 5 ? 'right' : 'left',
                    padding: '8px 12px',
                    color: 'var(--text-tertiary)',
                    fontWeight: 500,
                    fontSize: 12,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    borderBottom: '1px solid var(--border-default)',
                    background: 'var(--bg-deepest)',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {oppList.map((o, idx) => {
                const apr = parseFloat(String(o.apr_pct))
                const extra = parseFloat(String(o.extra_pct ?? '0'))
                const stratKey = o.strategy || 'funding_rate'
                const stratLabel = OPP_LABEL[stratKey] ? t(OPP_LABEL[stratKey]) : stratKey
                return (
                  <tr key={`${stratKey}-${o.symbol}-${idx}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 12px' }}><Badge tone={OPP_TONE[stratKey] || 'info'}>{stratLabel}</Badge></td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{o.symbol}</td>
                    <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{o.exchange}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: extra >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>
                      {extra >= 0 ? '+' : ''}{extra.toFixed(2)}%
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 500, color: apr > 0 ? 'var(--accent-emerald)' : 'var(--text-tertiary)' }}>
                      {apr > 0 ? `+${apr.toFixed(2)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                      ${parseFloat(stratStatus?.current_config?.max_position_notional_usd ?? '50').toFixed(0)}
                    </td>
                    <td style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-tertiary)' }}>{o.meta || t('扫描中')}</td>
                  </tr>
                )
              })}
              {oppList.length === 0 && (
                <tr><td colSpan={7} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('暂无符合条件的开仓机会')}</td></tr>
              )}
            </tbody>
          </table>
          </div>
          {openPos > 0 && (
            <div style={{ marginTop: 12, fontSize: 13, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              ↳ {openPos} 个活跃仓位 · 累计净盈亏 ${netPnl.toFixed(2)}
            </div>
          )}
        </CardElevated>
      </div>

      {/* ========== 系统活动 ========== */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title={t('系统活动')}
          right={<span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>LAST 1H</span>}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {(activityData?.data ?? []).length === 0 && (
            <div style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-tertiary)' }}>
              {t('暂无活动记录')}
            </div>
          )}
          {(activityData?.data ?? []).map((a, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 12px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-card)' }}>
              <div style={{ marginTop: 2 }}>{ACTIVITY_ICON[a.icon as keyof typeof ACTIVITY_ICON] ?? ACTIVITY_ICON.up}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, color: 'var(--text-primary)' }}>{a.text}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 2, color: 'var(--text-tertiary)' }}>{a.time}</div>
              </div>
            </div>
          ))}
        </div>
      </CardElevated>


    </div>
  )
}
