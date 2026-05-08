'use client'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Lock, CheckCircle2, Pencil, X as XIcon } from 'lucide-react'
import { getRiskLimits, patchRiskLimits, getRiskEvents, type RiskEvent } from '@/lib/api/risk'
import { getDashboardSummary } from '@/lib/api/dashboard'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, Button } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/Stats'
import { useT } from '@/components/i18n/I18nProvider'

type RiskLimits = {
  max_positions: number
  stop_loss_pct: string
  max_hold_hours: string
  min_apr_pct: string
  max_total_notional_usd: string
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
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<RiskLimits>({ queryKey: ['risk'], queryFn: getRiskLimits })
  const { data: eventsData } = useQuery({ queryKey: ['risk-events'], queryFn: () => getRiskEvents(30), refetchInterval: 60_000 })
  const { data: summary } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<{ min_apr_pct: string; max_positions: string; max_total_notional_usd: string }>({
    min_apr_pct: '',
    max_positions: '',
    max_total_notional_usd: '',
  })
  const [confirmWiden, setConfirmWiden] = useState(false)

  const patchMut = useMutation({
    mutationFn: (patch: Record<string, unknown>) => patchRiskLimits(patch, confirmWiden),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['risk'] })
      setEditing(false)
      setConfirmWiden(false)
    },
  })

  if (isLoading || !data) {
    return <div style={{ padding: 48, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{t('加载中…')}</div>
  }

  const minApr = parseFloat(data.min_apr_pct || '0')
  const maxPos = data.max_positions
  const maxNot = parseFloat(data.max_total_notional_usd || '0')

  const startEdit = () => {
    setDraft({
      min_apr_pct: data.min_apr_pct,
      max_positions: String(data.max_positions),
      max_total_notional_usd: data.max_total_notional_usd,
    })
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
    setConfirmWiden(false)
    patchMut.reset()
  }

  const saveEdit = () => {
    const patch: Record<string, unknown> = {}
    if (draft.min_apr_pct !== data.min_apr_pct) patch.min_apr_pct = draft.min_apr_pct
    if (draft.max_positions !== String(data.max_positions)) patch.max_positions = parseInt(draft.max_positions, 10)
    if (draft.max_total_notional_usd !== data.max_total_notional_usd) patch.max_total_notional_usd = draft.max_total_notional_usd
    if (Object.keys(patch).length === 0) {
      cancelEdit()
      return
    }
    patchMut.mutate(patch)
  }

  const isWidening =
    parseFloat(draft.min_apr_pct || '0') < minApr ||
    parseInt(draft.max_positions || '0', 10) > maxPos ||
    parseFloat(draft.max_total_notional_usd || '0') > maxNot

  const errorMsg = patchMut.isError
    ? String((patchMut.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '保存失败')
    : null

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '6px 10px',
    background: 'var(--bg-deepest)',
    border: '1px solid var(--border-default)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)',
    fontFamily: 'var(--font-mono)',
    fontSize: 14,
    outline: 'none',
    boxSizing: 'border-box',
    textAlign: 'right',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, alignItems: 'start' }}>
        {/* Tier 1 — 可编辑 */}
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title={t('Tier 1 · 仪表盘可调')}
            right={
              editing ? (
                <button
                  type="button"
                  onClick={cancelEdit}
                  title="取消"
                  style={{
                    background: 'transparent',
                    color: 'var(--text-tertiary)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 'var(--radius-sm)',
                    padding: 4,
                    cursor: 'pointer',
                    display: 'inline-flex',
                  }}
                >
                  <XIcon size={12} />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={startEdit}
                  title="编辑参数"
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    color: 'var(--accent-blood)',
                    background: 'transparent',
                    border: '1px solid rgba(227,64,88,0.3)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '4px 8px',
                    cursor: 'pointer',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    transition: 'all var(--duration-fast)',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(227,64,88,0.08)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Pencil size={10} />
                  <span>调整</span>
                </button>
              )
            }
          />

          {!editing ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{t('扫描最低 APR')}</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{minApr.toFixed(0)}%</span>
                </div>
                <ProgressBar pct={Math.min(100, (minApr / 25) * 100)} tone="success" />
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{t('最大同时仓位')}</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{maxPos}</span>
                </div>
                <ProgressBar pct={Math.min(100, (maxPos / 10) * 100)} tone="success" />
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>最高名义敞口</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>${maxNot.toFixed(0)}</span>
                </div>
                <ProgressBar pct={50} tone="success" />
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <div style={{ fontSize: 14, marginBottom: 6, color: 'var(--text-secondary)' }}>{t('扫描最低 APR')} (%)</div>
                <input
                  type="number"
                  step="0.1"
                  value={draft.min_apr_pct}
                  onChange={(e) => setDraft((d) => ({ ...d, min_apr_pct: e.target.value }))}
                  style={inputStyle}
                />
              </div>
              <div>
                <div style={{ fontSize: 14, marginBottom: 6, color: 'var(--text-secondary)' }}>{t('最大同时仓位')}</div>
                <input
                  type="number"
                  step="1"
                  min="1"
                  value={draft.max_positions}
                  onChange={(e) => setDraft((d) => ({ ...d, max_positions: e.target.value }))}
                  style={inputStyle}
                />
              </div>
              <div>
                <div style={{ fontSize: 14, marginBottom: 6, color: 'var(--text-secondary)' }}>最高名义敞口 ($)</div>
                <input
                  type="number"
                  step="100"
                  value={draft.max_total_notional_usd}
                  onChange={(e) => setDraft((d) => ({ ...d, max_total_notional_usd: e.target.value }))}
                  style={inputStyle}
                />
              </div>

              {isWidening && (
                <label style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  fontSize: 13,
                  color: 'var(--accent-gold)',
                  background: 'rgba(240,184,80,0.08)',
                  border: '1px solid rgba(240,184,80,0.25)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '8px 10px',
                  cursor: 'pointer',
                  marginTop: 4,
                }}>
                  <input
                    type="checkbox"
                    checked={confirmWiden}
                    onChange={(e) => setConfirmWiden(e.target.checked)}
                    style={{ marginTop: 2, accentColor: 'var(--accent-gold)' }}
                  />
                  <span>检测到放宽风控,确认要继续吗?</span>
                </label>
              )}

              {errorMsg && (
                <div style={{
                  fontSize: 13,
                  color: 'var(--accent-blood)',
                  background: 'rgba(227,64,88,0.08)',
                  border: '1px solid rgba(227,64,88,0.25)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '6px 10px',
                  fontFamily: 'var(--font-mono)',
                }}>
                  {errorMsg}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                <Button
                  variant="primary"
                  onClick={saveEdit}
                  disabled={patchMut.isPending || (isWidening && !confirmWiden)}
                  style={{ flex: 1, fontSize: 14 }}
                >
                  {patchMut.isPending ? '保存中…' : t('保存')}
                </Button>
                <Button variant="secondary" onClick={cancelEdit} style={{ flex: 1, fontSize: 14 }}>
                  {t('取消')}
                </Button>
              </div>
            </div>
          )}
        </CardElevated>

        {/* Tier 2 */}
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader title={t('Tier 2 · 延迟生效')} right={<Badge tone="active">SAFE</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div title="集中度统计待后端实现 / pending backend">
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>单交易所占比</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>— / 50%</span>
              </div>
              <ProgressBar pct={0} tone="success" />
            </div>
            <div title="集中度统计待后端实现 / pending backend">
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>单币种占比</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>— / 20%</span>
              </div>
              <ProgressBar pct={0} tone="success" />
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>止损百分比 (config)</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>{parseFloat(data.stop_loss_pct || '0').toFixed(2)}%</span>
              </div>
              <ProgressBar pct={Math.min(100, parseFloat(data.stop_loss_pct || '0') * 10)} tone="success" />
            </div>
          </div>
        </CardElevated>

        {/* Tier 3 — 锁定红线（红线静态来自 config.yaml；当前值实时来自 dashboard/summary） */}
        <CardElevated style={{ padding: 20, borderColor: 'var(--accent-blood)' }} className="animate-in">
          <SectionHeader title={t('Tier 3 · 锁定红线')} right={<Badge tone="active">SAFE</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
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
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>-3.0%</span>
                    </div>
                    <ProgressBar pct={Math.min(100, Math.abs(dailyDD) / 3.0 * 100)} tone="success" />
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmt(summary?.daily_drawdown_pct, '-')}</div>
                  </div>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                      <span style={{ color: 'var(--text-secondary)' }}>周回撤红线</span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>-8.0%</span>
                    </div>
                    <ProgressBar pct={Math.min(100, Math.abs(weeklyDD) / 8.0 * 100)} tone="success" />
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmt(summary?.weekly_dd_pct, '-')}</div>
                  </div>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                      <span style={{ color: 'var(--text-secondary)' }}>{t('最低保证金率')}</span>
                      <span style={{ fontFamily: 'var(--font-mono)' }}>50%</span>
                    </div>
                    <ProgressBar pct={Math.min(100, marginPct)} tone="success" />
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmtPct(summary?.margin_usage_pct)}</div>
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
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>需修改 config.yaml 重启系统才能调整</span>
          </div>
        </CardElevated>
      </div>

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
                  <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{e.event}</td>
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
