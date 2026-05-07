'use client'
import Link from 'next/link'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { getStrategyStatus, startStrategyById, stopStrategyById } from '@/lib/api/strategies'
import { CardElevated } from '@/components/ui/Card'
import { Badge, Button, type BadgeTone } from '@/components/ui/Button'
import { useT } from '@/components/i18n/I18nProvider'
import { STRATEGIES, type StrategyStatus } from '@/lib/strategies/catalog'

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
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['strategy'], queryFn: getStrategyStatus, refetchInterval: 10_000 })
  const startMut = useMutation({ mutationFn: (id: string) => startStrategyById(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }) })
  const stopMut  = useMutation({ mutationFn: (id: string) => stopStrategyById(id),  onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }) })
  const [actionId, setActionId] = useState<string | null>(null)
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

      {/* 12 卡片网格 — 响应式 auto-fit */}
      <div className="strategy-grid">
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
              <Link
                href={`/strategies/${s.id}`}
                style={{
                  flex: 1,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontFamily: 'var(--font-sans)',
                  fontSize: 12,
                  fontWeight: 500,
                  padding: '8px 12px',
                  background: 'var(--bg-card)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--radius-sm)',
                  textDecoration: 'none',
                  cursor: 'pointer',
                  transition: 'all var(--duration-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = 'var(--text-primary)'
                  e.currentTarget.style.borderColor = 'var(--border-strong)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = 'var(--text-secondary)'
                  e.currentTarget.style.borderColor = 'var(--border-default)'
                }}
              >
                {t('查看详情')}
              </Link>
              {s.status === 'DISABLED' ? (
                <Button variant="primary" style={{ flex: 1, fontSize: 12 }}>启用监控</Button>
              ) : s.status === 'MONITOR' ? (
                <Button variant="secondary" style={{ flex: 1, fontSize: 12 }}>推送配置</Button>
              ) : s.status === 'RUNNING' || s.status === 'UNDERWATER' ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    if (!confirm(`停止策略 #${s.num} ${s.zhName}?\n现有持仓不会自动平仓。`)) return
                    setActionId(s.id)
                    stopMut.mutate(s.id, { onSettled: () => setActionId(null) })
                  }}
                  disabled={stopMut.isPending && actionId === s.id}
                  style={{ flex: 1, fontSize: 12, color: 'var(--accent-blood)', borderColor: 'rgba(227,64,88,0.4)' }}
                >
                  {stopMut.isPending && actionId === s.id ? '停止中…' : '停止'}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  onClick={() => {
                    setActionId(s.id)
                    startMut.mutate(s.id, { onSettled: () => setActionId(null) })
                  }}
                  disabled={startMut.isPending && actionId === s.id}
                  style={{ flex: 1, fontSize: 12 }}
                >
                  {startMut.isPending && actionId === s.id ? '启动中…' : '启动'}
                </Button>
              )}
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
