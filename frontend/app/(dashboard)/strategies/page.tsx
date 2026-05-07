'use client'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getStrategyStatus } from '@/lib/api/strategies'
import { CardElevated } from '@/components/ui/Card'
import { Badge, Button, type BadgeTone } from '@/components/ui/Button'
import { useT } from '@/components/i18n/I18nProvider'

type Phase = 'P0' | 'P1' | 'P3'
type StrategyStatus = 'RUNNING' | 'PLANNED' | 'MONITOR' | 'DISABLED' | 'UNDERWATER'

type Strategy = {
  num: string
  zhName: string
  enLabel: string
  phase: Phase
  status: StrategyStatus
  capital: string
  monthly: string | null
  positions: string
  posLabel: string
  desc: string
  monthlyTone?: 'positive' | 'negative'
}

// 12 个保留策略 (2026-05-07 决策, 砍掉 #8 #11 #12 #15 #17)
const STRATEGIES: Strategy[] = [
  { num: '01', zhName: '资金费率套利', enLabel: 'FUNDING RATE ARBITRAGE · 主力 P0', phase: 'P0', status: 'RUNNING',
    capital: '$2,000', monthly: '+1.74%', positions: '3 / 5', posLabel: '持仓',
    desc: '用 Delta 中性的姿势收资金费率,白嫖多头给空头交的钱。', monthlyTone: 'positive' },
  { num: '04', zhName: '期现套利', enLabel: 'SPOT-PERP PREMIUM · P0', phase: 'P0', status: 'RUNNING',
    capital: '$1,000', monthly: '+1.42%', positions: '2 / 5', posLabel: '持仓',
    desc: '抓"短期溢价扩大→收敛"的窗口,跟资金费率套利不同时间尺度。', monthlyTone: 'positive' },
  { num: '13', zhName: '三角套利', enLabel: 'TRIANGULAR ARBITRAGE · P0', phase: 'P0', status: 'RUNNING',
    capital: '$500', monthly: '+0.62%', positions: '14 次', posLabel: '今日触发',
    desc: '用 3 笔交易吃同一交易所内不同币对之间的微小价差。', monthlyTone: 'positive' },
  { num: '02', zhName: '跨所基差套利', enLabel: 'PERP BASIS ARB · P1', phase: 'P1', status: 'RUNNING',
    capital: '$1,000', monthly: '+0.83%', positions: '1 / 3', posLabel: '持仓',
    desc: '做多便宜的合约,做空贵的合约,等基差收敛。', monthlyTone: 'positive' },
  { num: '03', zhName: '跨所价差套利', enLabel: 'SPOT SPREAD ARB · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '抓不同交易所现货价格的瞬时差异 (低延迟+提币速度敏感)。' },
  { num: '16', zhName: '配对交易', enLabel: 'PAIRS TRADING · P1', phase: 'P1', status: 'UNDERWATER',
    capital: '$5,000', monthly: '-0.64%', positions: '2 / 5', posLabel: '配对',
    desc: 'ETH-BNB 配对当前 z-score = -2.4,等待回归到 0。', monthlyTone: 'negative' },
  { num: '05', zhName: 'CEX-DEX 套利', enLabel: 'CROSS-EXCHANGE · MONITOR ONLY', phase: 'P1', status: 'MONITOR',
    capital: '$0', monthly: null, positions: '23 次', posLabel: '本月推送',
    desc: '监控 CEX 和 DEX 之间的价差,推送有利润机会(MEV 风险下不自动执行)。' },
  { num: '06', zhName: '期权波动率套利', enLabel: 'OPTIONS VOL ARB · P3', phase: 'P3', status: 'DISABLED',
    capital: '$0', monthly: null, positions: '$30k+', posLabel: '解锁条件',
    desc: '资金已达解锁线,但高风险策略默认禁用。需要手动启用并先进入监控模式。' },
  { num: '07', zhName: '网格策略', enLabel: 'GRID STRATEGY · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '区间内自动买卖,震荡行情友好,趋势行情吃亏。' },
  { num: '09', zhName: '做市策略', enLabel: 'MARKET MAKING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '订单簿挂单赚价差,需要做市返佣资格 + 极低延迟。' },
  { num: '10', zhName: '趋势跟踪', enLabel: 'TREND FOLLOWING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '技术指标识别中长期趋势,胜率不稳定,赚大输小。' },
  { num: '14', zhName: '稳定币利率套利', enLabel: 'STABLECOIN YIELD · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '跨平台借贷利率差套利,低风险但收益微薄。' },
]

const STATUS_TONE: Record<StrategyStatus, BadgeTone> = {
  RUNNING:    'active',
  PLANNED:    'paused',
  MONITOR:    'info',
  DISABLED:   'paused',
  UNDERWATER: 'warn',
}

type Filter = 'all' | 'running' | 'p0' | 'p1' | 'planned'

export default function StrategiesPage() {
  const { t } = useT()
  const { data } = useQuery({ queryKey: ['strategy'], queryFn: getStrategyStatus, refetchInterval: 10_000 })
  const [filter, setFilter] = useState<Filter>('all')

  // funding_rate 真状态合并到 #01 卡片
  const fundingRunning = data?.paper_running ?? false
  const merged = STRATEGIES.map((s) =>
    s.num === '01'
      ? { ...s, status: (fundingRunning ? 'RUNNING' : 'PLANNED') as StrategyStatus }
      : s,
  )

  const filtered = merged.filter((s) => {
    if (filter === 'all') return true
    if (filter === 'running') return s.status === 'RUNNING' || s.status === 'UNDERWATER'
    if (filter === 'p0') return s.phase === 'P0'
    if (filter === 'p1') return s.phase === 'P1'
    if (filter === 'planned') return s.status === 'PLANNED'
    return true
  })

  const runningCount = merged.filter((s) => s.status === 'RUNNING' || s.status === 'UNDERWATER').length
  const monitorCount = merged.filter((s) => s.status === 'MONITOR').length

  return (
    <div>
      <p style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 24 }}>
        {merged.length} 个策略 · {runningCount} 个运行中 · {monitorCount} 个监控
      </p>

      {/* 筛选条 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 24, flexWrap: 'wrap' }}>
        {([
          { k: 'all',     l: `${t('全部')} ${merged.length}` },
          { k: 'running', l: `运行中 ${runningCount}` },
          { k: 'p0',      l: 'Phase 0 主力' },
          { k: 'p1',      l: 'Phase 1+' },
          { k: 'planned', l: 'PLANNED' },
        ] as const).map(({ k, l }) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              padding: '6px 12px',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              background: filter === k ? 'var(--accent-blood)' : 'var(--bg-card)',
              color: filter === k ? '#fff' : 'var(--text-secondary)',
              border: filter === k ? '1px solid var(--accent-blood)' : '1px solid var(--border-default)',
              transition: 'all var(--duration-fast)',
            }}
          >
            {l}
          </button>
        ))}
      </div>

      {/* 12 卡片网格 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {filtered.map((s) => (
          <CardElevated
            key={s.num}
            style={{
              padding: 20,
              opacity: s.status === 'DISABLED' ? 0.6 : 1,
            }}
            className="animate-in"
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 24,
                  color: s.status === 'DISABLED' ? 'var(--text-muted)' : 'var(--accent-blood)',
                  width: 28,
                  fontWeight: 500,
                }}>
                  {s.num}
                </div>
                <div>
                  <h4 style={{
                    margin: 0,
                    fontFamily: 'var(--font-display)',
                    fontSize: 18,
                    fontWeight: 600,
                    letterSpacing: '0.04em',
                    lineHeight: 1.2,
                    color: 'var(--text-primary)',
                  }}>
                    {t(s.zhName)}
                  </h4>
                  <p style={{
                    margin: '4px 0 0',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    color: 'var(--text-tertiary)',
                    letterSpacing: '0.04em',
                  }}>
                    {s.enLabel}
                  </p>
                </div>
              </div>
              <Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge>
            </div>

            {/* 3 列指标 */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 12,
              margin: '16px 0',
              padding: '12px 0',
              borderTop: '1px solid var(--border-subtle)',
              borderBottom: '1px solid var(--border-subtle)',
            }}>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>分配资金</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, marginTop: 4, color: 'var(--text-primary)' }}>{s.capital}</div>
              </div>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>月化收益</div>
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 14,
                  marginTop: 4,
                  color: s.monthly === null ? 'var(--text-tertiary)'
                       : s.monthlyTone === 'negative' ? 'var(--accent-blood)'
                       : s.monthlyTone === 'positive' ? 'var(--accent-emerald)'
                       : 'var(--text-primary)',
                }}>
                  {s.monthly ?? '—'}
                </div>
              </div>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{s.posLabel}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, marginTop: 4, color: 'var(--text-primary)' }}>{s.positions}</div>
              </div>
            </div>

            <p style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text-secondary)', margin: 0, minHeight: 36 }}>
              {s.desc}
            </p>

            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <Button variant="secondary" style={{ flex: 1, fontSize: 12 }}>{t('查看详情')}</Button>
              <Button
                variant={s.status === 'DISABLED' ? 'primary' : 'secondary'}
                style={{ flex: 1, fontSize: 12 }}
              >
                {s.status === 'DISABLED' ? '启用监控' : t('配置')}
              </Button>
            </div>
          </CardElevated>
        ))}
      </div>

      {filtered.length === 0 && (
        <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
          没有匹配的策略
        </div>
      )}
    </div>
  )
}
