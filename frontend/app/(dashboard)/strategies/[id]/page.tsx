'use client'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, type BadgeTone } from '@/components/ui/Button'
import { useT } from '@/components/i18n/I18nProvider'
import { getStrategyById, type StrategyStatus } from '@/lib/strategies/catalog'
import { getStrategyStatus, getSpotPerpOpportunities, getFundingRateOpportunities } from '@/lib/api/strategies'
import { getDashboardSummary } from '@/lib/api/dashboard'

const INSTANCE_MAP: Record<string, string> = {
  'funding-rate': 'funding_rate_main',
  'spot-perp':    'spot_perp_main',
}

const STATUS_TONE: Record<StrategyStatus, BadgeTone> = {
  RUNNING:    'active',
  PLANNED:    'paused',
  MONITOR:    'info',
  DISABLED:   'paused',
  UNDERWATER: 'warn',
}

const BACK_LINK_STYLE = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  fontFamily: 'var(--font-mono)',
  fontSize: 14,
  color: 'var(--accent-blood)',
  textDecoration: 'none',
  letterSpacing: '0.04em',
} as const

export default function StrategyDetailPage({ params }: { params: { id: string } }) {
  const { t } = useT()
  const strategy = getStrategyById(params.id)

  const { data: live } = useQuery({
    queryKey: ['strategy'],
    queryFn: getStrategyStatus,
    refetchInterval: 10_000,
    enabled: params.id === 'funding-rate',
  })
  const { data: spotPerpOpps } = useQuery({
    queryKey: ['spot-perp-opps'],
    queryFn: getSpotPerpOpportunities,
    refetchInterval: 5_000,
    enabled: params.id === 'spot-perp',
  })
  const { data: fundingOpps } = useQuery({
    queryKey: ['funding-rate-opps'],
    queryFn: getFundingRateOpportunities,
    refetchInterval: 10_000,
    enabled: params.id === 'funding-rate',
  })
  const { data: summary } = useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboardSummary,
    refetchInterval: 30_000,
  })

  if (!strategy) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <h2 style={{ fontFamily: 'var(--font-display)', color: 'var(--text-primary)', margin: 0 }}>
          {t('策略不存在')}
        </h2>
        <p style={{ marginTop: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 14 }}>
          {t('未找到 id 为')}{' '}
          <code style={{ color: 'var(--accent-blood)' }}>{params.id}</code>{' '}
          {t('的策略')}
        </p>
        <div style={{ marginTop: 24 }}>
          <Link href="/strategies" style={BACK_LINK_STYLE}>
            <ArrowLeft size={14} />
            <span>{t('返回策略中心')}</span>
          </Link>
        </div>
      </div>
    )
  }

  const status: StrategyStatus =
    strategy.id === 'funding-rate' && live
      ? live.paper_running
        ? 'RUNNING'
        : 'PLANNED'
      : strategy.id === 'spot-perp' && spotPerpOpps
      ? spotPerpOpps.running
        ? 'MONITOR'
        : 'PLANNED'
      : strategy.status

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Link href="/strategies" style={BACK_LINK_STYLE}>
        <ArrowLeft size={14} />
        <span>{t('返回策略中心')}</span>
      </Link>

      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 32,
                color: 'var(--accent-blood)',
                fontWeight: 500,
              }}
            >
              {strategy.num}
            </span>
            <h1
              style={{
                margin: 0,
                fontFamily: 'var(--font-display)',
                fontSize: 36,
                fontWeight: 600,
                letterSpacing: '0.04em',
                color: 'var(--text-primary)',
              }}
            >
              {t(strategy.zhName)}
            </h1>
            <Badge tone={STATUS_TONE[status]}>{status}</Badge>
            <Badge tone="info">{strategy.phase}</Badge>
          </div>
          <p
            style={{
              margin: '8px 0 0',
              fontFamily: 'var(--font-mono)',
              fontSize: 13,
              color: 'var(--text-tertiary)',
              letterSpacing: '0.04em',
            }}
          >
            {strategy.enLabel}
          </p>
        </div>
      </div>

      {(() => {
        // 实时数据计算 — capital/monthly/positions 全部从 API 取
        type Perf = { instance: string; total_pnl: string; open_positions: number }
        const perfList = (summary?.strategy_performance ?? []) as Perf[]
        const instance = INSTANCE_MAP[strategy.id]
        const perf = instance ? perfList.find((p) => p.instance === instance) : undefined

        let liveCapital = '—'
        let liveMonthly: string | null = null
        let liveMonthlyTone: 'positive' | 'negative' | undefined
        let livePositions = '—'

        if (strategy.id === 'funding-rate' && live) {
          const cfg = live.current_config
          const sizeUsd = parseFloat(cfg?.max_position_notional_usd ?? '0')
          const maxPos = cfg?.max_concurrent_positions ?? 0
          const openCnt = perf?.open_positions ?? live.open_positions ?? 0
          // 三段：已用 / 账户实际余额 / 策略配置上限
          const deployed = openCnt * sizeUsd
          const account = parseFloat(summary?.total_equity_usd ?? '0')
          const configMax = maxPos * sizeUsd
          liveCapital = `$${deployed.toFixed(0)} / $${account.toFixed(0)} / $${configMax.toFixed(0)}`
          livePositions = `${openCnt} / ${maxPos}`
          if (perf) {
            const pnl = parseFloat(perf.total_pnl)
            liveMonthly = pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`
            liveMonthlyTone = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : undefined
          } else {
            liveMonthly = '$0.00'
          }
        } else if (perf) {
          const pnl = parseFloat(perf.total_pnl)
          liveMonthly = pnl >= 0 ? `+$${pnl.toFixed(2)}` : `-$${Math.abs(pnl).toFixed(2)}`
          liveMonthlyTone = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : undefined
          livePositions = String(perf.open_positions)
        }

        return (
          <div className="kpi-grid">
            <CardElevated style={{ padding: 20 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                {t('分配资金')}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 24, marginTop: 8, color: 'var(--text-primary)' }}
                title="已用 / 账户实际余额 / 策略配置上限">
                {liveCapital}
              </div>
              {strategy.id === 'funding-rate' && (
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 6, color: 'var(--text-tertiary)', letterSpacing: '0.04em' }}>
                  已用 / 账户 / 配置
                </div>
              )}
            </CardElevated>
            <CardElevated style={{ padding: 20 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                {t('月化收益')}
              </div>
              <div
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 28,
                  marginTop: 8,
                  color:
                    liveMonthly === null
                      ? 'var(--text-tertiary)'
                      : liveMonthlyTone === 'negative'
                      ? 'var(--accent-blood)'
                      : liveMonthlyTone === 'positive'
                      ? 'var(--accent-emerald)'
                      : 'var(--text-primary)',
                }}
              >
                {liveMonthly ?? '—'}
              </div>
            </CardElevated>
            <CardElevated style={{ padding: 20 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                {t('持仓')}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, marginTop: 8, color: 'var(--text-primary)' }}>
                {livePositions}
              </div>
            </CardElevated>
          </div>
        )
      })()}

      <CardElevated style={{ padding: 24 }}>
        <SectionHeader title={t('策略简介')} subtitle="STRATEGY THESIS" />
        <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--text-secondary)', margin: 0 }}>
          {strategy.desc}
        </p>
        {strategy.thesis && (
          <p style={{ fontSize: 16, lineHeight: 1.7, color: 'var(--text-secondary)', marginTop: 12, marginBottom: 0 }}>
            {strategy.thesis}
          </p>
        )}

        {strategy.rules && (
          <div style={{ marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border-subtle)', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 24 }}>
            {/* 入场条件 */}
            <div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.12em',
                textTransform: 'uppercase', color: 'var(--accent-emerald)',
                marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-emerald)',
                  boxShadow: '0 0 6px var(--accent-emerald)',
                }} />
                {t('入场条件')} · ENTRY
              </div>
              <ol style={{ paddingLeft: 0, listStyle: 'none', margin: 0 }}>
                {strategy.rules.entry.map((r, i) => (
                  <li key={i} style={{
                    fontSize: 15, lineHeight: 1.5, color: 'var(--text-secondary)',
                    padding: '8px 0', borderBottom: i < strategy.rules!.entry.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                    display: 'flex', gap: 10,
                  }}>
                    <span style={{
                      flexShrink: 0, width: 20, height: 20, borderRadius: 4,
                      background: 'rgba(16,185,129,0.1)', color: 'var(--accent-emerald)',
                      fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600,
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    }}>{i + 1}</span>
                    <span>{t(r)}</span>
                  </li>
                ))}
              </ol>
            </div>

            {/* 出场条件 */}
            <div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.12em',
                textTransform: 'uppercase', color: 'var(--accent-blood)',
                marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-blood)',
                  boxShadow: '0 0 6px var(--accent-blood)',
                }} />
                {t('出场条件')} · EXIT ({t('任一触发')})
              </div>
              <ol style={{ paddingLeft: 0, listStyle: 'none', margin: 0 }}>
                {strategy.rules.exit.map((r, i) => (
                  <li key={i} style={{
                    fontSize: 15, lineHeight: 1.5, color: 'var(--text-secondary)',
                    padding: '8px 0', borderBottom: i < strategy.rules!.exit.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                    display: 'flex', gap: 10,
                  }}>
                    <span style={{
                      flexShrink: 0, width: 20, height: 20, borderRadius: 4,
                      background: 'rgba(227,64,88,0.1)', color: 'var(--accent-blood)',
                      fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600,
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    }}>{i + 1}</span>
                    <span>{t(r)}</span>
                  </li>
                ))}
              </ol>
            </div>

            {/* 参数 */}
            <div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.12em',
                textTransform: 'uppercase', color: 'var(--accent-gold)',
                marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', background: 'var(--accent-gold)',
                  boxShadow: '0 0 6px var(--accent-gold)',
                }} />
                {t('参数')} · PARAMETERS
              </div>
              <div style={{ margin: 0 }}>
                {strategy.rules.params.map((p, i) => (
                  <div key={i} style={{
                    padding: '8px 0',
                    borderBottom: i < strategy.rules!.params.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                    display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 14,
                  }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t(p.label)}</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', textAlign: 'right' }}>
                      {t(p.value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </CardElevated>

      {strategy.risks && strategy.risks.length > 0 && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('风险点')}
            subtitle="RISK FACTORS"
            right={<AlertTriangle size={16} style={{ color: 'var(--accent-blood)' }} />}
          />
          <ul style={{ paddingLeft: 0, listStyle: 'none', margin: 0 }}>
            {strategy.risks.map((r, i) => (
              <li
                key={i}
                style={{
                  display: 'flex',
                  gap: 12,
                  padding: '10px 0',
                  borderBottom:
                    i < strategy.risks!.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                  fontSize: 15,
                  color: 'var(--text-secondary)',
                  lineHeight: 1.5,
                }}
              >
                <AlertTriangle
                  size={14}
                  style={{ color: 'var(--accent-blood)', flexShrink: 0, marginTop: 4 }}
                />
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </CardElevated>
      )}

      {/* spot-perp 实时机会扫描器 */}
      {strategy.id === 'spot-perp' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('实时基差机会')}
            subtitle="LIVE BASIS OPPORTUNITIES · BINANCE + OKX"
            right={
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 13,
                  color: spotPerpOpps?.running
                    ? 'var(--accent-emerald)'
                    : 'var(--text-tertiary)',
                }}
              >
                {spotPerpOpps?.running
                  ? `● ${t('扫描中')}`
                  : `○ ${t('未启动')}`}
                {spotPerpOpps?.last_scan_at && (
                  <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                    {t('上次扫描')}{' '}
                    {new Date(spotPerpOpps.last_scan_at).toLocaleTimeString(
                      'en-US',
                      { hour12: false },
                    )}
                  </span>
                )}
              </span>
            }
          />
          {(spotPerpOpps?.data ?? []).length === 0 ? (
            <div
              style={{
                padding: 24,
                textAlign: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 14,
                color: 'var(--text-tertiary)',
              }}
            >
              {spotPerpOpps?.running
                ? t('当前无基差超过阈值的标的')
                : t('扫描器未运行')}
            </div>
          ) : (
            <div className="table-scroll-x">
              <table
                className="data-table"
                style={{
                  width: '100%',
                  borderCollapse: 'separate',
                  borderSpacing: 0,
                  fontFamily: 'var(--font-mono)',
                  fontSize: 14,
                }}
              >
                <thead>
                  <tr>
                    {[t('币对'), t('方向'), t('现货'), t('永续'), t('基差'), t('基差 %')].map((h, i) => (
                      <th
                        key={i}
                        style={{
                          textAlign: i === 0 || i === 1 ? 'left' : 'right',
                          padding: '10px 12px',
                          color: 'var(--text-tertiary)',
                          fontWeight: 500,
                          fontSize: 12,
                          letterSpacing: '0.08em',
                          textTransform: 'uppercase',
                          borderBottom: '1px solid var(--border-default)',
                          background: 'var(--bg-deepest)',
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(spotPerpOpps?.data ?? []).map((o) => {
                    const basisPct = parseFloat(o.basis_pct)
                    const isPremium = o.direction === 'premium'
                    const dirColor = isPremium
                      ? 'var(--accent-emerald)'
                      : 'var(--accent-blood)'
                    return (
                      <tr
                        key={o.symbol}
                        style={{ borderBottom: '1px solid var(--border-subtle)' }}
                      >
                        <td style={{ padding: '12px', color: 'var(--text-primary)', fontWeight: 600 }}>
                          {o.symbol}
                        </td>
                        <td style={{ padding: '12px', color: dirColor, fontWeight: 500 }}>
                          {isPremium ? `↑ ${t('升水')}` : `↓ ${t('贴水')}`}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                          ${parseFloat(o.spot_price).toLocaleString('en-US', { maximumFractionDigits: 4 })}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                          ${parseFloat(o.perp_price).toLocaleString('en-US', { maximumFractionDigits: 4 })}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: dirColor }}>
                          {basisPct >= 0 ? '+' : ''}${parseFloat(o.basis_abs).toFixed(4)}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: dirColor, fontWeight: 600 }}>
                          {basisPct >= 0 ? '+' : ''}{basisPct.toFixed(4)}%
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div
            style={{
              marginTop: 12,
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              color: 'var(--text-muted)',
            }}
          >
            {t('扫描候选门槛 |basis| ≥ 0.10%（仅展示），实盘入场阈值 |basis| ≥ 0.25%（升水/贴水双向自动开平仓） · 60 秒扫描')}
          </div>
        </CardElevated>
      )}

      {/* funding-rate 实时机会扫描器（候选展示，含 passes_entry 标志） */}
      {strategy.id === 'funding-rate' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('实时费率机会')}
            subtitle="LIVE FUNDING-RATE OPPORTUNITIES · BINANCE + OKX"
            right={
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 13,
                  color: fundingOpps?.running
                    ? 'var(--accent-emerald)'
                    : 'var(--text-tertiary)',
                }}
              >
                {fundingOpps?.running
                  ? `● ${t('扫描中')}`
                  : `○ ${t('未启动')}`}
                {fundingOpps?.last_scan_at && (
                  <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                    {t('上次扫描')}{' '}
                    {new Date(fundingOpps.last_scan_at).toLocaleTimeString(
                      'en-US',
                      { hour12: false },
                    )}
                  </span>
                )}
              </span>
            }
          />
          {(fundingOpps?.data ?? []).length === 0 ? (
            <div
              style={{
                padding: 24,
                textAlign: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 14,
                color: 'var(--text-tertiary)',
              }}
            >
              {fundingOpps?.running
                ? t('当前无符合候选门槛的标的')
                : t('扫描器未运行')}
            </div>
          ) : (
            <div className="table-scroll-x">
              <table
                className="data-table"
                style={{
                  width: '100%',
                  borderCollapse: 'separate',
                  borderSpacing: 0,
                  fontFamily: 'var(--font-mono)',
                  fontSize: 14,
                }}
              >
                <thead>
                  <tr>
                    {[t('币对'), t('交易所'), t('当前 APR'), t('费率/期'), t('距入场'), t('历史正费率'), t('状态')].map((h, i) => (
                      <th
                        key={i}
                        style={{
                          textAlign: i === 0 || i === 1 || i === 6 ? 'left' : 'right',
                          padding: '10px 12px',
                          color: 'var(--text-tertiary)',
                          fontWeight: 500,
                          fontSize: 12,
                          letterSpacing: '0.08em',
                          textTransform: 'uppercase',
                          borderBottom: '1px solid var(--border-default)',
                          background: 'var(--bg-deepest)',
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(fundingOpps?.data ?? []).map((o) => {
                    const apr = parseFloat(o.apr_pct)
                    const dist = parseFloat(o.distance_to_entry_pct)
                    const minApr = parseFloat(fundingOpps?.min_apr_pct || '0')
                    // 距入场 < 20% 入场门槛 算"接近"，高亮橘色
                    const isNear = !o.passes_entry && dist > 0 && dist <= minApr * 0.2
                    const aprColor = o.passes_entry
                      ? 'var(--accent-emerald)'
                      : isNear
                      ? 'var(--accent-gold)'
                      : 'var(--text-secondary)'
                    return (
                      <tr
                        key={`${o.exchange}-${o.symbol}`}
                        style={{ borderBottom: '1px solid var(--border-subtle)' }}
                      >
                        <td style={{ padding: '12px', color: 'var(--text-primary)', fontWeight: 600 }}>
                          {o.symbol}
                        </td>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)' }}>
                          {o.exchange}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: aprColor, fontWeight: 600 }}>
                          {apr.toFixed(2)}%
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                          {(parseFloat(o.funding_rate) * 100).toFixed(4)}%
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: o.passes_entry ? 'var(--accent-emerald)' : isNear ? 'var(--accent-gold)' : 'var(--text-tertiary)' }}>
                          {o.passes_entry ? `✓ ${t('已达')}` : `−${dist.toFixed(2)}%`}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-tertiary)' }}>
                          {o.history_positive_count}/{o.history_total_count}
                        </td>
                        <td style={{ padding: '12px' }}>
                          <Badge tone={o.passes_entry ? 'active' : isNear ? 'warn' : 'paused'}>
                            {o.passes_entry ? t('可开仓') : isNear ? t('接近') : t('候选')}
                          </Badge>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div
            style={{
              marginTop: 12,
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              color: 'var(--text-muted)',
            }}
          >
            {t('扫描候选门槛 APR ≥')} {parseFloat(fundingOpps?.scan_threshold_apr_pct || '0').toFixed(2)}% {t('（仅展示），实盘入场阈值 APR ≥')} {parseFloat(fundingOpps?.min_apr_pct || '0').toFixed(2)}% {t('（结算前 15 分钟内自动开仓） · 60 秒扫描')}
          </div>
        </CardElevated>
      )}

      {/* 启动/停止控制移至策略中心列表页(避免在详情页与列表页重复) */}
    </div>
  )
}
