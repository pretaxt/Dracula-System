'use client'
import Link from 'next/link'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, type BadgeTone } from '@/components/ui/Button'
import { useT } from '@/components/i18n/I18nProvider'
import { getStrategyById, type StrategyStatus } from '@/lib/strategies/catalog'
import {
  getStrategyStatus,
  getSpotPerpOpportunities,
  getFundingRateOpportunities,
  getPerpBasisOpportunities,
  getPerpBasisConfig,
  patchPerpBasisConfig,
  getPerpBasisExchangeBalance,
  getSpotPerpConfig,
  patchSpotPerpConfig,
  getCexDexStatus,
  getCexDexConfig,
  getCexDexOpportunities,
  getCexDexWalletBalance,
  getCexDexCexBalance,
  getCexDexSpreads,
  getCexDexPaperHistory,
  patchCexDexConfig,
  type CexDexSpreadEntry,
  type CexDexPaperTrade,
} from '@/lib/api/strategies'
import { getRiskLimits, patchRiskLimits } from '@/lib/api/risk'
import { getDashboardSummary } from '@/lib/api/dashboard'

const INSTANCE_MAP: Record<string, string> = {
  'funding-rate': 'funding_rate_main',
  'spot-perp':    'spot_perp_main',
}

const STATUS_LABEL: Record<StrategyStatus, string> = {
  RUNNING: '运行中',
  PLANNED: '待启动',
  MONITOR: '监控只读',
  DISABLED: '已停用',
  UNDERWATER: '回撤中',
  PAPER: '模拟',
}

