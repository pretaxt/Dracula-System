'use client'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { Lock, CheckCircle2, Pencil } from 'lucide-react'
import { getRiskLimits, getRiskEvents, getRiskThresholds, type RiskEvent, type RiskThresholds } from '@/lib/api/risk'
import { getDashboardSummary } from '@/lib/api/dashboard'
import {
  getSpotPerpConfig,
  type SpotPerpConfig,
  getPerpBasisConfig,
  type PerpBasisConfig,
} from '@/lib/api/strategies'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, type BadgeTone } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/Stats'
import { useT } from '@/components/i18n/I18nProvider'

type RiskLimits = {
  max_positions: number
  stop_loss_pct: string
  max_hold_hours: string
  min_apr_pct: string
  max_total_notional_usd: string
  scan_threshold_apr_pct?: string   // #01 候选展示门槛（"0" 回退用 min_apr_pct）
}

const ACTION_COLOR: Record<string, string> = {
  auto_recovered: 'var(--accent-emerald)',
  closed:         'var(--accent-emerald)',
  cancelled:      'var(--text-tertiary)',
  triggered:      'var(--accent-blood)',
}

const ACTION_LABEL: Record<string, string> = {
  auto_recovered: '自动恢复',
  closed:         '已平仓',
  cancelled:      '已撤单',
  triggered:      '已触发',
}

