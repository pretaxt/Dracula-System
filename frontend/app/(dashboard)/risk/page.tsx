'use client'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Lock, CheckCircle2, Pencil, X as XIcon } from 'lucide-react'
import { getRiskLimits, patchRiskLimits, getRiskEvents, type RiskEvent } from '@/lib/api/risk'
import { getDashboardSummary } from '@/lib/api/dashboard'
import {
  getSpotPerpConfig,
  patchSpotPerpConfig,
  type SpotPerpConfig,
  type SpotPerpConfigPatch,
  getPerpBasisConfig,
  patchPerpBasisConfig,
  type PerpBasisConfig,
  type PerpBasisConfigPatch,
} from '@/lib/api/strategies'
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
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<RiskLimits>({ queryKey: ['risk'], queryFn: getRiskLimits })
  const { data: eventsData } = useQuery({ queryKey: ['risk-events'], queryFn: () => getRiskEvents(30), refetchInterval: 60_000 })
  const { data: summary } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboardSummary, refetchInterval: 30_000 })

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<{
    min_apr_pct: string
    max_positions: string
    max_total_notional_usd: string
    stop_loss_pct: string
    max_hold_hours: string
    scan_threshold_apr_pct: string
  }>({
    min_apr_pct: '',
    max_positions: '',
    max_total_notional_usd: '',
    stop_loss_pct: '',
    max_hold_hours: '',
    scan_threshold_apr_pct: '',
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

  // ── #04 spot-perp 配置（D.1.5）──
  const { data: spCfg } = useQuery<SpotPerpConfig>({
    queryKey: ['spot-perp-config'],
    queryFn: getSpotPerpConfig,
    refetchInterval: 60_000,
    retry: false,
  })
  const [spEditing, setSpEditing] = useState(false)
  const [spDraft, setSpDraft] = useState<SpotPerpConfigPatch>({})
  const spPatchMut = useMutation({
    mutationFn: patchSpotPerpConfig,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['spot-perp-config'] })
      setSpEditing(false)
    },
  })
  const spStartEdit = () => {
    if (!spCfg) return
    setSpDraft({
      entry_pct: spCfg.entry_pct,
      entry_pct_premium: spCfg.entry_pct_premium,
      entry_pct_discount: spCfg.entry_pct_discount,
      exit_pct: spCfg.exit_pct,
      max_hold_hours: spCfg.max_hold_hours,
      stop_basis_widening_pct: spCfg.stop_basis_widening_pct,
      peak_window_minutes: spCfg.peak_window_minutes,
      min_peak_dropoff_pct: spCfg.min_peak_dropoff_pct,
      max_concurrent: spCfg.max_concurrent,
      notional_per_position: spCfg.notional_per_position,
      direction_filter: spCfg.direction_filter,
      scan_threshold_pct: spCfg.scan_threshold_pct,
    })
    setSpEditing(true)
  }
  const spCancelEdit = () => { setSpEditing(false); spPatchMut.reset() }
  const spSaveEdit = () => {
    if (!spCfg) return
    const patch: SpotPerpConfigPatch = {}
    for (const k of Object.keys(spDraft) as (keyof SpotPerpConfigPatch)[]) {
      const newV = spDraft[k]
      const oldV = spCfg[k as keyof SpotPerpConfig] as unknown
      if (newV !== undefined && String(newV) !== String(oldV)) {
        // @ts-expect-error 动态赋值，类型已收窄
        patch[k] = newV
      }
    }
    if (Object.keys(patch).length === 0) { spCancelEdit(); return }
    spPatchMut.mutate(patch)
  }

  // ── #02 perp-basis 配置 ──
  const { data: pbCfg } = useQuery<PerpBasisConfig>({
    queryKey: ['perp-basis-config'],
    queryFn: getPerpBasisConfig,
    refetchInterval: 60_000,
    retry: false,
  })
  const [pbEditing, setPbEditing] = useState(false)
  const [pbDraft, setPbDraft] = useState<PerpBasisConfigPatch>({})
  const pbPatchMut = useMutation({
    mutationFn: patchPerpBasisConfig,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['perp-basis-config'] })
      setPbEditing(false)
    },
  })
  const pbStartEdit = () => {
    if (!pbCfg) return
    setPbDraft({
      min_diff_apr_pct: pbCfg.min_diff_apr_pct,
      exit_diff_apr_pct: pbCfg.exit_diff_apr_pct,
      max_hold_hours: pbCfg.max_hold_hours,
      min_hold_hours: pbCfg.min_hold_hours,
      max_concurrent: pbCfg.max_concurrent,
      notional_per_position: pbCfg.notional_per_position,
    })
    setPbEditing(true)
  }
  const pbCancelEdit = () => { setPbEditing(false); pbPatchMut.reset() }
  const pbSaveEdit = () => {
    if (!pbCfg) return
    const patch: PerpBasisConfigPatch = {}
    for (const k of Object.keys(pbDraft) as (keyof PerpBasisConfigPatch)[]) {
      const newV = pbDraft[k]
      const oldV = pbCfg[k as keyof PerpBasisConfig] as unknown
      if (newV !== undefined && String(newV) !== String(oldV)) {
        // @ts-expect-error 动态赋值，类型已收窄
        patch[k] = newV
      }
    }
    if (Object.keys(patch).length === 0) { pbCancelEdit(); return }
    pbPatchMut.mutate(patch)
  }

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
      stop_loss_pct: data.stop_loss_pct,
      max_hold_hours: data.max_hold_hours,
      scan_threshold_apr_pct: data.scan_threshold_apr_pct ?? '0',
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
    if (draft.stop_loss_pct !== data.stop_loss_pct) patch.stop_loss_pct = draft.stop_loss_pct
    if (draft.max_hold_hours !== data.max_hold_hours) patch.max_hold_hours = draft.max_hold_hours
    if (draft.scan_threshold_apr_pct !== (data.scan_threshold_apr_pct ?? '0')) patch.scan_threshold_apr_pct = draft.scan_threshold_apr_pct
    if (Object.keys(patch).length === 0) {
      cancelEdit()
      return
    }
    patchMut.mutate(patch)
  }

  const isWidening =
    parseFloat(draft.min_apr_pct || '0') < minApr ||
    parseInt(draft.max_positions || '0', 10) > maxPos ||
    parseFloat(draft.max_total_notional_usd || '0') > maxNot ||
    parseFloat(draft.stop_loss_pct || '0') > parseFloat(data.stop_loss_pct || '0') ||
    parseFloat(draft.max_hold_hours || '0') > parseFloat(data.max_hold_hours || '0')

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

  const stopLoss = parseFloat(data.stop_loss_pct || '0')
  const maxHold = parseFloat(data.max_hold_hours || '0')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 策略风控参数（每策略一卡，2 列网格）*/}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16, alignItems: 'start' }}>
        {/* 卡 1 · 策略 #01 资金费率套利 */}
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title="#01 资金费率套利 · 风控参数"
            subtitle="FUNDING RATE · LIVE TUNABLE"
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
                  <span style={{ color: 'var(--text-secondary)' }} title={t('候选展示门槛 — APR ≥ 该值进 UI 候选表（仅展示，不实盘开仓；0 = 回退用扫描最低 APR）')}>{t('候选展示门槛')}</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {parseFloat(data.scan_threshold_apr_pct ?? '0') > 0
                      ? `${parseFloat(data.scan_threshold_apr_pct ?? '0').toFixed(2)}%`
                      : t('回退')}
                  </span>
                </div>
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
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>止损百分比</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{stopLoss.toFixed(2)}%</span>
                </div>
                <ProgressBar pct={Math.min(100, stopLoss * 10)} tone="success" />
              </div>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>最大持仓时长</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{maxHold.toFixed(0)}h</span>
                </div>
                <ProgressBar pct={Math.min(100, (maxHold / 720) * 100)} tone="success" />
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
                <div style={{ fontSize: 14, marginBottom: 6, color: 'var(--text-secondary)' }} title={t('候选展示门槛 — APR ≥ 该值进 UI 候选表（仅展示，不实盘开仓；0 = 回退用扫描最低 APR）')}>
                  {t('候选展示门槛')} (%)
                </div>
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  value={draft.scan_threshold_apr_pct}
                  onChange={(e) => setDraft((d) => ({ ...d, scan_threshold_apr_pct: e.target.value }))}
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
              <div>
                <div style={{ fontSize: 14, marginBottom: 6, color: 'var(--text-secondary)' }}>止损百分比 (%)</div>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  value={draft.stop_loss_pct}
                  onChange={(e) => setDraft((d) => ({ ...d, stop_loss_pct: e.target.value }))}
                  style={inputStyle}
                />
              </div>
              <div>
                <div style={{ fontSize: 14, marginBottom: 6, color: 'var(--text-secondary)' }}>最大持仓时长 (h)</div>
                <input
                  type="number"
                  step="1"
                  min="1"
                  value={draft.max_hold_hours}
                  onChange={(e) => setDraft((d) => ({ ...d, max_hold_hours: e.target.value }))}
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

        {/* 卡 2 · 策略 #04 期现套利 */}
        {spCfg && (
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title="#04 期现套利 · 风控参数"
            subtitle="SPOT-PERP BASIS · LIVE TUNABLE"
            right={
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Badge tone={spCfg.live_mode ? 'warn' : 'active'}>
                  {spCfg.live_mode ? 'LIVE' : 'PAPER'}
                </Badge>
                {spEditing ? (
                  <button
                    type="button"
                    onClick={spCancelEdit}
                    title="取消"
                    style={{ background: 'transparent', color: 'var(--text-tertiary)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', padding: 4, cursor: 'pointer', display: 'inline-flex' }}
                  >
                    <XIcon size={12} />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={spStartEdit}
                    title="编辑参数"
                    style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-blood)', background: 'transparent', border: '1px solid rgba(227,64,88,0.3)', borderRadius: 'var(--radius-sm)', padding: '4px 8px', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  >
                    <Pencil size={10} />
                    <span>调整</span>
                  </button>
                )}
              </div>
            }
          />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginTop: 12 }}>
            {/* 入场阈值 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>入场基差阈值</span>
                {spEditing ? (
                  <input
                    type="number" step="0.01" min="0"
                    value={spDraft.entry_pct ?? spCfg.entry_pct}
                    onChange={(e) => setSpDraft({ ...spDraft, entry_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(spCfg.entry_pct).toFixed(2)}%</span>
                )}
              </div>
              <ProgressBar pct={Math.min(100, (Number(spCfg.entry_pct) / 1) * 100)} tone="success" />
            </div>

            {/* 收敛平仓 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>收敛平仓阈值</span>
                {spEditing ? (
                  <input
                    type="number" step="0.01" min="0"
                    value={spDraft.exit_pct ?? spCfg.exit_pct}
                    onChange={(e) => setSpDraft({ ...spDraft, exit_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(spCfg.exit_pct).toFixed(2)}%</span>
                )}
              </div>
              <ProgressBar pct={Math.min(100, (Number(spCfg.exit_pct) / 0.5) * 100)} tone="success" />
            </div>

            {/* 最大持仓时长 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>最大持仓 (h)</span>
                {spEditing ? (
                  <input
                    type="number" step="1" min="1"
                    value={spDraft.max_hold_hours ?? spCfg.max_hold_hours}
                    onChange={(e) => setSpDraft({ ...spDraft, max_hold_hours: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(spCfg.max_hold_hours).toFixed(0)}h</span>
                )}
              </div>
            </div>

            {/* 同时持仓数 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>同时持仓上限</span>
                {spEditing ? (
                  <input
                    type="number" step="1" min="1" max="10"
                    value={spDraft.max_concurrent ?? spCfg.max_concurrent}
                    onChange={(e) => setSpDraft({ ...spDraft, max_concurrent: parseInt(e.target.value, 10) })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{spCfg.max_concurrent}</span>
                )}
              </div>
            </div>

            {/* 单笔规模 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>单笔 notional ($)</span>
                {spEditing ? (
                  <input
                    type="number" step="10" min="0"
                    value={spDraft.notional_per_position ?? spCfg.notional_per_position}
                    onChange={(e) => setSpDraft({ ...spDraft, notional_per_position: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>${Number(spCfg.notional_per_position).toFixed(0)}</span>
                )}
              </div>
            </div>

            {/* 方向过滤 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>方向过滤</span>
                {spEditing ? (
                  <select
                    value={spDraft.direction_filter ?? spCfg.direction_filter}
                    onChange={(e) => setSpDraft({ ...spDraft, direction_filter: e.target.value as 'premium' | 'discount' | 'both' })}
                    style={{ ...inputStyle, width: 120, textAlign: 'left' }}
                  >
                    <option value="premium">premium</option>
                    <option value="discount">discount (D.2)</option>
                    <option value="both">both (D.2)</option>
                  </select>
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{spCfg.direction_filter}</span>
                )}
              </div>
            </div>

            {/* a — 基差扩大止损 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="当前基差较入场扩大 ≥ 该值即止损（0=禁用）">
                  基差扩大止损
                </span>
                {spEditing ? (
                  <input
                    type="number" step="0.05" min="0"
                    value={spDraft.stop_basis_widening_pct ?? spCfg.stop_basis_widening_pct}
                    onChange={(e) => setSpDraft({ ...spDraft, stop_basis_widening_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {Number(spCfg.stop_basis_widening_pct) > 0
                      ? `${Number(spCfg.stop_basis_widening_pct).toFixed(2)}%`
                      : '禁用'}
                  </span>
                )}
              </div>
              <ProgressBar pct={Math.min(100, (Number(spCfg.stop_basis_widening_pct) / 1) * 100)} tone="warn" />
            </div>

            {/* c — premium 方向独立阈值 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="premium 方向独立入场阈值（0 回退用入场基差阈值）">
                  premium 阈值
                </span>
                {spEditing ? (
                  <input
                    type="number" step="0.05" min="0"
                    value={spDraft.entry_pct_premium ?? spCfg.entry_pct_premium}
                    onChange={(e) => setSpDraft({ ...spDraft, entry_pct_premium: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {Number(spCfg.entry_pct_premium) > 0
                      ? `${Number(spCfg.entry_pct_premium).toFixed(2)}%`
                      : '回退'}
                  </span>
                )}
              </div>
            </div>

            {/* c — discount 方向独立阈值 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="discount 方向独立入场阈值（含 borrow + funding 双层成本，建议 ≥ premium + 0.20%）">
                  discount 阈值
                </span>
                {spEditing ? (
                  <input
                    type="number" step="0.05" min="0"
                    value={spDraft.entry_pct_discount ?? spCfg.entry_pct_discount}
                    onChange={(e) => setSpDraft({ ...spDraft, entry_pct_discount: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {Number(spCfg.entry_pct_discount) > 0
                      ? `${Number(spCfg.entry_pct_discount).toFixed(2)}%`
                      : '回退'}
                  </span>
                )}
              </div>
            </div>

            {/* 候选展示门槛（scan_threshold_pct）— UI 显示 |basis| ≥ 该值的候选；< entry_pct */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="候选展示门槛 — |基差| ≥ 该值进 UI 候选表（仅展示，不实盘开仓）">
                  候选展示门槛
                </span>
                {spEditing ? (
                  <input
                    type="number" step="0.05" min="0"
                    value={spDraft.scan_threshold_pct ?? spCfg.scan_threshold_pct}
                    onChange={(e) => setSpDraft({ ...spDraft, scan_threshold_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(spCfg.scan_threshold_pct).toFixed(2)}%</span>
                )}
              </div>
            </div>

            {/* b — 入场时机过滤：滑窗时长 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="入场时机过滤 — 追踪近 N 分钟 |基差| 峰值（防接飞刀）">
                  峰值滑窗 (min)
                </span>
                {spEditing ? (
                  <input
                    type="number" step="1" min="0"
                    value={spDraft.peak_window_minutes ?? spCfg.peak_window_minutes}
                    onChange={(e) => setSpDraft({ ...spDraft, peak_window_minutes: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {Number(spCfg.peak_window_minutes) > 0
                      ? `${Number(spCfg.peak_window_minutes).toFixed(0)}min`
                      : '禁用'}
                  </span>
                )}
              </div>
            </div>

            {/* b — 入场时机过滤：最少回落幅度 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="最少回落幅度 — |基差| 必须从峰值回落 ≥ 该值才入场（0 = 禁用）">
                  入场回落要求
                </span>
                {spEditing ? (
                  <input
                    type="number" step="0.01" min="0"
                    value={spDraft.min_peak_dropoff_pct ?? spCfg.min_peak_dropoff_pct}
                    onChange={(e) => setSpDraft({ ...spDraft, min_peak_dropoff_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {Number(spCfg.min_peak_dropoff_pct) > 0
                      ? `${Number(spCfg.min_peak_dropoff_pct).toFixed(2)}%`
                      : '禁用'}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border-subtle)', fontSize: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            <span>
              候选币 {spCfg.candidate_symbols.length} · 交易所 {spCfg.exchanges.join('+')} · 扫描门槛 {Number(spCfg.scan_threshold_pct).toFixed(2)}%
            </span>
            {spEditing && (
              <div style={{ display: 'flex', gap: 8 }}>
                {spPatchMut.isError && (
                  <span style={{ color: 'var(--accent-blood)' }}>
                    {String((spPatchMut.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '保存失败')}
                  </span>
                )}
                <Button onClick={spSaveEdit} disabled={spPatchMut.isPending}>
                  {spPatchMut.isPending ? '保存中…' : '保存'}
                </Button>
              </div>
            )}
            {!spEditing && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--accent-emerald)' }}>
                <CheckCircle2 size={12} /> 修改即时持久化（重启不丢）
              </span>
            )}
          </div>
        </CardElevated>
        )}

        {/* 卡 3 · 策略 #02 跨所 funding 差套利 */}
        {pbCfg && (
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader
            title="#02 跨所 funding 差套利 · 风控参数"
            subtitle="PERP-BASIS ARB · LIVE TUNABLE"
            right={
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Badge tone={pbCfg.paper_running ? 'active' : 'warn'}>
                  {pbCfg.paper_running ? 'PAPER' : 'IDLE'}
                </Badge>
                {pbEditing ? (
                  <button
                    type="button"
                    onClick={pbCancelEdit}
                    title="取消"
                    style={{ background: 'transparent', color: 'var(--text-tertiary)', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', padding: 4, cursor: 'pointer', display: 'inline-flex' }}
                  >
                    <XIcon size={12} />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={pbStartEdit}
                    title="编辑参数"
                    style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-blood)', background: 'transparent', border: '1px solid rgba(227,64,88,0.3)', borderRadius: 'var(--radius-sm)', padding: '4px 8px', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  >
                    <Pencil size={10} />
                    <span>调整</span>
                  </button>
                )}
              </div>
            }
          />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16, marginTop: 12 }}>
            {/* 入场 funding 差 APR 阈值 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="long/short 两端年化 funding rate 差 ≥ 该值才入场">
                  入场 diff APR
                </span>
                {pbEditing ? (
                  <input
                    type="number" step="1" min="0"
                    value={pbDraft.min_diff_apr_pct ?? pbCfg.min_diff_apr_pct}
                    onChange={(e) => setPbDraft({ ...pbDraft, min_diff_apr_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(pbCfg.min_diff_apr_pct).toFixed(0)}%</span>
                )}
              </div>
              <ProgressBar pct={Math.min(100, (Number(pbCfg.min_diff_apr_pct) / 100) * 100)} tone="success" />
            </div>

            {/* 出场 diff 衰减阈值 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="diff APR 衰减到 ≤ 该值即平仓（费差消失）">
                  退出 diff APR
                </span>
                {pbEditing ? (
                  <input
                    type="number" step="0.5" min="0"
                    value={pbDraft.exit_diff_apr_pct ?? pbCfg.exit_diff_apr_pct}
                    onChange={(e) => setPbDraft({ ...pbDraft, exit_diff_apr_pct: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(pbCfg.exit_diff_apr_pct).toFixed(1)}%</span>
                )}
              </div>
              <ProgressBar pct={Math.min(100, (Number(pbCfg.exit_diff_apr_pct) / 10) * 100)} tone="warn" />
            </div>

            {/* 最大持仓时长 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>最大持仓 (h)</span>
                {pbEditing ? (
                  <input
                    type="number" step="1" min="1"
                    value={pbDraft.max_hold_hours ?? pbCfg.max_hold_hours}
                    onChange={(e) => setPbDraft({ ...pbDraft, max_hold_hours: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(pbCfg.max_hold_hours).toFixed(0)}h</span>
                )}
              </div>
            </div>

            {/* 最少持仓时长 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="最少持仓时长，覆盖至少 1 个 funding 结算（防小波动早退）">
                  最少持仓 (h)
                </span>
                {pbEditing ? (
                  <input
                    type="number" step="1" min="0"
                    value={pbDraft.min_hold_hours ?? pbCfg.min_hold_hours}
                    onChange={(e) => setPbDraft({ ...pbDraft, min_hold_hours: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{Number(pbCfg.min_hold_hours).toFixed(0)}h</span>
                )}
              </div>
            </div>

            {/* 同时持仓数 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>同时持仓上限</span>
                {pbEditing ? (
                  <input
                    type="number" step="1" min="1" max="10"
                    value={pbDraft.max_concurrent ?? pbCfg.max_concurrent}
                    onChange={(e) => setPbDraft({ ...pbDraft, max_concurrent: parseInt(e.target.value, 10) })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{pbCfg.max_concurrent}</span>
                )}
              </div>
            </div>

            {/* 单笔规模 */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }} title="每笔仓位双腿 notional（每端各 N USDT，总 2N margin）">
                  单笔 notional ($)
                </span>
                {pbEditing ? (
                  <input
                    type="number" step="10" min="0"
                    value={pbDraft.notional_per_position ?? pbCfg.notional_per_position}
                    onChange={(e) => setPbDraft({ ...pbDraft, notional_per_position: e.target.value })}
                    style={{ ...inputStyle, width: 80 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)' }}>${Number(pbCfg.notional_per_position).toFixed(0)}</span>
                )}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border-subtle)', fontSize: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            <span>
              候选币 {pbCfg.candidate_symbols.length} · 扫描间隔 {pbCfg.scan_interval_seconds}s · enabled={String(pbCfg.enabled)}
            </span>
            {pbEditing && (
              <div style={{ display: 'flex', gap: 8 }}>
                {pbPatchMut.isError && (
                  <span style={{ color: 'var(--accent-blood)' }}>
                    {String((pbPatchMut.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '保存失败')}
                  </span>
                )}
                <Button onClick={pbSaveEdit} disabled={pbPatchMut.isPending}>
                  {pbPatchMut.isPending ? '保存中…' : '保存'}
                </Button>
              </div>
            )}
            {!pbEditing && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--accent-emerald)' }}>
                <CheckCircle2 size={12} /> 修改即时持久化（重启不丢）
              </span>
            )}
          </div>
        </CardElevated>
        )}
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
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>50%</span>
                  </div>
                  <ProgressBar pct={Math.min(100, marginPct)} tone="success" />
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 {fmtPct(summary?.margin_usage_pct)}</div>
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>单交易所占比</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>50%</span>
                  </div>
                  {(() => {
                    const exConc = parseFloat(summary?.max_exchange_concentration_pct ?? '0')
                    return <>
                      <ProgressBar pct={Math.min(100, exConc / 50 * 100)} tone={exConc > 50 ? 'blood' : 'success'} />
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginTop: 4, color: 'var(--text-tertiary)' }}>
                        当前 {summary === undefined ? '—' : `${exConc.toFixed(1)}%`}
                      </div>
                    </>
                  })()}
                </div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>单币种占比</span>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>20%</span>
                  </div>
                  {(() => {
                    const symConc = parseFloat(summary?.max_symbol_concentration_pct ?? '0')
                    return <>
                      <ProgressBar pct={Math.min(100, symConc / 20 * 100)} tone={symConc > 20 ? 'blood' : 'success'} />
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
            账户级硬性熔断,触及任意一条立即停所有策略;需修改 config.yaml 重启系统才能调整
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
