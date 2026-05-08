'use client'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getPositions, closePosition } from '@/lib/api/positions'
import { getOrders, type Order } from '@/lib/api/orders'
import { Card, CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, Button, StatusDot } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { useT } from '@/components/i18n/I18nProvider'

type Position = {
  uuid: string
  symbol: string
  strategy_instance: string
  status: string
  notional_usd: string
  target_apr_pct: string | null
  unrealized_pnl: string
  realized_pnl: string
  funding_received: string
  fees_paid: string
  days_held: string
}

function sideTone(side: string): 'positive' | 'negative' | 'neutral' {
  if (side.includes('open') || side === 'BUY') return 'positive'
  if (side.includes('close') || side === 'SELL') return 'negative'
  return 'neutral'
}

export default function PositionsPage() {
  const { t } = useT()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['positions', 'open'], queryFn: () => getPositions({ status: 'open', page_size: 50 }), refetchInterval: 15_000 })
  const { data: ordersData } = useQuery({ queryKey: ['orders'], queryFn: () => getOrders(20), refetchInterval: 30_000 })
  const [pendingClose, setPendingClose] = useState<{ uuid: string; symbol: string } | null>(null)
  const closeMut = useMutation({
    mutationFn: (uuid: string) => closePosition(uuid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['positions'] })
      setPendingClose(null)
    },
  })

  const positions: Position[] = data?.data ?? []
  const totalCount     = positions.length
  const totalNotional  = positions.reduce((s, p) => s + parseFloat(p.notional_usd), 0)
  const totalUnrealized = positions.reduce((s, p) => s + parseFloat(p.unrealized_pnl), 0)
  const totalRealized   = positions.reduce((s, p) => s + parseFloat(p.realized_pnl) + parseFloat(p.funding_received), 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 4 KPI */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{t('持仓总数')}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--text-primary)' }}>{totalCount}</div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>名义价值</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: 'var(--text-primary)' }}>${totalNotional.toFixed(0)}</div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>{t('浮动盈亏')}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: totalUnrealized >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>
            {totalUnrealized >= 0 ? '+' : ''}${totalUnrealized.toFixed(2)}
          </div>
        </Card>
        <Card style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>已实现盈亏</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-2xl)', marginTop: 8, color: totalRealized >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>
            {totalRealized >= 0 ? '+' : ''}${totalRealized.toFixed(2)}
          </div>
        </Card>
      </div>

      {/* 当前持仓 */}
      <CardElevated style={{ padding: 20 }}>
        <SectionHeader
          title={t('当前持仓')}
          right={
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="secondary" style={{ fontSize: 12 }}>导出 CSV</Button>
              <Button variant="secondary" style={{ fontSize: 12, color: 'var(--accent-blood)', borderColor: 'rgba(227,64,88,0.4)' }}>紧急平仓所有</Button>
            </div>
          }
        />
        <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <thead>
            <tr>
              {[t('策略'), t('币对'), t('状态'), t('规模'), 'APR', t('资金费'), 'PnL', t('持仓'), t('操作')].map((h, i) => (
                <th key={i} style={{
                  textAlign: i >= 3 && i <= 7 ? 'right' : 'left',
                  padding: '8px 12px',
                  color: 'var(--text-tertiary)',
                  fontSize: 10,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid var(--border-default)',
                  background: 'var(--bg-deepest)',
                  fontWeight: 500,
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>{t('加载中…')}</td></tr>
            )}
            {positions.map((p) => {
              const apr  = p.target_apr_pct ? parseFloat(p.target_apr_pct) : null
              const pnl  = parseFloat(p.realized_pnl) + parseFloat(p.unrealized_pnl) + parseFloat(p.funding_received)
              const days = parseFloat(p.days_held)
              const tone = p.status === 'open' ? 'active' : p.status === 'closing' ? 'warn' : 'paused'
              return (
                <tr key={p.uuid} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '10px 12px' }}>
                    <Badge tone="active">{p.strategy_instance.includes('funding') ? t('资金费率') : p.strategy_instance}</Badge>
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{p.symbol}</td>
                  <td style={{ padding: '10px 12px' }}><StatusDot tone={tone} /></td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>${parseFloat(p.notional_usd).toFixed(0)}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--accent-emerald)' }}>{apr !== null ? `${apr.toFixed(2)}%` : '—'}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>${parseFloat(p.funding_received).toFixed(4)}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: pnl >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)' }}>
                    {pnl >= 0 ? '+' : ''}${pnl.toFixed(4)}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-tertiary)' }}>{days.toFixed(1)}d</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right' }}>
                    {p.status === 'open' && (
                      <button
                        onClick={() => setPendingClose({ uuid: p.uuid, symbol: p.symbol })}
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10,
                          color: 'var(--accent-blood)',
                          background: 'transparent',
                          border: '1px solid rgba(227,64,88,0.4)',
                          borderRadius: 'var(--radius-sm)',
                          padding: '4px 10px',
                          cursor: 'pointer',
                        }}
                      >
                        平仓
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
            {!isLoading && positions.length === 0 && (
              <tr><td colSpan={9} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>无持仓</td></tr>
            )}
          </tbody>
        </table>
        {data?.meta && (
          <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            {data.meta.total} 条记录 · 第 {data.meta.page} 页
          </div>
        )}
      </CardElevated>

      {/* 最近订单 */}
      <CardElevated style={{ padding: 20 }}>
        <SectionHeader title={t('最近订单')} subtitle="RECENT ORDERS · LAST 20" />
        <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <thead>
            <tr>
              {[t('时间'), t('交易所'), t('币对'), t('类型'), t('方向'), t('数量'), t('成交均价'), t('状态')].map((h, i) => (
                <th key={i} style={{
                  textAlign: i >= 5 && i <= 6 ? 'right' : 'left',
                  padding: '8px 12px',
                  color: 'var(--text-tertiary)',
                  fontSize: 10,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid var(--border-default)',
                  background: 'var(--bg-deepest)',
                  fontWeight: 500,
                }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(ordersData?.data ?? []).map((o: Order, i: number) => {
              const tone = sideTone(o.side)
              const time = new Date(o.time)
              return (
                <tr key={`${o.position_uuid}-${i}`} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>
                    {time.toISOString().substring(11, 19)}
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{o.exchange}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{o.symbol}</td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>{o.order_type}</td>
                  <td style={{
                    padding: '10px 12px',
                    color: tone === 'positive' ? 'var(--accent-emerald)' : tone === 'negative' ? 'var(--accent-blood)' : 'var(--text-secondary)',
                  }}>
                    {o.side}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>${o.amount}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: 'var(--text-primary)' }}>{o.price}</td>
                  <td style={{ padding: '10px 12px' }}><Badge tone="active">{o.status.toUpperCase()}</Badge></td>
                </tr>
              )
            })}
            {(ordersData?.data ?? []).length === 0 && (
              <tr><td colSpan={8} style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>暂无订单</td></tr>
            )}
          </tbody>
        </table>
      </CardElevated>

      <ConfirmDialog
        open={pendingClose !== null}
        tone="danger"
        title="平仓确认"
        message={
          <>
            确认要平仓 <strong style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>{pendingClose?.symbol}</strong> 吗?
            {'\n\n'}此操作不可撤销,系统将立即按市价平掉该仓位的多空两腿。
          </>
        }
        confirmText="确认平仓"
        cancelText="取消"
        loading={closeMut.isPending}
        onConfirm={() => {
          if (pendingClose) closeMut.mutate(pendingClose.uuid)
        }}
        onCancel={() => {
          if (!closeMut.isPending) setPendingClose(null)
        }}
      />
    </div>
  )
}