function formatEventTime(iso: string): string {
  const d = new Date(iso)
  const month = String(d.getUTCMonth() + 1).padStart(2, '0')
  const day = String(d.getUTCDate()).padStart(2, '0')
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${month}-${day} ${hh}:${mm}`
}

export default function RiskPage() {
  const { t } = useT()
  const { data, isLoading } = useQuery<RiskLimits>({ queryKey: ['risk'], queryFn: getRiskLimits })
  const { data: eventsData } = useQuery({ queryKey: ['risk-events'], queryFn: () => getRiskEvents(30), refetchInterval: 60_000 })
  const { data: summary } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })
  const { data: thresholds } = useQuery<RiskThresholds>({ queryKey: ['risk-thresholds'], queryFn: getRiskThresholds, refetchInterval: 60_000 })
  const T_dailyDD = Math.abs(parseFloat(thresholds?.daily_dd_halt_pct ?? '-3'))
  const T_weeklyDD = Math.abs(parseFloat(thresholds?.weekly_dd_halt_pct ?? '-8'))
  const T_margin = parseFloat(thresholds?.min_margin_usage_pct ?? '50')
  const T_exConc = parseFloat(thresholds?.max_exchange_concentration_pct ?? '50')
  const T_symConc = parseFloat(thresholds?.max_symbol_concentration_pct ?? '45')
  const { data: spCfg } = useQuery<SpotPerpConfig>({
    queryKey: ['spot-perp-config'],
    queryFn: getSpotPerpConfig,
    refetchInterval: 60_000,
    retry: false,
  })
  const { data: pbCfg } = useQuery<PerpBasisConfig>({
    queryKey: ['perp-basis-config'],
    queryFn: getPerpBasisConfig,
    refetchInterval: 60_000,
    retry: false,
  })

  if (isLoading || !data) {
    return <div style={{ padding: 48, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{t('加载中…')}</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 策略风控参数（每策略一卡，2 列网格）*/}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16, alignItems: 'start' }}>
        {/* 卡片只读模式 — 三策略风控参数概览，"调整" 跳转策略详情页 */}
        {[
          {
            id: 'funding-rate',
            title: '#01 资金费率套利 · 风控参数',
            subtitle: 'FUNDING RATE · READ-ONLY MIRROR',
            badge: { text: 'LIVE', tone: 'active' as BadgeTone },
            params: [
              { label: '入场最低 APR',      value: `${parseFloat(data.min_apr_pct || '0').toFixed(2)}%` },
              { label: '候选展示门槛',      value: `${parseFloat(data.scan_threshold_apr_pct || '0').toFixed(2)}%` },
              { label: '同时持仓上限',      value: String(data.max_positions) },
              { label: '总名义上限',        value: `$${parseFloat(data.max_total_notional_usd || '0').toFixed(0)}` },
              { label: '止损',              value: `${parseFloat(data.stop_loss_pct || '0').toFixed(2)}%` },
              { label: '最长持仓',          value: `${parseFloat(data.max_hold_hours || '0').toFixed(0)}h` },
            ],
          },
          ...(pbCfg ? [{
            id: 'perp-basis',
            title: '#02 跨所 funding 差套利 · 风控参数',
            subtitle: 'PERP-BASIS ARB · READ-ONLY MIRROR',
            badge: { text: pbCfg.paper_running ? 'PAPER' : 'IDLE', tone: (pbCfg.paper_running ? 'active' : 'warn') as BadgeTone },
            params: [
              { label: '入场 diff APR',     value: `${parseFloat(pbCfg.min_diff_apr_pct).toFixed(0)}%` },
              { label: '退出 diff APR',     value: `${parseFloat(pbCfg.exit_diff_apr_pct).toFixed(1)}%` },
              { label: '最大持仓',          value: `${parseFloat(pbCfg.max_hold_hours).toFixed(0)}h` },
              { label: '最少持仓',          value: `${parseFloat(pbCfg.min_hold_hours).toFixed(0)}h` },
              { label: '同时持仓上限',      value: String(pbCfg.max_concurrent) },
              { label: '单笔 notional',     value: `$${parseFloat(pbCfg.notional_per_position).toFixed(0)}` },
            ],
          }] : []),
          ...(spCfg ? [{
            id: 'spot-perp',
            title: '#04 期现套利 · 风控参数',
            subtitle: 'SPOT-PERP BASIS · READ-ONLY MIRROR',
            badge: { text: spCfg.live_mode ? 'LIVE' : 'PAPER', tone: (spCfg.live_mode ? 'warn' : 'active') as BadgeTone },
            params: [
              { label: '入场基差阈值',      value: `${parseFloat(spCfg.entry_pct).toFixed(2)}%` },
              { label: 'PREMIUM 阈值',      value: parseFloat(spCfg.entry_pct_premium) > 0 ? `${parseFloat(spCfg.entry_pct_premium).toFixed(2)}%` : '回退' },
              { label: 'DISCOUNT 阈值',     value: parseFloat(spCfg.entry_pct_discount) > 0 ? `${parseFloat(spCfg.entry_pct_discount).toFixed(2)}%` : '回退' },
              { label: '收敛平仓',          value: `${parseFloat(spCfg.exit_pct).toFixed(2)}%` },
              { label: '最长持仓',          value: `${parseFloat(spCfg.max_hold_hours).toFixed(0)}h` },
              { label: '同时持仓上限',      value: String(spCfg.max_concurrent) },
              { label: '单笔 notional',     value: `$${parseFloat(spCfg.notional_per_position).toFixed(0)}` },
              { label: '基差扩大止损',      value: parseFloat(spCfg.stop_basis_widening_pct) > 0 ? `${parseFloat(spCfg.stop_basis_widening_pct).toFixed(2)}%` : '禁用' },
              { label: '方向过滤',          value: spCfg.direction_filter },
            ],
          }] : []),
        ].map((card) => (
          <CardElevated key={card.id} style={{ padding: 20 }} className="animate-in">
            <SectionHeader
              title={card.title}
              subtitle={card.subtitle}
              right={
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <Badge tone={card.badge.tone}>{card.badge.text}</Badge>
                  <Link
                    href={`/strategies/${card.id}`}
                    style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12,
                      color: 'var(--accent-blood)', background: 'transparent',
                      border: '1px solid rgba(227,64,88,0.3)',
                      borderRadius: 'var(--radius-sm)', padding: '4px 8px',
                      cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
                      gap: 4, textDecoration: 'none',
                    }}
                  >
                    <Pencil size={10} />
                    <span>{t('详情页调整')}</span>
                  </Link>
                </div>
              }
            />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, marginTop: 12 }}>
              {card.params.map((p) => (
                <div key={p.label}>
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 12, marginBottom: 4 }}>{p.label}</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-primary)' }}>{p.value}</div>
                </div>
              ))}
            </div>
            <div style={{
              marginTop: 16, paddingTop: 12,
              borderTop: '1px solid var(--border-subtle)',
              fontSize: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)',
              display: 'inline-flex', alignItems: 'center', gap: 4,
            }}>
              <CheckCircle2 size={12} style={{ color: 'var(--accent-emerald)' }} />
              {t('只读视图 · 调参请前往策略详情页')}
            </div>
          </CardElevated>
        ))}
      </div>

      {/* 锁定红线 · 账户级（不属于任何单一策略，独立全宽展示）*/}
      <CardElevated style={{ padding: 20, borderColor: 'var(--accent-blood)' }} className="animate-in">
        <SectionHeader
          title="锁定红线 · 账户级"
          subtitle="TIER 3 · ACCOUNT-LEVEL CIRCUIT BREAKERS"
          right={<Badge tone="active">SAFE</Badge>}
        />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginTop: 12 }}>
          {(() => {
            const dailyDD = parseFloat(summary?.daily_drawdown_pct ?? '0')
            const weeklyDD = parseFloat(summary?.weekly_dd_pct ?? '0')
            const marginPct = parseFloat(summary?.margin_usage_pct ?? '0')
            const fmt = (v: string | undefined, sign: string) =>
              summary === undefined ? '—' : `${sign}${parseFloat(v ?? '0').toFixed(2)}%`
            const fmtPct = (v: string | undefined) =>
              summary === undefined ? '—' : `${parseFloat(v ?? '0').toFixed(1)}%`
            return (
              <>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>单日回撤红线</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>{`-${T_dailyDD.toFixed(1)}%`}</span>
                  </div>
                  <ProgressBar pct={Math.min(100, Math.abs(dailyDD) / T_dailyDD * 100)} tone="success" />
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmt(summary?.daily_drawdown_pct, '-')}</div>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>周回撤红线</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>{`-${T_weeklyDD.toFixed(1)}%`}</span>
                  </div>
                  <ProgressBar pct={Math.min(100, Math.abs(weeklyDD) / T_weeklyDD * 100)} tone="success" />
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmt(summary?.weekly_dd_pct, '-')}</div>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{t('最低保证金率')}</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>{`${T_margin.toFixed(0)}%`}</span>
                  </div>
                  <ProgressBar pct={Math.min(100, marginPct)} tone="success" />
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmtPct(summary?.margin_usage_pct)}</div>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>单交易所占比</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>{`${T_exConc.toFixed(0)}%`}</span>
                  </div>
                  {(() => {
                    const exConc = parseFloat(summary?.max_exchange_concentration_pct ?? '0')
                    return <>
                      <ProgressBar pct={Math.min(100, exConc / T_exConc * 100)} tone={exConc > T_exConc ? 'blood' : 'success'} />
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>
                        当前 {summary === undefined ? '—' : `${exConc.toFixed(1)}%`}
                      </div>
                    </>
                  })()}
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>单币种占比</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>{`${T_symConc.toFixed(0)}%`}</span>
                  </div>
                  {(() => {
                    const symConc = parseFloat(summary?.max_symbol_concentration_pct ?? '0')
                    return <>
                      <ProgressBar pct={Math.min(100, symConc / T_symConc * 100)} tone={symConc > T_symConc ? 'blood' : 'success'} />
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>
                        当前 {summary === undefined ? '—' : `${symConc.toFixed(1)}%`}
                      </div>
                    </>
                  })()}
                </div>
              </>
            )
          })()}
        </div>
        <div style={{
          marginTop: 16,
          paddingTop: 12,
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 14,
          color: 'var(--text-tertiary)',
        }}>
          <Lock size={12} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            账户级硬性熔断,触及任意一条立即停所有策略;修改 /opt/dracula/.env 后 docker compose restart api 即生效
          </span>
        </div>
      </CardElevated>

      {/* 风控事件日志 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="风控事件日志"
          subtitle="RISK EVENT LOG · LAST 30 DAYS"
          right={
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--text-tertiary)' }}>
              <CheckCircle2 size={12} style={{ color: 'var(--accent-emerald)' }} />
              <span>{t('三层风控全部正常')}</span>
            </span>
          }
        />
        <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 14 }}>
          <thead>
            <tr>
              {[t('时间'), '层级', '事件', '触发指标', '数值', '处理'].map((h, i) => (
                <th key={i} style={{
                  textAlign: i === 4 ? 'right' : 'left',
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
            {(eventsData?.data ?? []).map((e: RiskEvent, i: number) => {
              const isNegativeValue = e.value.startsWith('-')
              return (
                <tr key={`${e.time}-${i}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{formatEventTime(e.time)}</td>
                  <td style={{ padding: '10px 12px' }}><Badge tone="warn">{e.tier}</Badge></td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{t(e.event)}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>{e.trigger}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: isNegativeValue ? 'var(--accent-blood)' : 'var(--text-primary)' }}>{e.value}</td>
                  <td style={{ padding: '10px 12px', fontSize: 12, color: ACTION_COLOR[e.action] || 'var(--text-tertiary)' }}>
                    {ACTION_LABEL[e.action] || e.action}
                  </td>
                </tr>
              )
            })}
            {(eventsData?.data ?? []).length === 0 && (
              <tr><td colSpan={6} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('三层风控全部正常')}</td></tr>
            )}
          </tbody>
        </table>
      </CardElevated>
    </div>
  )
}
