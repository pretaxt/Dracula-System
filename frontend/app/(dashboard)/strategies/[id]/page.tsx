'use client'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle, Play, Square } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, Button, type BadgeTone } from '@/components/ui/Button'
import { useT } from '@/components/i18n/I18nProvider'
import { getStrategyById, type StrategyStatus } from '@/lib/strategies/catalog'
import { getStrategyStatus, startStrategyById, stopStrategyById } from '@/lib/api/strategies'

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
  fontSize: 12,
  color: 'var(--accent-blood)',
  textDecoration: 'none',
  letterSpacing: '0.04em',
} as const

export default function StrategyDetailPage({ params }: { params: { id: string } }) {
  const { t } = useT()
  const qc = useQueryClient()
  const strategy = getStrategyById(params.id)

  const { data: live } = useQuery({
    queryKey: ['strategy'],
    queryFn: getStrategyStatus,
    refetchInterval: 10_000,
    enabled: params.id === 'funding-rate',
  })

  const startMut = useMutation({
    mutationFn: () => startStrategyById(params.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }),
  })
  const stopMut = useMutation({
    mutationFn: () => stopStrategyById(params.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy'] }),
  })

  if (!strategy) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <h2 style={{ fontFamily: 'var(--font-display)', color: 'var(--text-primary)', margin: 0 }}>
          {t('策略不存在')}
        </h2>
        <p style={{ marginTop: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
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
      : strategy.status

  const isRunning = status === 'RUNNING' || status === 'UNDERWATER'

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
                fontSize: 28,
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
                fontSize: 32,
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
              fontSize: 11,
              color: 'var(--text-tertiary)',
              letterSpacing: '0.04em',
            }}
          >
            {strategy.enLabel}
          </p>
        </div>
      </div>

      <div className="kpi-grid">
        <CardElevated style={{ padding: 20 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            {t('分配资金')}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 24, marginTop: 8, color: 'var(--text-primary)' }}>
            {strategy.capital}
          </div>
        </CardElevated>
        <CardElevated style={{ padding: 20 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            {t('月化收益')}
          </div>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 24,
              marginTop: 8,
              color:
                strategy.monthly === null
                  ? 'var(--text-tertiary)'
                  : strategy.monthlyTone === 'negative'
                  ? 'var(--accent-blood)'
                  : 'var(--accent-emerald)',
            }}
          >
            {strategy.monthly ?? '—'}
          </div>
        </CardElevated>
        <CardElevated style={{ padding: 20 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            {t(strategy.posLabel)}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 24, marginTop: 8, color: 'var(--text-primary)' }}>
            {strategy.positions}
          </div>
        </CardElevated>
      </div>

      <CardElevated style={{ padding: 24 }}>
        <SectionHeader title={t('策略简介')} subtitle="STRATEGY THESIS" />
        <p style={{ fontSize: 14, lineHeight: 1.7, color: 'var(--text-secondary)', margin: 0 }}>
          {strategy.desc}
        </p>
        {strategy.thesis && (
          <p style={{ fontSize: 14, lineHeight: 1.7, color: 'var(--text-secondary)', marginTop: 12, marginBottom: 0 }}>
            {strategy.thesis}
          </p>
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
                  fontSize: 13,
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

      <CardElevated style={{ padding: 24 }}>
        <SectionHeader title={t('控制台')} subtitle="CONTROLS" />
        <div
          style={{
            display: 'flex',
            gap: 12,
            flexWrap: 'wrap',
            alignItems: 'center',
            marginTop: 12,
          }}
        >
          {strategy.status === 'DISABLED' ? (
            <>
              <Badge tone="warn">{t('需手动启用')}</Badge>
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  color: 'var(--text-tertiary)',
                }}
              >
                {t('达到资金解锁条件后才能启用')}
              </span>
            </>
          ) : isRunning ? (
            <Button
              variant="secondary"
              onClick={() => {
                if (
                  !confirm(
                    `${t('停止策略')} #${strategy.num} ${t(strategy.zhName)}?\n${t('现有持仓不会自动平仓。')}`,
                  )
                )
                  return
                stopMut.mutate()
              }}
              disabled={stopMut.isPending}
              style={{ color: 'var(--accent-blood)', borderColor: 'rgba(227,64,88,0.4)' }}
            >
              <Square size={14} />
              <span style={{ marginLeft: 6 }}>
                {stopMut.isPending ? t('停止中…') : t('停止策略')}
              </span>
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() => startMut.mutate()}
              disabled={startMut.isPending}
            >
              <Play size={14} />
              <span style={{ marginLeft: 6 }}>
                {startMut.isPending ? t('启动中…') : t('启动策略')}
              </span>
            </Button>
          )}
          {strategy.id !== 'funding-rate' && (
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: 'var(--text-muted)',
              }}
            >
              ⓘ {t('该策略后端为 shell 实现,启动/停止仅返回壳子响应')}
            </span>
          )}
        </div>
      </CardElevated>
    </div>
  )
}