const STATUS_TONE: Record<StrategyStatus, BadgeTone> = {
  RUNNING:    'active',
  PAPER:      'info',
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
  const { data: perpBasisOpps } = useQuery({
    queryKey: ['perp-basis-opps'],
    queryFn: getPerpBasisOpportunities,
    refetchInterval: 10_000,
    enabled: params.id === 'perp-basis',
  })
  const { data: perpBasisCfg } = useQuery({
    queryKey: ['perp-basis-cfg'],
    queryFn: getPerpBasisConfig,
    refetchInterval: 30_000,
    enabled: params.id === 'perp-basis',
  })
  const { data: perpBasisBal } = useQuery({
    queryKey: ['perp-basis-bal'],
    queryFn: getPerpBasisExchangeBalance,
    refetchInterval: 30_000,
    enabled: params.id === 'perp-basis',
  })
  // #01 funding-rate 配置（用 risk_limits 全局 API，但 UI 入口聚集到 #01 详情页）
  const { data: frRiskCfg } = useQuery({
    queryKey: ['risk-limits-for-fr'],
    queryFn: getRiskLimits,
    refetchInterval: 30_000,
    enabled: params.id === 'funding-rate',
  })
  // #04 spot-perp 配置
  const { data: spotPerpCfg } = useQuery({
    queryKey: ['spot-perp-cfg'],
    queryFn: getSpotPerpConfig,
    refetchInterval: 30_000,
    enabled: params.id === 'spot-perp',
  })
  const { data: cexDexStat } = useQuery({
    queryKey: ['cex-dex-stat-detail'],
    queryFn: getCexDexStatus,
    refetchInterval: 5_000,
    enabled: params.id === 'cex-dex',
  })
  const { data: cexDexCfg } = useQuery({
    queryKey: ['cex-dex-cfg-detail'],
    queryFn: getCexDexConfig,
    refetchInterval: 30_000,
    enabled: params.id === 'cex-dex',
  })
  const { data: cexDexOpps } = useQuery({
    queryKey: ['cex-dex-opps'],
    queryFn: getCexDexOpportunities,
    refetchInterval: 5_000,
    enabled: params.id === 'cex-dex',
  })
  const { data: cexDexWallet } = useQuery({
    queryKey: ['cex-dex-wallet'],
    queryFn: getCexDexWalletBalance,
    refetchInterval: 30_000,
    enabled: params.id === 'cex-dex',
  })
  const { data: cexDexCexBal } = useQuery({
    queryKey: ['cex-dex-cex-balance'],
    queryFn: getCexDexCexBalance,
    refetchInterval: 60_000,
    enabled: params.id === 'cex-dex',
  })
  const { data: cexDexSpreads } = useQuery({
    queryKey: ['cex-dex-spreads'],
    queryFn: getCexDexSpreads,
    refetchInterval: 5_000,
    enabled: params.id === 'cex-dex',
  })
  const { data: cexDexHistory } = useQuery({
    queryKey: ['cex-dex-paper-history'],
    queryFn: getCexDexPaperHistory,
    refetchInterval: 10_000,
    enabled: params.id === 'cex-dex',
  })
  const queryClient = useQueryClient()
  const patchPbCfg = useMutation({
    mutationFn: patchPerpBasisConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['perp-basis-cfg'] })
      queryClient.invalidateQueries({ queryKey: ['perp-basis-opps'] })
    },
  })
  const patchFrCfg = useMutation({
    mutationFn: (patch: Record<string, unknown>) => patchRiskLimits(patch, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['risk-limits-for-fr'] })
      queryClient.invalidateQueries({ queryKey: ['risk'] })
      queryClient.invalidateQueries({ queryKey: ['funding-rate-opps'] })
    },
  })
  const patchSpCfg = useMutation({
    mutationFn: patchSpotPerpConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spot-perp-cfg'] })
      queryClient.invalidateQueries({ queryKey: ['spot-perp-opps'] })
    },
  })
  const patchCdCfg = useMutation({
    mutationFn: patchCexDexConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cex-dex-cfg-detail'] })
      queryClient.invalidateQueries({ queryKey: ['cex-dex-stat-detail'] })
    },
  })
  // PATCH 表单 local state
  const [pbForm, setPbForm] = useState({
    min_diff_apr_pct: '',
    exit_diff_apr_pct: '',
    max_concurrent: '',
    notional_per_position: '',
    max_hold_hours: '',
    min_hold_hours: '',
  })
  const [frForm, setFrForm] = useState({
    min_apr_pct: '',
    scan_threshold_apr_pct: '',
    max_positions: '',
    max_total_notional_usd: '',
    stop_loss_pct: '',
    max_hold_hours: '',
  })
  const [spForm, setSpForm] = useState({
    entry_pct: '',
    entry_pct_premium: '',
    entry_pct_discount: '',
    exit_pct: '',
    max_hold_hours: '',
    max_concurrent: '',
    notional_per_position: '',
    stop_basis_widening_pct: '',
    scan_threshold_pct: '',
  })
  const [cdForm, setCdForm] = useState({
    min_net_profit_usd: '',
    max_trade_usd: '',
    max_daily_loss_usd: '',
    max_gas_gwei: '',
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
      : strategy.id === 'perp-basis' && perpBasisCfg
      ? perpBasisCfg.paper_running
        ? 'RUNNING'
        : 'PLANNED'
      : strategy.id === 'spot-perp' && spotPerpCfg
      ? spotPerpCfg.session_running
        ? 'RUNNING'
        : 'PLANNED'
      : strategy.id === 'cex-dex' && cexDexStat
      ? cexDexStat.running && cexDexStat.mode === 'live'
        ? 'RUNNING'
        : cexDexStat.running
        ? 'PAPER'
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
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0, whiteSpace: 'nowrap' }}>
              {status === 'RUNNING' && (
                <span
                  className="pulse-blood"
                  aria-label="running"
                  style={{
                    display: 'inline-block', width: 8, height: 8,
                    borderRadius: '50%', background: 'var(--accent-blood)',
                    boxShadow: '0 0 6px var(--accent-blood)',
                    flexShrink: 0,
                  }}
                />
              )}
              <Badge tone={STATUS_TONE[status]}>{t(STATUS_LABEL[status])}</Badge>
            </span>
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
        } else if (strategy.id === 'perp-basis' && perpBasisCfg) {
          // #02 perp-basis：用 perpBasisCfg + perf 同步显示三段
          const sizeUsd = parseFloat(perpBasisCfg.notional_per_position ?? '50')
          const maxPos = perpBasisCfg.max_concurrent ?? 0
          // perf.open_positions = leg 数，跨所策略每仓 2 legs → 实际仓数 = legs / 2
          const legCnt = perf?.open_positions ?? 0
          const openCnt = Math.floor(legCnt / 2) || legCnt  // 兼容旧记录
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
        } else if (strategy.id === 'spot-perp' && spotPerpCfg) {
          // #04 spot-perp：同 #02 模式
          const sizeUsd = parseFloat(spotPerpCfg.notional_per_position ?? '50')
          const maxPos = spotPerpCfg.max_concurrent ?? 0
          const openCnt = perf?.open_positions ?? 0
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
        } else if (strategy.id === 'cex-dex' && cexDexStat) {
          const walletUsd = parseFloat(cexDexWallet?.total_usd ?? '0')
          const accountUsd = parseFloat(String(summary?.total_equity_usd ?? '0'))
          liveCapital = `$${walletUsd.toFixed(0)} (链) / $${accountUsd.toFixed(0)} (CEX)`
          livePositions = String(cexDexStat.open_trades)
          if (cexDexStat.daily_loss_usd > 0) {
            liveMonthly = `-$${cexDexStat.daily_loss_usd.toFixed(2)}`
            liveMonthlyTone = 'negative'
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
                    {[t('币对'), t('方向'), t('现货'), t('永续'), t('基差'), t('基差 %'), t('距入场'), t('状态')].map((h, i) => (
                      <th
                        key={i}
                        style={{
                          textAlign: i === 0 || i === 1 || i === 7 ? 'left' : 'right',
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
                    const absBasis = Math.abs(basisPct)
                    const isPremium = o.direction === 'premium'
                    // 按方向取入场阈值：per-direction > 0 优先，否则回退 entry_pct
                    const entryGeneric = parseFloat(spotPerpOpps?.entry_pct ?? '0')
                    const entryPremium = parseFloat(spotPerpOpps?.entry_pct_premium ?? '0')
                    const entryDiscount = parseFloat(spotPerpOpps?.entry_pct_discount ?? '0')
                    const entryThresh = isPremium
                      ? (entryPremium > 0 ? entryPremium : entryGeneric)
                      : (entryDiscount > 0 ? entryDiscount : entryGeneric)
                    const distance = entryThresh > 0 ? entryThresh - absBasis : 0
                    const passesEntry = entryThresh > 0 && absBasis >= entryThresh
                    const isNear = !passesEntry && distance > 0 && distance <= entryThresh * 0.2
                    const dirColor = isPremium
                      ? 'var(--accent-emerald)'
                      : 'var(--accent-blood)'
                    const statusColor = passesEntry
                      ? 'var(--accent-emerald)'
                      : isNear
                      ? 'var(--accent-gold)'
                      : 'var(--text-tertiary)'
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
                        <td style={{ padding: '12px', textAlign: 'right', color: statusColor }}>
                          {entryThresh <= 0
                            ? '—'
                            : passesEntry
                            ? `✓ ${t('已达')}`
                            : `−${distance.toFixed(2)}%`}
                        </td>
                        <td style={{ padding: '12px' }}>
                          <Badge tone={passesEntry ? 'active' : isNear ? 'warn' : 'paused'}>
                            {entryThresh <= 0
                              ? t('候选')
                              : passesEntry
                              ? t('可开仓')
                              : isNear
                              ? t('接近')
                              : t('候选')}
                          </Badge>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {(() => {
            const scanT = parseFloat(spotPerpOpps?.scan_threshold_pct ?? '0')
            const ePrem = parseFloat(spotPerpOpps?.entry_pct_premium ?? '0')
            const eDisc = parseFloat(spotPerpOpps?.entry_pct_discount ?? '0')
            const eGen = parseFloat(spotPerpOpps?.entry_pct ?? '0')
            const premThresh = ePrem > 0 ? ePrem : eGen
            const discThresh = eDisc > 0 ? eDisc : eGen
            const entryDisplay = premThresh === discThresh
              ? `${premThresh.toFixed(2)}%`
              : `${t('升水')} ${premThresh.toFixed(2)}% / ${t('贴水')} ${discThresh.toFixed(2)}%`
            return (
              <div
                style={{
                  marginTop: 12,
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--text-muted)',
                }}
              >
                {t('扫描候选门槛')} |basis| ≥ {scanT.toFixed(2)}%（{t('仅展示')}）
                ， {t('实盘入场阈值')} |basis| ≥ {entryDisplay}
                （{t('升水/贴水双向自动开平仓')}）· 60 {t('秒扫描')}
              </div>
            )
          })()}
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
                    const windowMin = fundingOpps?.pre_funding_window_minutes ?? 15
                    // 距 funding 结算分钟数；判断是否在 15min 入场窗口内
                    const minutesToFunding = o.next_funding_time_ms > 0
                      ? (o.next_funding_time_ms - Date.now()) / 60000
                      : Infinity
                    const inWindow = minutesToFunding > 0 && minutesToFunding <= windowMin
                    const isNear = !o.passes_entry && dist > 0 && dist <= minApr * 0.2
                    // 状态分级：可开仓（达标+在窗口）/ 等窗口（达标但等结算）/ 接近（差一点）/ 候选
                    const canOpen = o.passes_entry && inWindow
                    const waiting = o.passes_entry && !inWindow
                    const aprColor = canOpen
                      ? 'var(--accent-emerald)'
                      : waiting
                      ? 'var(--accent-azure)'
                      : isNear
                      ? 'var(--accent-gold)'
                      : 'var(--text-secondary)'
                    const distColor = canOpen
                      ? 'var(--accent-emerald)'
                      : waiting
                      ? 'var(--accent-azure)'
                      : isNear
                      ? 'var(--accent-gold)'
                      : 'var(--text-tertiary)'
                    const distLabel = canOpen
                      ? `✓ ${t('已达')}`
                      : waiting
                      ? `⏱ ${minutesToFunding < 60 ? `${minutesToFunding.toFixed(0)}min` : `${(minutesToFunding / 60).toFixed(1)}h`}`
                      : `−${dist.toFixed(2)}%`
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
                        <td style={{ padding: '12px', textAlign: 'right', color: distColor }}>
                          {distLabel}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-tertiary)' }}>
                          {o.history_positive_count}/{o.history_total_count}
                        </td>
                        <td style={{ padding: '12px' }}>
                          <Badge tone={canOpen ? 'active' : waiting ? 'info' : isNear ? 'warn' : 'paused'}>
                            {canOpen ? t('可开仓') : waiting ? t('等窗口') : isNear ? t('接近') : t('候选')}
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

      {/* #02 perp-basis 实时机会扫描器（跨所 funding 差） */}
      {strategy.id === 'perp-basis' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('实时跨所 funding 差')}
            subtitle={`LIVE PERP-BASIS ARB · ${perpBasisOpps?.exchange_pair_count ?? 0} EXCHANGE PAIRS`}
            right={
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 13,
                color: perpBasisOpps?.running ? 'var(--accent-emerald)' : 'var(--text-tertiary)',
              }}>
                {perpBasisOpps?.running ? `● ${t('扫描中')}` : `○ ${t('未启动')}`}
                {perpBasisOpps?.last_scan_at && (
                  <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                    {t('上次扫描')}{' '}
                    {new Date(perpBasisOpps.last_scan_at).toLocaleTimeString('en-US', { hour12: false })}
                  </span>
                )}
              </span>
            }
          />
          {(perpBasisOpps?.data ?? []).length === 0 ? (
            <div style={{
              padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)',
              fontSize: 14, color: 'var(--text-tertiary)',
            }}>
              {perpBasisOpps?.running
                ? t('当前无 funding 差超过门槛的标的')
                : t('扫描器未运行')}
            </div>
          ) : (
            <div className="table-scroll-x">
              <table className="data-table" style={{
                width: '100%', borderCollapse: 'separate', borderSpacing: 0,
                fontFamily: 'var(--font-mono)', fontSize: 14,
              }}>
                <thead>
                  <tr>
                    {[t('币对'), t('long 端'), t('short 端'), t('long APR'), t('short APR'), t('差 APR'), t('健康度'), t('周期 L/S')].map((h, i) => (
                      <th key={i} style={{
                        textAlign: i <= 2 ? 'left' : 'right',
                        padding: '10px 12px', color: 'var(--text-tertiary)',
                        fontWeight: 500, fontSize: 12, letterSpacing: '0.08em',
                        textTransform: 'uppercase', borderBottom: '1px solid var(--border-default)',
                        background: 'var(--bg-deepest)',
                      }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(perpBasisOpps?.data ?? []).map((o) => {
                    const diff = parseFloat(o.diff_apr_pct)
                    const tier = o.health_tier ?? 'safe'
                    // 健康度分级配色 — safe 绿 / risky 金 / dirty 红警示
                    const tierColor = tier === 'dirty'
                      ? 'var(--accent-blood)'
                      : tier === 'risky'
                        ? 'var(--accent-gold)'
                        : 'var(--accent-emerald)'
                    const tierLabel = tier === 'dirty'
                      ? '⚠ dirty'
                      : tier === 'risky'
                        ? '⚡ risky'
                        : '✓ safe'
                    return (
                      <tr key={`${o.symbol}-${o.long_exchange}-${o.short_exchange}`}
                          style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '12px', color: 'var(--text-primary)', fontWeight: 600 }}>
                          {o.symbol}
                        </td>
                        <td style={{ padding: '12px', color: 'var(--accent-emerald)' }}>
                          ↑ {o.long_exchange}
                        </td>
                        <td style={{ padding: '12px', color: 'var(--accent-blood)' }}>
                          ↓ {o.short_exchange}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                          {parseFloat(o.long_apr_pct).toFixed(2)}%
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                          {parseFloat(o.short_apr_pct).toFixed(2)}%
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: tierColor, fontWeight: 600 }}>
                          {diff.toFixed(2)}%
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: tierColor, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                          {tierLabel}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-tertiary)' }}>
                          {o.long_funding_interval_hours}h / {o.short_funding_interval_hours}h
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div style={{
            marginTop: 12, fontFamily: 'var(--font-mono)', fontSize: 12,
            color: 'var(--text-muted)',
          }}>
            {t('入场门槛 funding diff APR ≥')} {parseFloat(perpBasisOpps?.min_diff_apr_pct || '0').toFixed(1)}%
            ， {perpBasisOpps?.exchange_pair_count ?? 0} {t('个交易所组合 · 30 秒扫描 · 数据来自 MarketDataHub')}
          </div>
          <div style={{
            marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 11,
            color: 'var(--text-tertiary)', letterSpacing: '0.04em',
          }}>
            {t('健康度')}: <span style={{ color: 'var(--accent-emerald)' }}>✓ safe</span> {t('≤200%')} ·
            {' '}<span style={{ color: 'var(--accent-gold)' }}>⚡ risky</span> {t('200-500%')} ·
            {' '}<span style={{ color: 'var(--accent-blood)' }}>⚠ dirty</span> {t('>500% (慎入)')}
          </div>
        </CardElevated>
      )}

      {/* #02 perp-basis: per-exchange perp 余额（跨所策略需 ≥2 家就绪）*/}
      {strategy.id === 'perp-basis' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('交易所资金状态')}
            subtitle="PER-EXCHANGE PERP MARGIN"
            right={
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 13,
                color: perpBasisBal?.ready ? 'var(--accent-emerald)' : 'var(--accent-gold)',
              }}>
                {perpBasisBal?.ready ? `● ${t('跨所就绪')}` : `○ ${t('需 ≥2 家配 trading key')}`}
              </span>
            }
          />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: 12,
          }}>
            {(perpBasisBal?.data ?? []).map((b) => (
              <div key={b.exchange} style={{
                padding: 16,
                background: 'var(--bg-deepest)',
                border: `1px solid ${b.ready ? 'var(--accent-emerald)' : 'var(--border-subtle)'}`,
                borderRadius: 4,
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11,
                  color: 'var(--text-tertiary)', textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                }}>
                  {b.exchange}
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 22, marginTop: 6,
                  color: b.ready ? 'var(--accent-emerald)' : 'var(--text-secondary)',
                }}>
                  ${parseFloat(b.perp_usdt_total).toFixed(2)}
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 4,
                  color: 'var(--text-muted)',
                }}>
                  {t('可用')} ${parseFloat(b.perp_usdt_free).toFixed(2)} · {b.ready ? t('已就绪') : t('< $10 不足')}
                </div>
                {/* wallet_breakdown 明细 */}
                {b.wallet_breakdown && Object.keys(b.wallet_breakdown).length > 0 && (
                  <div style={{
                    marginTop: 10, paddingTop: 8,
                    borderTop: '1px solid var(--border-subtle)',
                    display: 'flex', flexDirection: 'column', gap: 3,
                  }}>
                    {Object.entries(b.wallet_breakdown as Record<string, number>).map(([wk, wv]) => (
                      <div key={wk} style={{
                        display: 'flex', justifyContent: 'space-between',
                        fontFamily: 'var(--font-mono)', fontSize: 11,
                      }}>
                        <span style={{ color: 'var(--text-tertiary)' }}>{wk}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>${Number(wv).toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {(perpBasisBal?.data ?? []).length === 0 && (
              <div style={{
                padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)',
                fontSize: 14, color: 'var(--text-tertiary)',
              }}>
                {t('Reconciler 未就绪 — 等 30 秒首轮余额刷新')}
              </div>
            )}
          </div>
        </CardElevated>
      )}

      {/* #02 perp-basis: 配置表单（PATCH 热更新）*/}
      {strategy.id === 'perp-basis' && perpBasisCfg && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('参数配置（PATCH 热更新）')}
            subtitle="LIVE CONFIG · APPLIES NEXT TICK"
            right={
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 12,
                color: perpBasisCfg.paper_running ? 'var(--accent-emerald)' : 'var(--text-tertiary)',
              }}>
                {perpBasisCfg.paper_running ? `● ${t('paper 运行中')}` : `○ ${t('未启动')}`}
              </span>
            }
          />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 16,
          }}>
            {([
              { key: 'min_diff_apr_pct',     label: 'funding diff 入场 (%)', cur: perpBasisCfg.min_diff_apr_pct },
              { key: 'exit_diff_apr_pct',    label: 'diff 衰减退出 (%)',     cur: perpBasisCfg.exit_diff_apr_pct },
              { key: 'max_hold_hours',       label: '最长持仓 (h)',           cur: perpBasisCfg.max_hold_hours },
              { key: 'min_hold_hours',       label: '最少持仓 (h)',           cur: perpBasisCfg.min_hold_hours },
              { key: 'notional_per_position',label: '单笔名义 (USD)',         cur: perpBasisCfg.notional_per_position },
              { key: 'max_concurrent',       label: '同时持仓上限',           cur: String(perpBasisCfg.max_concurrent) },
            ] as const).map((f) => (
              <div key={f.key}>
                <label style={{
                  display: 'block', fontFamily: 'var(--font-mono)', fontSize: 11,
                  color: 'var(--text-tertiary)', textTransform: 'uppercase',
                  letterSpacing: '0.08em', marginBottom: 6,
                }}>
                  {t(f.label)}
                </label>
                <input
                  type="text"
                  placeholder={f.cur}
                  value={(pbForm as Record<string, string>)[f.key]}
                  onChange={(e) => setPbForm({ ...pbForm, [f.key]: e.target.value })}
                  style={{
                    width: '100%', padding: '8px 10px',
                    background: 'var(--bg-deepest)', border: '1px solid var(--border-default)',
                    borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 14,
                    color: 'var(--text-primary)',
                  }}
                />
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4,
                  color: 'var(--text-muted)',
                }}>
                  {t('当前')}: {f.cur}
                </div>
              </div>
            ))}
          </div>
          <div style={{
            marginTop: 20, paddingTop: 16,
            borderTop: '1px solid var(--border-subtle)',
            display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
          }}>
            <button
              onClick={() => {
                const patch: Record<string, unknown> = {}
                if (pbForm.min_diff_apr_pct) patch.min_diff_apr_pct = pbForm.min_diff_apr_pct
                if (pbForm.exit_diff_apr_pct) patch.exit_diff_apr_pct = pbForm.exit_diff_apr_pct
                if (pbForm.max_hold_hours) patch.max_hold_hours = pbForm.max_hold_hours
                if (pbForm.min_hold_hours) patch.min_hold_hours = pbForm.min_hold_hours
                if (pbForm.notional_per_position) patch.notional_per_position = pbForm.notional_per_position
                if (pbForm.max_concurrent) patch.max_concurrent = parseInt(pbForm.max_concurrent, 10)
                if (Object.keys(patch).length === 0) return
                patchPbCfg.mutate(patch)
                setPbForm({
                  min_diff_apr_pct: '', exit_diff_apr_pct: '', max_concurrent: '',
                  notional_per_position: '', max_hold_hours: '', min_hold_hours: '',
                })
              }}
              disabled={patchPbCfg.isPending}
              style={{
                padding: '8px 18px', background: 'var(--accent-blood)', color: 'white',
                border: 'none', borderRadius: 4, fontFamily: 'var(--font-mono)',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
                opacity: patchPbCfg.isPending ? 0.5 : 1, letterSpacing: '0.06em',
              }}
            >
              {patchPbCfg.isPending ? t('应用中...') : t('应用 PATCH')}
            </button>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)',
            }}>
              {t('仅填空字段会被更新，下一 tick (≤30s) 生效')}
            </span>
          </div>
        </CardElevated>
      )}

      {/* #01 funding-rate: 配置表单（risk_limits PATCH，#01 强相关字段集中入口）*/}
      {strategy.id === 'funding-rate' && frRiskCfg && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('参数配置（PATCH 热更新）')}
            subtitle="LIVE CONFIG · APPLIES NEXT TICK"
            right={
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 12,
                color: live?.paper_running ? 'var(--accent-emerald)' : 'var(--text-tertiary)',
              }}>
                {live?.paper_running ? `● ${t('paper 运行中')}` : `○ ${t('未启动')}`}
              </span>
            }
          />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 16,
          }}>
            {([
              { key: 'min_apr_pct',            label: '最低入场 APR (%)',     cur: String(frRiskCfg.min_apr_pct) },
              { key: 'scan_threshold_apr_pct', label: '候选展示门槛 APR (%)', cur: String(frRiskCfg.scan_threshold_apr_pct ?? '0') },
              { key: 'max_positions',          label: '同时持仓上限',         cur: String(frRiskCfg.max_positions) },
              { key: 'max_total_notional_usd', label: '总名义上限 (USD)',     cur: String(frRiskCfg.max_total_notional_usd) },
              { key: 'stop_loss_pct',          label: '止损 (%)',             cur: String(frRiskCfg.stop_loss_pct) },
              { key: 'max_hold_hours',         label: '最长持仓 (h)',         cur: String(frRiskCfg.max_hold_hours) },
            ] as const).map((f) => (
              <div key={f.key}>
                <label style={{
                  display: 'block', fontFamily: 'var(--font-mono)', fontSize: 11,
                  color: 'var(--text-tertiary)', textTransform: 'uppercase',
                  letterSpacing: '0.08em', marginBottom: 6,
                }}>
                  {t(f.label)}
                </label>
                <input
                  type="text"
                  placeholder={f.cur}
                  value={(frForm as Record<string, string>)[f.key]}
                  onChange={(e) => setFrForm({ ...frForm, [f.key]: e.target.value })}
                  style={{
                    width: '100%', padding: '8px 10px',
                    background: 'var(--bg-deepest)', border: '1px solid var(--border-default)',
                    borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 14,
                    color: 'var(--text-primary)',
                  }}
                />
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4,
                  color: 'var(--text-muted)',
                }}>
                  {t('当前')}: {f.cur}
                </div>
              </div>
            ))}
          </div>
          <div style={{
            marginTop: 20, paddingTop: 16,
            borderTop: '1px solid var(--border-subtle)',
            display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
          }}>
            <button
              onClick={() => {
                const patch: Record<string, unknown> = {}
                if (frForm.min_apr_pct) patch.min_apr_pct = frForm.min_apr_pct
                if (frForm.scan_threshold_apr_pct) patch.scan_threshold_apr_pct = frForm.scan_threshold_apr_pct
                if (frForm.max_positions) patch.max_positions = parseInt(frForm.max_positions, 10)
                if (frForm.max_total_notional_usd) patch.max_total_notional_usd = frForm.max_total_notional_usd
                if (frForm.stop_loss_pct) patch.stop_loss_pct = frForm.stop_loss_pct
                if (frForm.max_hold_hours) patch.max_hold_hours = frForm.max_hold_hours
                if (Object.keys(patch).length === 0) return
                patchFrCfg.mutate(patch)
                setFrForm({
                  min_apr_pct: '', scan_threshold_apr_pct: '', max_positions: '',
                  max_total_notional_usd: '', stop_loss_pct: '', max_hold_hours: '',
                })
              }}
              disabled={patchFrCfg.isPending}
              style={{
                padding: '8px 18px', background: 'var(--accent-blood)', color: 'white',
                border: 'none', borderRadius: 4, fontFamily: 'var(--font-mono)',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
                opacity: patchFrCfg.isPending ? 0.5 : 1, letterSpacing: '0.06em',
              }}
            >
              {patchFrCfg.isPending ? t('应用中...') : t('应用 PATCH')}
            </button>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)',
            }}>
              {t('仅填空字段会被更新，下一 tick (≤30s) 生效')}
            </span>
          </div>
        </CardElevated>
      )}

      {/* #04 spot-perp: 配置表单 */}
      {strategy.id === 'spot-perp' && spotPerpCfg && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('参数配置（PATCH 热更新）')}
            subtitle="LIVE CONFIG · APPLIES NEXT TICK"
            right={
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 12,
                color: spotPerpCfg.session_running ? 'var(--accent-emerald)' : 'var(--text-tertiary)',
              }}>
                {spotPerpCfg.session_running ? `● ${t('paper 运行中')}` : `○ ${t('未启动')}`}
              </span>
            }
          />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: 16,
          }}>
            {([
              { key: 'entry_pct',               label: '入场基差通用阈值 (%)',  cur: spotPerpCfg.entry_pct },
              { key: 'entry_pct_premium',       label: 'PREMIUM 阈值 (%)',     cur: spotPerpCfg.entry_pct_premium },
              { key: 'entry_pct_discount',      label: 'DISCOUNT 阈值 (%)',    cur: spotPerpCfg.entry_pct_discount },
              { key: 'exit_pct',                label: '收敛平仓阈值 (%)',     cur: spotPerpCfg.exit_pct },
              { key: 'max_hold_hours',          label: '最长持仓 (h)',          cur: spotPerpCfg.max_hold_hours },
              { key: 'max_concurrent',          label: '同时持仓上限',          cur: String(spotPerpCfg.max_concurrent) },
              { key: 'notional_per_position',   label: '单笔名义 (USD)',        cur: spotPerpCfg.notional_per_position },
              { key: 'stop_basis_widening_pct', label: '基差扩大止损 (%)',     cur: spotPerpCfg.stop_basis_widening_pct },
              { key: 'scan_threshold_pct',      label: '候选展示门槛 (%)',     cur: spotPerpCfg.scan_threshold_pct },
            ] as const).map((f) => (
              <div key={f.key}>
                <label style={{
                  display: 'block', fontFamily: 'var(--font-mono)', fontSize: 11,
                  color: 'var(--text-tertiary)', textTransform: 'uppercase',
                  letterSpacing: '0.08em', marginBottom: 6,
                }}>
                  {t(f.label)}
                </label>
                <input
                  type="text"
                  placeholder={f.cur}
                  value={(spForm as Record<string, string>)[f.key]}
                  onChange={(e) => setSpForm({ ...spForm, [f.key]: e.target.value })}
                  style={{
                    width: '100%', padding: '8px 10px',
                    background: 'var(--bg-deepest)', border: '1px solid var(--border-default)',
                    borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 14,
                    color: 'var(--text-primary)',
                  }}
                />
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4,
                  color: 'var(--text-muted)',
                }}>
                  {t('当前')}: {f.cur}
                </div>
              </div>
            ))}
          </div>
          <div style={{
            marginTop: 20, paddingTop: 16,
            borderTop: '1px solid var(--border-subtle)',
            display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
          }}>
            <button
              onClick={() => {
                const patch: Record<string, unknown> = {}
                if (spForm.entry_pct) patch.entry_pct = spForm.entry_pct
                if (spForm.entry_pct_premium) patch.entry_pct_premium = spForm.entry_pct_premium
                if (spForm.entry_pct_discount) patch.entry_pct_discount = spForm.entry_pct_discount
                if (spForm.exit_pct) patch.exit_pct = spForm.exit_pct
                if (spForm.max_hold_hours) patch.max_hold_hours = spForm.max_hold_hours
                if (spForm.max_concurrent) patch.max_concurrent = parseInt(spForm.max_concurrent, 10)
                if (spForm.notional_per_position) patch.notional_per_position = spForm.notional_per_position
                if (spForm.stop_basis_widening_pct) patch.stop_basis_widening_pct = spForm.stop_basis_widening_pct
                if (spForm.scan_threshold_pct) patch.scan_threshold_pct = spForm.scan_threshold_pct
                if (Object.keys(patch).length === 0) return
                patchSpCfg.mutate(patch)
                setSpForm({
                  entry_pct: '', entry_pct_premium: '', entry_pct_discount: '',
                  exit_pct: '', max_hold_hours: '', max_concurrent: '',
                  notional_per_position: '', stop_basis_widening_pct: '', scan_threshold_pct: '',
                })
              }}
              disabled={patchSpCfg.isPending}
              style={{
                padding: '8px 18px', background: 'var(--accent-blood)', color: 'white',
                border: 'none', borderRadius: 4, fontFamily: 'var(--font-mono)',
                fontSize: 13, fontWeight: 600, cursor: 'pointer',
                opacity: patchSpCfg.isPending ? 0.5 : 1, letterSpacing: '0.06em',
              }}
            >
              {patchSpCfg.isPending ? t('应用中...') : t('应用 PATCH')}
            </button>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)',
            }}>
              {t('仅填空字段会被更新，下一 tick (≤30s) 生效')}
            </span>
          </div>
        </CardElevated>
      )}

      {/* #05 cex-dex: 运行状态卡 */}
      {strategy.id === 'cex-dex' && cexDexStat && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader title={t('运行状态')} subtitle="ARBITRUM ONE · CEX-DEX RUNNER" />
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: 12, marginTop: 8,
          }}>
            {[
              { label: '执行模式', value: cexDexStat.mode.toUpperCase(),
                color: cexDexStat.mode === 'live' ? 'var(--accent-blood)' : cexDexStat.mode === 'paper' ? 'var(--accent-emerald)' : 'var(--text-tertiary)' },
              { label: 'ETH 价格', value: `$${cexDexStat.eth_usd.toLocaleString('en-US', { maximumFractionDigits: 2 })}` },
              { label: '今日亏损', value: `-$${cexDexStat.daily_loss_usd.toFixed(2)}`,
                color: cexDexStat.daily_loss_usd > 0 ? 'var(--accent-blood)' : 'var(--text-primary)' },
              { label: '日损上限', value: `$${cexDexStat.max_daily_loss_usd.toFixed(0)}` },
              { label: '未结成交', value: String(cexDexStat.open_trades) },
            ].map(({ label, value, color }) => (
              <div key={label} style={{
                padding: '12px 16px',
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--bg-card)',
              }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)',
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{label}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: color ?? 'var(--text-primary)' }}>{value}</div>
              </div>
            ))}
          </div>
          {cexDexStat.last_scan_at && (
            <div style={{ marginTop: 12, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
              上次扫描: {new Date(cexDexStat.last_scan_at).toLocaleTimeString('en-US', { hour12: false })}
            </div>
          )}
          {cexDexStat.daily_loss_usd >= cexDexStat.max_daily_loss_usd && cexDexStat.max_daily_loss_usd > 0 && (
            <div style={{ marginTop: 12, padding: '10px 14px',
              background: 'rgba(227,64,88,0.12)', borderRadius: 'var(--radius-sm)',
              fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--accent-blood)' }}>
              🚨 日损熔断已触发 — 今日停止执行
            </div>
          )}
        </CardElevated>
      )}

      {/* #05 cex-dex: Arbitrum 钱包余额 */}
      {strategy.id === 'cex-dex' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('Arbitrum 钱包余额')}
            subtitle="ON-CHAIN WALLET · ARBITRUM ONE"
            right={
              cexDexWallet?.wallet_address
                ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                    {cexDexWallet.wallet_address.slice(0, 8)}…{cexDexWallet.wallet_address.slice(-6)}
                  </span>
                : <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)' }}>未配置</span>
            }
          />
          {!cexDexWallet?.configured ? (
            <div style={{ padding: '16px 0', fontFamily: 'var(--font-mono)', fontSize: 13,
              color: 'var(--text-tertiary)' }}>
              Web3 凭据未配置 → 前往设置页配置钱包私钥
            </div>
          ) : (
            <>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                gap: 10, marginBottom: 16,
              }}>
                {(cexDexWallet.balances ?? []).map((b) => (
                  <div key={b.token} style={{
                    padding: '12px 14px',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-card)',
                  }}>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)',
                      textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>{b.token}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, color: 'var(--text-primary)',
                      fontWeight: 600 }}>{parseFloat(b.amount).toFixed(4)}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)',
                      marginTop: 2 }}>≈ ${b.usd}</div>
                  </div>
                ))}
                {(cexDexWallet.balances ?? []).length === 0 && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-tertiary)', padding: '8px 0' }}>
                    链上余额为零（钱包未充值）
                  </div>
                )}
              </div>
              <div style={{
                padding: '10px 14px',
                background: 'var(--bg-card)',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-default)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-secondary)' }}>
                  链上总估值 (USD)
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 700,
                  color: parseFloat(cexDexWallet.total_usd) > 0 ? 'var(--accent-emerald)' : 'var(--text-tertiary)' }}>
                  ${parseFloat(cexDexWallet.total_usd).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                </span>
              </div>

              {/* CEX side inventory */}
              <div style={{ marginTop: 16, borderTop: '1px solid var(--border-subtle)', paddingTop: 14 }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-gold)',
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
                  CEX 侧库存 (Binance Spot)
                </div>
                {cexDexCexBal?.configured ? (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    {[
                      { label: 'ETH', amount: cexDexCexBal.eth, usd: `$${(parseFloat(cexDexCexBal.eth) * parseFloat(cexDexCexBal.eth_price)).toFixed(2)}` },
                      { label: 'USDT', amount: cexDexCexBal.usdt, usd: `$${parseFloat(cexDexCexBal.usdt).toFixed(2)}` },
                    ].map((b) => (
                      <div key={b.label} style={{ padding: '10px 14px', border: '1px solid var(--border-default)',
                        borderRadius: 'var(--radius-sm)', background: 'var(--bg-card)' }}>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)',
                          textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>{b.label}</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, color: 'var(--text-primary)', fontWeight: 600 }}>{parseFloat(b.amount).toFixed(4)}</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>≈ {b.usd}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-tertiary)' }}>CEX 未连接</div>
                )}
                {cexDexCexBal?.configured && (
                  <div style={{ marginTop: 10, padding: '8px 12px', background: 'var(--bg-card)',
                    borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-default)',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-secondary)' }}>CEX 总估值</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 700,
                      color: parseFloat(cexDexCexBal.total_usd) > 0 ? 'var(--accent-gold)' : 'var(--text-tertiary)' }}>
                      ${parseFloat(cexDexCexBal.total_usd).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                    </span>
                  </div>
                )}
              </div>
            </>
          )}
        </CardElevated>
      )}

      {/* #05 cex-dex: 实时机会扫描 */}
      {strategy.id === 'cex-dex' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('实时价差机会')}
            subtitle="LIVE CEX-DEX OPPORTUNITIES · 5s SCAN"
            right={
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13,
                color: cexDexOpps?.running ? 'var(--accent-emerald)' : 'var(--text-tertiary)' }}>
                {cexDexOpps?.running ? `● ${t('扫描中')}` : `○ ${t('未启动')}`}
                {cexDexOpps?.last_scan_at && (
                  <span style={{ color: 'var(--text-muted)', marginLeft: 8 }}>
                    {new Date(cexDexOpps.last_scan_at).toLocaleTimeString('en-US', { hour12: false })}
                  </span>
                )}
              </span>
            }
          />
          {(cexDexOpps?.data ?? []).length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)',
              fontSize: 14, color: 'var(--text-tertiary)' }}>
              {cexDexOpps?.running
                ? `当前净利 < $${cexDexOpps.min_net_profit_usd} 门槛，无触发机会`
                : t('扫描器未运行')}
            </div>
          ) : (
            <div className="table-scroll-x">
              <table className="data-table" style={{ width: '100%', borderCollapse: 'separate',
                borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 14 }}>
                <thead>
                  <tr>
                    {['币对', '方向', 'CEX 价格', 'DEX 价格', '价差 bps', 'gas 估算', '净利润'].map((h, i) => (
                      <th key={i} style={{
                        textAlign: i <= 1 ? 'left' : 'right',
                        padding: '10px 12px', color: 'var(--text-tertiary)',
                        fontWeight: 500, fontSize: 12, letterSpacing: '0.08em',
                        textTransform: 'uppercase', borderBottom: '1px solid var(--border-default)',
                        background: 'var(--bg-deepest)',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(cexDexOpps?.data ?? []).map((o, idx) => {
                    const net = parseFloat(o.net_profit_usd)
                    const netColor = net > 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)'
                    const dirLabel = o.direction === 'cex_cheap' ? '↑ CEX 低买' : '↓ DEX 低买'
                    const dirColor = o.direction === 'cex_cheap' ? 'var(--accent-emerald)' : 'var(--accent-gold)'
                    return (
                      <tr key={idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '12px', color: 'var(--text-primary)', fontWeight: 600 }}>{o.pair}</td>
                        <td style={{ padding: '12px', color: dirColor, fontWeight: 500 }}>{dirLabel}</td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                          ${parseFloat(o.cex_price).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                          ${parseFloat(o.dex_price).toLocaleString('en-US', { maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                          {parseFloat(o.raw_spread_bps).toFixed(1)}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-muted)' }}>
                          ${parseFloat(o.estimated_gas_usd).toFixed(4)}
                        </td>
                        <td style={{ padding: '12px', textAlign: 'right', color: netColor, fontWeight: 600 }}>
                          ${net.toFixed(3)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardElevated>
      )}


      {/* #05 cex-dex: 实时价差仪表盘 */}
      {strategy.id === 'cex-dex' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('价差监控')}
            subtitle={`LIVE SPREAD · 扫描 ${cexDexSpreads?.scan_count ?? 0} 次`}
            right={
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
                触发门槛 ≥ ~13 bps (净利 ${cexDexSpreads?.threshold_usd ?? 2})
              </span>
            }
          />
          {(cexDexSpreads?.data ?? []).map((s: CexDexSpreadEntry) => {
            const bestSpread = Math.max(s.spread_cex_cheap_bps, s.spread_dex_cheap_bps)
            const TRIGGER_BPS = 13
            const pct = Math.max(0, Math.min(100, (bestSpread / TRIGGER_BPS) * 100))
            const color = bestSpread >= TRIGGER_BPS ? 'var(--accent-emerald)' : bestSpread >= 8 ? 'var(--accent-gold)' : 'var(--text-muted)'
            return (
              <div key={s.pair} style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 13 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text-primary)' }}>{s.pair}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', color }}>
                    {bestSpread.toFixed(1)} bps
                    {bestSpread >= TRIGGER_BPS && <span style={{ marginLeft: 6, color: 'var(--accent-emerald)' }}>🔥 触发</span>}
                  </span>
                </div>
                <div style={{ height: 6, background: 'var(--bg-card)', borderRadius: 3, overflow: 'hidden',
                  border: '1px solid var(--border-subtle)' }}>
                  <div style={{ height: '100%', width: `${pct}%`, background: color,
                    transition: 'width 0.4s ease', borderRadius: 3 }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6,
                  fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                  <span>CEX bid {s.cex_bid.toFixed(2)} / ask {s.cex_ask.toFixed(2)}</span>
                  <span>DEX buy {s.dex_buy.toFixed(2)} / sell {s.dex_sell.toFixed(2)}</span>
                </div>
              </div>
            )
          })}
          {(cexDexSpreads?.data ?? []).length === 0 && (
            <div style={{ padding: 16, fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-tertiary)' }}>
              等待首次扫描...
            </div>
          )}
        </CardElevated>
      )}


      {/* #05 cex-dex: paper 历史记录 */}
      {strategy.id === 'cex-dex' && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader
            title={t('Paper 记录')}
            subtitle={`SIMULATED TRADES · 共 ${cexDexHistory?.total ?? 0} 次触发`}
          />
          {(cexDexHistory?.data ?? []).length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)',
              fontSize: 14, color: 'var(--text-tertiary)' }}>
              暂无 paper 触发记录（运行中，等待价差 ≥ 13 bps）
            </div>
          ) : (
            <div className="table-scroll-x">
              <table className="data-table" style={{ width: '100%', borderCollapse: 'separate',
                borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 13 }}>
                <thead>
                  <tr>
                    {['时间', '币对', '方向', '价差 bps', '净利润', '规模'].map((h, i) => (
                      <th key={i} style={{
                        textAlign: i <= 2 ? 'left' : 'right', padding: '8px 12px',
                        color: 'var(--text-tertiary)', fontWeight: 500, fontSize: 11,
                        letterSpacing: '0.08em', textTransform: 'uppercase',
                        borderBottom: '1px solid var(--border-default)', background: 'var(--bg-deepest)',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(cexDexHistory?.data ?? []).map((t: CexDexPaperTrade, idx: number) => (
                    <tr key={idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '10px 12px', color: 'var(--text-muted)', fontSize: 12 }}>
                        {new Date(t.timestamp).toLocaleTimeString('en-US', { hour12: false })}
                      </td>
                      <td style={{ padding: '10px 12px', color: 'var(--text-primary)', fontWeight: 600 }}>{t.pair}</td>
                      <td style={{ padding: '10px 12px', color: t.direction === 'cex_cheap' ? 'var(--accent-emerald)' : 'var(--accent-gold)' }}>
                        {t.direction === 'cex_cheap' ? '↑ CEX 低买' : '↓ DEX 低买'}
                      </td>
                      <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                        {t.spread_bps.toFixed(1)}
                      </td>
                      <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: 600,
                        color: t.net_profit_usd > 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>
                        ${t.net_profit_usd.toFixed(3)}
                      </td>
                      <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-muted)' }}>
                        ${t.trade_usd}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardElevated>
      )}

      {/* #05 cex-dex: 配置 + 模式切换 */}
      {strategy.id === 'cex-dex' && cexDexCfg && (
        <CardElevated style={{ padding: 24 }}>
          <SectionHeader title={t('配置')} subtitle="CEX-DEX CONFIG PATCH" />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginBottom: 20 }}>
            {/* 当前配置展示 */}
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-gold)',
                textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
                当前值
              </div>
              {[
                { k: '执行模式', v: cexDexCfg.execution_mode },
                { k: '净利门槛', v: `$${cexDexCfg.min_net_profit_usd}` },
                { k: '单笔规模', v: `$${cexDexCfg.max_trade_usd}` },
                { k: '日损上限', v: `$${cexDexCfg.max_daily_loss_usd}` },
                { k: 'gas 上限', v: `${cexDexCfg.max_gas_gwei} Gwei` },
                { k: '滑点保护', v: `${cexDexCfg.max_slippage_bps} bps` },
                { k: 'CEX 交易所', v: cexDexCfg.cex_exchange },
                { k: '扫描间隔', v: `${cexDexCfg.scan_interval_seconds}s` },
              ].map(({ k, v }) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13,
                  padding: '6px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{k}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{v}</span>
                </div>
              ))}
            </div>
            {/* 交易对列表 */}
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-gold)',
                textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
                交易对
              </div>
              {(cexDexCfg.pairs ?? []).map((p) => (
                <div key={p.cex_symbol} style={{ display: 'flex', justifyContent: 'space-between',
                  fontSize: 13, padding: '6px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 600 }}>
                    {p.cex_symbol}
                  </span>
                  <span style={{ color: 'var(--text-muted)' }}>
                    pool fee {p.pool_fee / 10000}%
                  </span>
                </div>
              ))}
            </div>
          </div>
          {/* PATCH 表单 */}
          <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: 20 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-gold)',
              textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>
              热更新（留空 = 不改）
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 16 }}>
              {[
                { key: 'min_net_profit_usd', label: '净利门槛 ($)' },
                { key: 'max_trade_usd', label: '单笔规模 ($)' },
                { key: 'max_daily_loss_usd', label: '日损上限 ($)' },
                { key: 'max_gas_gwei', label: 'gas 上限 (Gwei)' },
              ].map(({ key, label }) => (
                <div key={key}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)',
                    marginBottom: 4 }}>{label}</div>
                  <input
                    type="number"
                    value={cdForm[key as keyof typeof cdForm]}
                    onChange={(e) => setCdForm((f) => ({ ...f, [key]: e.target.value }))}
                    placeholder={String(cexDexCfg[key as keyof typeof cexDexCfg] ?? '')}
                    style={{
                      width: '100%', background: 'var(--bg-card)', border: '1px solid var(--border-default)',
                      borderRadius: 4, padding: '6px 10px', color: 'var(--text-primary)',
                      fontFamily: 'var(--font-mono)', fontSize: 13, boxSizing: 'border-box',
                    }}
                  />
                </div>
              ))}
            </div>
            {/* 模式切换 */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 8 }}>
                执行模式切换
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                {(['paper', 'live'] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => {
                      if (mode === 'live' && !confirm('切换到 LIVE 模式将触发真实链上交易和 CEX 下单。确认？')) return
                      patchCdCfg.mutate({ execution_mode: mode })
                    }}
                    style={{
                      padding: '6px 16px',
                      background: cexDexCfg.execution_mode === mode
                        ? (mode === 'live' ? 'var(--accent-blood)' : 'var(--accent-emerald)')
                        : 'var(--bg-card)',
                      color: cexDexCfg.execution_mode === mode ? '#fff' : 'var(--text-secondary)',
                      border: `1px solid ${cexDexCfg.execution_mode === mode
                        ? (mode === 'live' ? 'var(--accent-blood)' : 'var(--accent-emerald)')
                        : 'var(--border-default)'}`,
                      borderRadius: 4, cursor: 'pointer',
                      fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600,
                      letterSpacing: '0.06em',
                    }}
                  >
                    {mode.toUpperCase()}
                  </button>
                ))}
              </div>
              {cexDexCfg.execution_mode === 'live' && (
                <div style={{ marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 12,
                  color: 'var(--accent-blood)' }}>
                  ⚠ LIVE 模式 — 真实下单中
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button
                onClick={() => {
                  const patch: Record<string, number> = {}
                  if (cdForm.min_net_profit_usd) patch.min_net_profit_usd = parseFloat(cdForm.min_net_profit_usd)
                  if (cdForm.max_trade_usd) patch.max_trade_usd = parseFloat(cdForm.max_trade_usd)
                  if (cdForm.max_daily_loss_usd) patch.max_daily_loss_usd = parseFloat(cdForm.max_daily_loss_usd)
                  if (cdForm.max_gas_gwei) patch.max_gas_gwei = parseFloat(cdForm.max_gas_gwei)
                  if (Object.keys(patch).length === 0) return
                  patchCdCfg.mutate(patch)
                  setCdForm({ min_net_profit_usd: '', max_trade_usd: '', max_daily_loss_usd: '', max_gas_gwei: '' })
                }}
                disabled={patchCdCfg.isPending || Object.values(cdForm).every((v) => !v)}
                style={{
                  padding: '8px 16px',
                  background: 'var(--accent-emerald)', color: '#fff',
                  border: 'none', borderRadius: 4, fontFamily: 'var(--font-mono)',
                  fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  opacity: patchCdCfg.isPending ? 0.5 : 1, letterSpacing: '0.06em',
                }}
              >
                {patchCdCfg.isPending ? t('应用中...') : t('应用 PATCH')}
              </button>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                热更新立即生效（下次 tick）
              </span>
            </div>
          </div>
        </CardElevated>
      )}

      {/* 启动/停止控制移至策略中心列表页(避免在详情页与列表页重复) */}
    </div>
  )
}
