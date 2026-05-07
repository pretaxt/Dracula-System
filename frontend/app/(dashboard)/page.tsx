'use client'
import { useQuery } from '@tanstack/react-query'
import { getDashboardSummary } from '@/lib/api/dashboard'
import { getOpportunities } from '@/lib/api/funding'
import { XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'
import { Wallet, TrendingUp, BarChart3, Shield, CheckCircle2, TrendingUp as TrUp, AlertTriangle, Zap, XCircle } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, StatusDot, type BadgeTone } from '@/components/ui/Button'
import { ProgressBar, KPICard } from '@/components/ui/Stats'
import { useT } from '@/components/i18n/I18nProvider'

const STRATEGY_PERF: { name: string; pnl: number; pct: number; tone: 'active' | 'warn' | 'paused' }[] = [
  { name: '资金费率套利', pnl: 348, pct: 78, tone: 'active' },
  { name: '期现套利',     pnl: 142, pct: 32, tone: 'active' },
  { name: '三角套利',     pnl:  89, pct: 20, tone: 'active' },
  { name: '跨所基差套利', pnl:  67, pct: 15, tone: 'active' },
  { name: '配对交易',     pnl: -32, pct:  7, tone: 'warn' },
  { name: 'CEX-DEX 监控', pnl:   0, pct:  0, tone: 'paused' },
]

const ACTIVITY: { icon: 'up' | 'check' | 'warn' | 'zap' | 'x'; text: string; time: string }[] = [
  { icon: 'up',    text: '资金费率结算 · HYPE/USDC @ Hyperliquid +$2.43',                       time: '14:00:01 UTC · 1 分钟前' },
  { icon: 'check', text: '建仓成功 · ETH/USDT @ Binance · APR 18.4% · 仓位 $500',               time: '13:42:18 UTC · 19 分钟前' },
  { icon: 'warn',  text: 'HTX API 延迟升高 · 当前 P95 延迟 480ms',                              time: '13:28:51 UTC · 33 分钟前' },
  { icon: 'zap',   text: 'CEX-DEX 机会推送 · PEPE 价差 1.41% · 已发 Telegram',                  time: '13:15:02 UTC · 47 分钟前' },
  { icon: 'x',     text: '平仓 · ARB/USDT @ Bybit · 资金费率连续 2 期转负 · +$8.21',            time: '12:58:33 UTC · 1 小时前' },
]

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
  const { data: summary, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: opps } = useQuery({ queryKey: ['opportunities'], queryFn: getOpportunities, refetchInterval: 15_000 })

  if (isLoading) {
    return <div style={{ padding: 48, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{t('加载中…')}</div>
  }

  const todayFund = parseFloat(summary?.today_funding_usd || '0')
  const netPnl    = parseFloat(summary?.net_pnl_usd || '0')
  const openPos   = summary?.open_positions ?? 0
  const series    = summary?.pnl_series_30d ?? []
  const last7     = series.slice(-7).reduce((sum: number, p: { net_pnl_usd: string }) => sum + parseFloat(p.net_pnl_usd), 0)

  const chartData = series.map((p: { date: string; net_pnl_usd: string }) => ({
    date: p.date.slice(5),
    pnl:  parseFloat(p.net_pnl_usd),
  }))

  const oppList: Array<Record<string, string | number>> = opps?.data ?? []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* ========== 4 KPI ========== */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <KPICard
          label={t('总资本')}
          value={<>$34,827.<span style={{ color: 'var(--text-tertiary)', fontSize: '70%' }}>52</span></>}
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
          value={`${last7 >= 0 ? '+' : ''}$${last7.toFixed(2)}`}
          accent={last7 >= 0 ? 'positive' : 'negative'}
          meta={`${last7 >= 0 ? '+' : ''}${last7 !== 0 ? (last7 / 348 * 100).toFixed(2) : '0'}% MTD`}
          icon={<BarChart3 size={14} style={{ color: 'var(--accent-emerald)' }} />}
          animationDelay="0.1s"
        />
        <KPICard
          label={t('单日回撤')}
          value={<>-0.32<span style={{ color: 'var(--text-tertiary)', fontSize: '70%' }}>%</span></>}
          icon={<Shield size={14} style={{ color: 'var(--accent-emerald)' }} />}
          footer={
            <>
              <ProgressBar pct={11} tone="success" />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 6, color: 'var(--text-tertiary)' }}>
                <span>{t('距 Tier 3 红线')}</span>
                <span style={{ color: 'var(--accent-emerald)' }}>2.68%</span>
              </div>
            </>
          }
          animationDelay="0.15s"
        />
      </div>

      {/* ========== 权益曲线 + 策略表现 ========== */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
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
            {STRATEGY_PERF.map((s) => (
              <div key={s.name}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <StatusDot tone={s.tone} />
                    <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{t(s.name)}</span>
                  </div>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 12,
                    color: s.pnl > 0 ? 'var(--accent-emerald)' : s.pnl < 0 ? 'var(--accent-blood)' : 'var(--text-tertiary)',
                  }}>
                    {s.pnl === 0 ? t('监控中') : `${s.pnl > 0 ? '+' : ''}$${s.pnl}`}
                  </span>
                </div>
                <ProgressBar pct={s.pct} tone={s.tone === 'warn' ? 'warn' : s.tone === 'paused' ? 'default' : 'success'} />
              </div>
            ))}
          </div>
        </CardElevated>
      </div>

      {/* ========== 风控状态 + 实时机会 ========== */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 16 }}>
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader title={t('风控状态')} right={<Badge tone="active">SAFE</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {[
              { l: t('单日回撤'),       v: '-0.32%',  cap: '/ -3.00%' },
              { l: t('周回撤'),         v: '-1.04%',  cap: '/ -8.00%' },
              { l: t('最低保证金率'),   v: '87.3%',   cap: '/ 50%' },
              { l: t('API 错误率 (5m)'),v: '0.4%',    cap: '/ 5.0%' },
              { l: t('WS 连接稳定性'),  v: '100%',    cap: '',           accent: 'positive' as const },
            ].map((row, i) => (
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
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                <StatusDot tone="active" />
                <span>{t('实时')}</span>
              </span>
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
          {ACTIVITY.map((a, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 12px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-card)' }}>
              <div style={{ marginTop: 2 }}>{ACTIVITY_ICON[a.icon]}</div>
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
