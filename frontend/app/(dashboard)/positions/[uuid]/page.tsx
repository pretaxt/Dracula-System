'use client'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { closePosition, getPosition, type Position } from '@/lib/api/positions'
import { getSpotPerpOpportunities } from '@/lib/api/strategies'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, Button, type BadgeTone } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { useT } from '@/components/i18n/I18nProvider'

const STATUS_TONE: Record<string, BadgeTone> = {
  open: 'active',
  closing: 'warn',
  closed: 'paused',
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

const KV_LABEL: React.CSSProperties = {
  color: 'var(--text-tertiary)',
  fontSize: 13,
  fontFamily: 'var(--font-mono)',
  letterSpacing: '0.04em',
}
const KV_VALUE: React.CSSProperties = {
  color: 'var(--text-primary)',
  fontFamily: 'var(--font-mono)',
  fontSize: 14,
  textAlign: 'right',
}

function fmtUsd(v: string | number | undefined | null, sign = false, digits = 4): string {
  if (v == null || v === '') return '—'
  const n = typeof v === 'number' ? v : parseFloat(v)
  if (!Number.isFinite(n)) return '—'
  const s = `$${Math.abs(n).toFixed(digits)}`
  if (sign) return `${n >= 0 ? '+' : '-'}${s}`
  return n < 0 ? `-${s}` : s
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export default function PositionDetailPage({ params }: { params: { uuid: string } }) {
  const { t } = useT()
  const qc = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['position', params.uuid],
    queryFn: () => getPosition(params.uuid),
    refetchInterval: 15_000,
  })

  const isSpotPerp = data?.strategy_instance.includes('spot_perp')
  const { data: spOpps } = useQuery({
    queryKey: ['spot-perp-opps'],
    queryFn: getSpotPerpOpportunities,
    refetchInterval: 15_000,
    enabled: !!isSpotPerp && data?.status === 'open',
  })

  const [pendingClose, setPendingClose] = useState(false)
  const closeMut = useMutation({
    mutationFn: () => closePosition(params.uuid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['position', params.uuid] })
      qc.invalidateQueries({ queryKey: ['positions'] })
      setPendingClose(false)
    },
  })

  if (isLoading) {
    return (
      <div style={{ padding: 48, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
        {t('加载中…')}
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div style={{ padding: 48 }}>
        <Link href="/positions" style={BACK_LINK_STYLE}>
          <ArrowLeft size={14} /><span>{t('返回持仓列表')}</span>
        </Link>
        <h2 style={{ fontFamily: 'var(--font-display)', marginTop: 24, color: 'var(--text-primary)' }}>
          {t('仓位不存在或已删除')}
        </h2>
        <p style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
          uuid: <code>{params.uuid}</code>
        </p>
      </div>
    )
  }

  const p: Position = data
  const meta = p.meta || null
  const direction = meta?.direction
  const isOpen = p.status === 'open'

  // 当前基差（仅 open spot_perp 有意义）
  const currentBasisOpp = spOpps?.data?.find(o => o.symbol === p.symbol)
  const currentBasis = currentBasisOpp ? parseFloat(currentBasisOpp.basis_pct) : null
  const entryBasis = p.target_apr_pct ? parseFloat(p.target_apr_pct) : null
  const captured =
    entryBasis != null && currentBasis != null
      ? Math.abs(entryBasis) - Math.abs(currentBasis)
      : null

  const realized = parseFloat(p.realized_pnl)
  const unrealized = parseFloat(p.unrealized_pnl)
  const funding = parseFloat(p.funding_received)
  const fees = parseFloat(p.fees_paid)
  const netPnl = realized + unrealized + funding

  const events: { time: string; label: string; tone: 'positive' | 'negative' | 'neutral'; detail?: string }[] = []
  if (p.opened_at) {
    let detail = ''
    if (meta?.entry_spot_px && meta?.entry_perp_px) {
      detail = `spot ${meta.entry_spot_px} · perp ${meta.entry_perp_px}`
      if (meta.spot_size) detail += ` · size ${meta.spot_size}`
      if (direction) detail += ` · ${direction}`
    } else if (entryBasis != null) {
      detail = `入场基差 ${entryBasis.toFixed(4)}%`
    }
    events.push({ time: p.opened_at, label: '开仓', tone: 'positive', detail })
  }
  if (p.closed_at) {
    let detail = ''
    if (meta?.close_spot_px && meta?.close_perp_px) {
      detail = `spot ${meta.close_spot_px} · perp ${meta.close_perp_px}`
      if (meta.close_fees) detail += ` · close fees $${meta.close_fees}`
    }
    if (p.exit_reason) detail += detail ? ` · 原因 ${p.exit_reason}` : `原因 ${p.exit_reason}`
    events.push({
      time: p.closed_at, label: '平仓',
      tone: realized >= 0 ? 'positive' : 'negative',
      detail,
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Link href="/positions" style={BACK_LINK_STYLE}>
        <ArrowLeft size={14} /><span>{t('返回持仓列表')}</span>
      </Link>

      {/* Header */}
      <CardElevated style={{ padding: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 16 }}>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              color: 'var(--text-tertiary)',
              letterSpacing: '0.08em',
              marginBottom: 4,
            }}>{p.strategy_instance}</div>
            <h1 style={{
              fontFamily: 'var(--font-display)',
              fontSize: 28,
              margin: 0,
              color: 'var(--text-primary)',
            }}>
              {p.symbol || '—'}
            </h1>
            <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <Badge tone={STATUS_TONE[p.status] || 'paused'}>{p.status.toUpperCase()}</Badge>
              {direction && (
                <Badge tone={direction === 'premium' ? 'active' : 'warn'}>
                  {direction === 'premium' ? '升水 PREMIUM' : '贴水 DISCOUNT'}
                </Badge>
              )}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-tertiary)' }}>
                持仓 {parseFloat(p.days_held).toFixed(2)} 天
              </span>
            </div>
          </div>
          {isOpen && (
            <Button
              variant="secondary"
              onClick={() => setPendingClose(true)}
              style={{ color: 'var(--accent-blood)', borderColor: 'rgba(227,64,88,0.4)' }}
            >平仓</Button>
          )}
        </div>
      </CardElevated>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        {/* PnL 拆解 */}
        <CardElevated style={{ padding: 20 }}>
          <SectionHeader title="盈亏拆解" subtitle="PNL BREAKDOWN" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
            <Row label="名义规模" value={fmtUsd(p.notional_usd, false, 2)} />
            <Row label="已实现 PnL" value={fmtUsd(realized, true)} tone={realized >= 0 ? 'pos' : 'neg'} />
            <Row label="浮动 PnL" value={fmtUsd(unrealized, true)} tone={unrealized >= 0 ? 'pos' : 'neg'} />
            <Row label="资金费累计" value={fmtUsd(funding, true)} tone={funding >= 0 ? 'pos' : 'neg'} />
            <Row label="手续费累计" value={fmtUsd(fees, false)} tone="neg" />
            <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: 8, marginTop: 4 }}>
              <Row
                label="净 PnL"
                value={fmtUsd(netPnl, true)}
                tone={netPnl >= 0 ? 'pos' : 'neg'}
                bold
              />
            </div>
          </div>
        </CardElevated>

        {/* 基差状态 */}
        <CardElevated style={{ padding: 20 }}>
          <SectionHeader title="基差状态" subtitle="BASIS STATE" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
            <Row
              label="入场基差"
              value={entryBasis != null ? `${entryBasis >= 0 ? '+' : ''}${entryBasis.toFixed(4)}%` : '—'}
            />
            <Row
              label={isOpen ? '当前基差' : '平仓基差'}
              value={
                currentBasis != null
                  ? `${currentBasis >= 0 ? '+' : ''}${currentBasis.toFixed(4)}%`
                  : '—'
              }
            />
            <Row
              label="已捕获 (绝对值差)"
              value={captured != null ? `${captured >= 0 ? '+' : ''}${captured.toFixed(4)}%` : '—'}
              tone={captured != null && captured >= 0 ? 'pos' : 'neg'}
            />
            {meta?.entry_spot_px && (
              <Row label="入场现货价" value={meta.entry_spot_px} />
            )}
            {meta?.entry_perp_px && (
              <Row label="入场永续价" value={meta.entry_perp_px} />
            )}
            {meta?.close_spot_px && (
              <Row label="平仓现货价" value={meta.close_spot_px} />
            )}
            {meta?.close_perp_px && (
              <Row label="平仓永续价" value={meta.close_perp_px} />
            )}
          </div>
        </CardElevated>

        {/* 元数据 / 腿信息 */}
        {meta && (
          <CardElevated style={{ padding: 20 }}>
            <SectionHeader title="腿信息" subtitle="LEG META" />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
              {meta.exchange && <Row label="交易所" value={meta.exchange.toUpperCase()} />}
              {meta.spot_size && <Row label="现货数量" value={meta.spot_size} />}
              {meta.perp_size && <Row label="永续数量" value={meta.perp_size} />}
              {meta.borrow_interest && <Row label="借币利息 (USDT)" value={meta.borrow_interest} tone="neg" />}
              {meta.client_id && (
                <Row label="客户单 ID" value={<code style={{ fontSize: 11 }}>{meta.client_id}</code>} />
              )}
            </div>
          </CardElevated>
        )}
      </div>

      {/* 时间线 */}
      <CardElevated style={{ padding: 20 }}>
        <SectionHeader title="时间线" subtitle="TIMELINE" />
        <div style={{ marginTop: 16, position: 'relative', paddingLeft: 24 }}>
          {/* vertical line */}
          <div style={{
            position: 'absolute',
            top: 8, bottom: 8, left: 6,
            width: 2,
            background: 'var(--border-default)',
          }} />
          {events.length === 0 && (
            <div style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
              {t('无事件')}
            </div>
          )}
          {events.map((e, i) => (
            <div key={i} style={{ position: 'relative', marginBottom: 18 }}>
              <div style={{
                position: 'absolute',
                left: -22, top: 4,
                width: 12, height: 12, borderRadius: '50%',
                background:
                  e.tone === 'positive'
                    ? 'var(--accent-emerald)'
                    : e.tone === 'negative'
                    ? 'var(--accent-blood)'
                    : 'var(--text-tertiary)',
                boxShadow: '0 0 6px rgba(0,0,0,0.5)',
              }} />
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 13,
                color: 'var(--text-tertiary)',
                marginBottom: 2,
              }}>{fmtTime(e.time)}</div>
              <div style={{
                fontFamily: 'var(--font-display)',
                fontSize: 16,
                color: 'var(--text-primary)',
                marginBottom: 2,
              }}>{e.label}</div>
              {e.detail && (
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--text-secondary)',
                }}>{e.detail}</div>
              )}
            </div>
          ))}
        </div>
      </CardElevated>

      <ConfirmDialog
        open={pendingClose}
        tone="danger"
        title="平仓确认"
        message={
          <>
            确认要平仓 <strong style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>{p.symbol}</strong> 吗?
            {'\n\n'}此操作不可撤销,系统将立即按市价平掉该仓位的多空两腿。
          </>
        }
        confirmText="确认平仓"
        cancelText="取消"
        loading={closeMut.isPending}
        onConfirm={() => closeMut.mutate()}
        onCancel={() => { if (!closeMut.isPending) setPendingClose(false) }}
      />
    </div>
  )
}

function Row({
  label, value, tone, bold,
}: {
  label: string
  value: React.ReactNode
  tone?: 'pos' | 'neg'
  bold?: boolean
}) {
  const color =
    tone === 'pos'
      ? 'var(--accent-emerald)'
      : tone === 'neg'
      ? 'var(--accent-blood)'
      : 'var(--text-primary)'
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <span style={KV_LABEL}>{label}</span>
      <span style={{ ...KV_VALUE, color, fontWeight: bold ? 600 : 400 }}>{value}</span>
    </div>
  )
}
