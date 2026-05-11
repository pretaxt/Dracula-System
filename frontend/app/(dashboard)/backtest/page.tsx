'use client'
import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { useT } from '@/components/i18n/I18nProvider'
import {
  runBacktest,
  runSpotPerpBacktest,
  runPerpBasisBacktest,
  runPerpBasisSweep,
  type BacktestResult,
  type EquityPoint,
  type SpotPerpBacktestResult,
  type PerpBasisBacktestResult,
  type PerpBasisSweepResponse,
} from '@/lib/api/backtest'
import { getSymbols } from '@/lib/api/system'

const FALLBACK_SYMBOLS = ['HIGH', 'CHIP', 'API3', 'ENSO', 'COMP']
const EXCHANGES = ['binance', 'okx']

function SymbolPicker({ value, onChange, options }: {
  value: string
  onChange: (s: string) => void
  options: string[]  // ["BTC", "ETH", ...]
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const filtered = useMemo(() => {
    if (!query.trim()) return options
    const q = query.trim().toUpperCase()
    return options.filter(s => s.toUpperCase().includes(q))
  }, [query, options])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          color: 'var(--text-primary)',
          padding: '6px 28px 6px 10px',
          fontSize: 15,
          cursor: 'pointer',
          position: 'relative',
          fontFamily: 'var(--font-mono)',
        }}
      >
        {value}/USDT
        <span style={{
          position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
          fontSize: 12, color: 'var(--text-muted)',
        }}>▼</span>
      </div>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, zIndex: 1000,
          background: '#0a0d12',
          backgroundImage: 'linear-gradient(180deg, #12161e 0%, #0a0d12 100%)',
          border: '1px solid var(--accent-blood)',
          borderRadius: 6,
          boxShadow: '0 12px 40px rgba(0,0,0,0.85), 0 0 0 1px rgba(0,0,0,0.5), 0 0 24px rgba(227,64,88,0.15)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          maxHeight: 320, overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
        }}>
          <input
            type="text"
            placeholder="搜索 (e.g. BTC, SOL)"
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoFocus
            style={{
              background: '#000',
              border: 'none',
              borderBottom: '1px solid var(--border)',
              color: 'var(--text-primary)',
              padding: '10px 12px',
              fontSize: 14,
              fontFamily: 'var(--font-mono)',
              outline: 'none',
            }}
          />
          <div style={{ overflow: 'auto', flex: 1 }}>
            {filtered.length === 0 ? (
              <div style={{ padding: 12, color: 'var(--text-muted)', fontSize: 14, textAlign: 'center' }}>
                无匹配
              </div>
            ) : (
              filtered.map(s => (
                <div
                  key={s}
                  onClick={() => { onChange(s); setOpen(false); setQuery('') }}
                  style={{
                    padding: '6px 10px',
                    fontSize: 14,
                    fontFamily: 'var(--font-mono)',
                    cursor: 'pointer',
                    color: s === value ? 'var(--accent-blood)' : 'var(--text-secondary)',
                    background: s === value ? 'rgba(227,64,88,0.08)' : 'transparent',
                    borderLeft: s === value ? '2px solid var(--accent-blood)' : '2px solid transparent',
                  }}
                  onMouseEnter={e => { if (s !== value) e.currentTarget.style.background = 'rgba(255,255,255,0.04)' }}
                  onMouseLeave={e => { if (s !== value) e.currentTarget.style.background = 'transparent' }}
                >
                  {s}/USDT
                </div>
              ))
            )}
          </div>
          <div style={{
            borderTop: '1px solid var(--border)',
            padding: '6px 12px',
            fontSize: 12,
            color: 'var(--text-muted)',
            fontFamily: 'var(--font-mono)',
            background: '#000',
            display: 'flex', justifyContent: 'space-between',
          }}>
            <span>{filtered.length} / {options.length}</span>
            <span>动态扫描宇宙</span>
          </div>
        </div>
      )}
    </div>
  )
}

function fmt2(n: number) { return n.toFixed(2) }
function fmtUsd(n: number) {
  return n >= 1000 ? `$${(n / 1000).toFixed(2)}K` : `$${n.toFixed(2)}`
}

function EquityChart({ curve, initialCapital }: { curve: EquityPoint[]; initialCapital: number }) {
  if (curve.length < 2) return null
  const W = 600, H = 160, PAD = { t: 12, r: 12, b: 28, l: 56 }
  const xs = curve.map(p => p.ts)
  const ys = curve.map(p => p.equity)
  const xMin = xs[0], xMax = xs[xs.length - 1]
  const yMin = Math.min(...ys) * 0.999
  const yMax = Math.max(...ys) * 1.001
  const cx = (t: number) => PAD.l + ((t - xMin) / (xMax - xMin)) * (W - PAD.l - PAD.r)
  const cy = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.t - PAD.b)
  const pts = curve.map(p => `${cx(p.ts)},${cy(p.equity)}`).join(' ')
  const area = `M${cx(xs[0])},${cy(yMin)} ` +
    curve.map(p => `L${cx(p.ts)},${cy(p.equity)}`).join(' ') +
    ` L${cx(xs[xs.length - 1])},${cy(yMin)} Z`
  const baseY = cy(initialCapital)
  const color = ys[ys.length - 1] >= initialCapital ? '#10b981' : '#ef4444'
  const yTicks = [yMin, (yMin + yMax) / 2, yMax]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H, display: 'block' }}>
      <line x1={PAD.l} y1={baseY} x2={W - PAD.r} y2={baseY}
        stroke="rgba(255,255,255,0.1)" strokeDasharray="4 3" />
      <path d={area} fill={color} fillOpacity={0.08} />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
      {yTicks.map((v, i) => (
        <text key={i} x={PAD.l - 6} y={cy(v) + 4} textAnchor="end"
          style={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          {fmtUsd(v)}
        </text>
      ))}
      {[0, Math.floor(curve.length / 2), curve.length - 1].map(i => (
        <text key={i} x={cx(curve[i].ts)} y={H - 4} textAnchor="middle"
          style={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          {new Date(curve[i].ts).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
        </text>
      ))}
    </svg>
  )
}

function Metric({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 13, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}
      </span>
      <span style={{ fontSize: 24, fontWeight: 700, color: color ?? 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
        {value}
      </span>
      {sub && <span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>{sub}</span>}
    </div>
  )
}

export default function BacktestPage() {
  const { t } = useT()
  const [symbol, setSymbol] = useState('BTC')
  const [exchange, setExchange] = useState('binance')
  const [days, setDays] = useState(30)
  const [capital, setCapital] = useState(10000)
  const [size, setSize] = useState(500)
  const [minApr, setMinApr] = useState(10)
  const [maxPos, setMaxPos] = useState(5)
  const [stopLoss, setStopLoss] = useState(2)
  const [maxHold, setMaxHold] = useState(168)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 同步策略扫描宇宙：把 BTC/USDT 等 597 个动态发现的标的注入回测候选
  const { data: symbolsData } = useQuery({
    queryKey: ['system-symbols'],
    queryFn: getSymbols,
    staleTime: 5 * 60 * 1000,  // 5min cache，符号变化很慢
  })
  const symbolBases = useMemo(() => {
    if (!symbolsData?.symbols?.length) return FALLBACK_SYMBOLS
    return symbolsData.symbols
      .filter(s => s.endsWith('/USDT'))
      .map(s => s.split('/')[0])
  }, [symbolsData])

  const [activeTab, setActiveTab] = useState<string>('funding-rate')

  async function handleRun() {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await runBacktest({
        symbol, exchange, days,
        initial_capital_usd: capital,
        size_per_trade_usd: size,
        min_apr_pct: minApr,
        max_positions: maxPos,
        stop_loss_pct: stopLoss,
        max_hold_hours: maxHold,
      })
      setResult(res)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '回测请求失败')
    } finally {
      setLoading(false)
    }
  }

  const returnColor = result
    ? result.total_return_pct >= 0 ? '#10b981' : '#ef4444'
    : 'var(--text-primary)'

  const inputStyle: React.CSSProperties = {
    background: 'var(--surface-2)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    color: 'var(--text-primary)',
    padding: '6px 10px',
    fontSize: 15,
    width: '100%',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 13,
    color: 'var(--text-muted)',
    marginBottom: 4,
    display: 'block',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  }

  const TABS = [
    { id: 'funding-rate', label: '#01 资金费率套利' },
    { id: 'perp-basis',   label: '#02 跨所基差套利' },
    { id: 'spot-perp',    label: '#04 期现套利' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Tab 导航 */}
      <div style={{
        display: 'flex', gap: 4,
        background: 'var(--surface-2)',
        border: '1px solid var(--border)',
        borderRadius: 8, padding: 4,
      }}>
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              flex: 1, padding: '8px 16px',
              background: activeTab === tab.id
                ? 'linear-gradient(135deg, var(--accent-blood) 0%, #c12944 100%)'
                : 'transparent',
              color: activeTab === tab.id ? '#fff' : 'var(--text-secondary)',
              border: 'none', borderRadius: 6,
              cursor: 'pointer', fontFamily: 'var(--font-mono)',
              fontSize: 13, fontWeight: activeTab === tab.id ? 700 : 400,
              letterSpacing: '0.04em',
              boxShadow: activeTab === tab.id ? '0 2px 8px rgba(227,64,88,0.35)' : 'none',
              transition: 'all 0.15s ease',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'funding-rate' && (
      <>
      <CardElevated style={{ padding: 20 }}>
        <SectionHeader title={t('历史回测')} subtitle={t('资金费率套利策略历史绩效模拟')} />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, marginTop: 16 }}>
          <div>
            <label style={labelStyle}>{t('标的')}</label>
            <SymbolPicker value={symbol} onChange={setSymbol} options={symbolBases} />
          </div>
          <div>
            <label style={labelStyle}>{t('交易所')}</label>
            <select value={exchange} onChange={e => setExchange(e.target.value)} style={inputStyle}>
              {EXCHANGES.map(x => <option key={x} value={x}>{x}</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>{t('回测天数')}</label>
            <select value={days} onChange={e => setDays(Number(e.target.value))} style={inputStyle}>
              {[7, 14, 30, 60, 90].map(d => <option key={d} value={d}>{d}天</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>{t('起始资金 $')}</label>
            <input type="number" value={capital} onChange={e => setCapital(Number(e.target.value))}
              min={1000} step={1000} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>{t('每笔仓位 $')}</label>
            <input type="number" value={size} onChange={e => setSize(Number(e.target.value))}
              min={100} step={100} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>{t('最低 APR %')}</label>
            <input type="number" value={minApr} onChange={e => setMinApr(Number(e.target.value))}
              min={0.1} step={0.5} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>{t('最大持仓数')}</label>
            <input type="number" value={maxPos} onChange={e => setMaxPos(Number(e.target.value))}
              min={1} max={20} step={1} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>{t('止损 %')}</label>
            <input type="number" value={stopLoss} onChange={e => setStopLoss(Number(e.target.value))}
              min={0.5} step={0.5} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>{t('最长持仓 h')}</label>
            <input type="number" value={maxHold} onChange={e => setMaxHold(Number(e.target.value))}
              min={8} step={8} style={inputStyle} />
          </div>
        </div>
        <div style={{
          marginTop: 20,
          padding: '16px 20px',
          background: 'linear-gradient(135deg, rgba(227,64,88,0.06) 0%, rgba(227,64,88,0.02) 100%)',
          border: '1px solid rgba(227,64,88,0.2)',
          borderRadius: 8,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', flex: 1, minWidth: 0 }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.12em',
              textTransform: 'uppercase', color: 'var(--accent-blood)',
              padding: '3px 8px', border: '1px solid rgba(227,64,88,0.3)',
              borderRadius: 3, background: 'rgba(227,64,88,0.05)',
            }}>
              {t('READY')}
            </span>
            <div style={{ display: 'flex', gap: 18, fontSize: 14, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
              <span><span style={{ color: 'var(--text-muted)' }}>{t('标的')}:</span> <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 600 }}>{symbol}/USDT</span></span>
              <span><span style={{ color: 'var(--text-muted)' }}>{t('周期')}:</span> <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 600 }}>{days}d</span></span>
              <span><span style={{ color: 'var(--text-muted)' }}>{t('资金')}:</span> <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', fontWeight: 600 }}>{fmtUsd(capital)}</span></span>
              <span><span style={{ color: 'var(--text-muted)' }}>APR≥:</span> <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-gold)', fontWeight: 600 }}>{minApr}%</span></span>
            </div>
          </div>
          <button onClick={handleRun} disabled={loading} style={{
            padding: '12px 32px',
            background: loading
              ? 'var(--surface-3)'
              : 'linear-gradient(135deg, var(--accent-blood) 0%, #c12944 100%)',
            color: '#fff', border: 'none', borderRadius: 6,
            cursor: loading ? 'not-allowed' : 'pointer',
            fontWeight: 700, fontSize: 15, letterSpacing: '0.08em',
            textTransform: 'uppercase',
            boxShadow: loading ? 'none' : '0 4px 14px rgba(227,64,88,0.35), 0 0 0 1px rgba(227,64,88,0.2) inset',
            transition: 'all var(--duration-fast) var(--ease-in-out)',
            display: 'inline-flex', alignItems: 'center', gap: 8,
            minWidth: 160, justifyContent: 'center',
          }}
          onMouseEnter={(e) => {
            if (!loading) {
              e.currentTarget.style.transform = 'translateY(-1px)'
              e.currentTarget.style.boxShadow = '0 6px 20px rgba(227,64,88,0.5), 0 0 0 1px rgba(227,64,88,0.3) inset'
            }
          }}
          onMouseLeave={(e) => {
            if (!loading) {
              e.currentTarget.style.transform = 'translateY(0)'
              e.currentTarget.style.boxShadow = '0 4px 14px rgba(227,64,88,0.35), 0 0 0 1px rgba(227,64,88,0.2) inset'
            }
          }}>
            {loading ? (
              <>
                <span style={{
                  width: 12, height: 12, border: '2px solid rgba(255,255,255,0.3)',
                  borderTopColor: '#fff', borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite', display: 'inline-block',
                }} />
                {t('运行中')}
              </>
            ) : (
              <>▶ {t('运行回测')}</>
            )}
          </button>
        </div>
        {error && (
          <div style={{
            marginTop: 12, padding: '10px 14px',
            background: 'rgba(239,68,68,0.08)',
            border: '1px solid rgba(239,68,68,0.3)',
            borderRadius: 6, color: '#ef4444', fontSize: 15,
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontWeight: 700 }}>!</span> {error}
          </div>
        )}
        <style jsx>{`
          @keyframes spin { to { transform: rotate(360deg); } }
        `}</style>
      </CardElevated>

      {result && (
        <>
          {result.total_trades === 0 && (
            <div style={{ padding: '12px 16px', background: 'rgba(234,179,8,0.08)', border: '1px solid rgba(234,179,8,0.25)', borderRadius: 6, fontSize: 15, color: 'var(--accent-gold)' }}>
              {t('当前参数下无满足条件的套利机会，尝试降低最低 APR 或更换标的')}
            </div>
          )}
          <CardElevated style={{ padding: 20 }}>
            <SectionHeader title={t('绩效指标')}
              subtitle={`${result.total_trades} 笔 · ${fmt2(result.periods_days)} 天`} />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 20, marginTop: 16 }}>
              <Metric label={t('总收益率')} value={`${fmt2(result.total_return_pct)}%`} color={returnColor} />
              <Metric label={t('年化收益率')} value={`${fmt2(result.annualized_return_pct)}%`} color={returnColor} />
              <Metric label={t('夏普比率')} value={fmt2(result.sharpe_ratio)}
                sub={result.sharpe_ratio >= 1 ? '优秀' : result.sharpe_ratio >= 0.5 ? '良好' : '一般'} />
              <Metric label={t('最大回撤')} value={`${fmt2(result.max_drawdown_pct)}%`}
                color={result.max_drawdown_pct > 5 ? '#ef4444' : '#10b981'} />
              <Metric label={t('胜率')} value={`${fmt2(result.win_rate_pct)}%`} />
              <Metric label={t('资金费收入')} value={fmtUsd(result.total_funding_usd)} color="#10b981" />
              <Metric label={t('手续费支出')} value={fmtUsd(result.total_fees_usd)} color="#ef4444" />
              <Metric label={t('最终权益')} value={fmtUsd(result.final_equity_usd)} />
            </div>
          </CardElevated>

          <CardElevated style={{ padding: 20 }}>
            <SectionHeader title={t('资金曲线')} />
            <div style={{ marginTop: 12 }}>
              <EquityChart curve={result.equity_curve} initialCapital={capital} />
            </div>
          </CardElevated>

          {result.trades.length > 0 && (
            <CardElevated style={{ padding: 20 }}>
              <SectionHeader title={t('交易明细')} subtitle={`共 ${result.trades.length} 笔`} />
              <div style={{ overflowX: 'auto', marginTop: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['标的', '开仓时间', '平仓时间', '资金费', '手续费', '净盈亏'].map(h => (
                        <th key={h} style={{ padding: '6px 8px', textAlign: 'left',
                          color: 'var(--text-muted)', fontWeight: 500, whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((tr, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{tr.symbol}</td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                          {new Date(tr.opened_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                          {tr.closed_at
                            ? new Date(tr.closed_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                            : <span style={{ color: 'var(--accent-gold)' }}>持仓中</span>}
                        </td>
                        <td style={{ padding: '6px 8px', color: '#10b981', fontFamily: 'var(--font-mono)' }}>+{tr.funding.toFixed(4)}</td>
                        <td style={{ padding: '6px 8px', color: '#ef4444', fontFamily: 'var(--font-mono)' }}>-{tr.fees.toFixed(4)}</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)',
                          color: tr.pnl >= 0 ? '#10b981' : '#ef4444', fontWeight: 600 }}>
                          {tr.pnl >= 0 ? '+' : ''}{tr.pnl.toFixed(4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardElevated>
          )}
        </>
      )}
      </>
      )}

      {activeTab === 'spot-perp' && (
        <SpotPerpBacktestSection />
      )}

      {activeTab === 'perp-basis' && (
        <PerpBasisBacktestSection />
      )}
    </div>
  )
}


// ---------------------------------------------------------------------------
// #04 spot-perp 回测区块
// ---------------------------------------------------------------------------


function SpotPerpBacktestSection() {
  const { t } = useT()
  const [symbols, setSymbols] = useState<string[]>(['SUI', 'ZEC', 'TON', 'LAYER', 'TAO', 'ONDO', 'FIL', 'UNI', 'AAVE', 'WLD', 'ASTER', 'APT', 'ENA', 'NEAR', 'SAHARA', 'SEI', 'CHIP', 'BCH', 'DOT', 'XLM', 'PUMP', 'PENGU', 'TRUMP', 'LDO', 'HBAR', 'SAGA', 'ICP', 'QTUM', 'DASH', 'TIA'])
  const [exchange, setExchange] = useState('binance')
  const [days, setDays] = useState(7)
  const [timeframe, setTimeframe] = useState('1m')
  const [capital, setCapital] = useState(1000)
  const [notional, setNotional] = useState(50)
  const [maxConc, setMaxConc] = useState(2)
  const [entryPct, setEntryPct] = useState(0.30)
  const [entryPrem, setEntryPrem] = useState(0)
  const [entryDisc, setEntryDisc] = useState(0.50)
  const [exitPct, setExitPct] = useState(0.10)
  const [maxHold, setMaxHold] = useState(12)
  const [minHoldMin, setMinHoldMin] = useState(5)
  const [stopWiden, setStopWiden] = useState(0.50)
  const [peakWindow, setPeakWindow] = useState(10)
  const [peakDropoff, setPeakDropoff] = useState(0.05)
  const [direction, setDirection] = useState<'premium' | 'discount' | 'both'>('both')
  const [slippage, setSlippage] = useState(0.10)
  const [feeRate, setFeeRate] = useState(0.0004)
  const [result, setResult] = useState<SpotPerpBacktestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const inputStyle: React.CSSProperties = {
    background: 'var(--surface-2)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    color: 'var(--text-primary)',
    padding: '6px 10px',
    fontSize: 14,
    width: '100%',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 12, color: 'var(--text-muted)', marginBottom: 4,
    display: 'block', textTransform: 'uppercase', letterSpacing: '0.05em',
  }

  async function handleRun() {
    setLoading(true); setError(null); setResult(null)
    try {
      const r = await runSpotPerpBacktest({
        symbols, exchange, days, timeframe,
        initial_capital_usd: capital,
        notional_per_position: notional,
        max_concurrent: maxConc,
        entry_pct: entryPct,
        entry_pct_premium: entryPrem,
        entry_pct_discount: entryDisc,
        exit_pct: exitPct,
        max_hold_hours: maxHold,
        min_hold_minutes: minHoldMin,
        stop_basis_widening_pct: stopWiden,
        peak_window_minutes: peakWindow,
        min_peak_dropoff_pct: peakDropoff,
        direction_filter: direction,
        slippage_pct: slippage,
        fee_rate: feeRate,
      })
      setResult(r)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '回测失败')
    } finally {
      setLoading(false)
    }
  }

  function toggleSymbol(s: string) {
    setSymbols((curr) => curr.includes(s) ? curr.filter((x) => x !== s) : [...curr, s])
  }

  const summary = (result?.summary ?? {}) as Record<string, string | number | object>
  const totalPnl = parseFloat(String(summary.total_pnl_usd ?? '0'))
  const pnlColor = totalPnl >= 0 ? '#10b981' : '#ef4444'

  return (
    <CardElevated style={{ padding: 20 }}>
      <SectionHeader title={t('#04 期现套利回测')} subtitle="SPOT-PERP BASIS · CCXT 1m kline → engine → metrics" />

      {/* 标的选择（多选） */}
      <div style={{ marginTop: 16 }}>
        <label style={labelStyle}>{t('标的（多选）')}</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {['SUI', 'ZEC', 'TON', 'LAYER', 'TAO', 'ONDO', 'FIL', 'UNI', 'AAVE', 'WLD', 'ASTER', 'APT', 'ENA', 'NEAR', 'SAHARA', 'SEI', 'CHIP', 'BCH', 'DOT', 'XLM', 'PUMP', 'PENGU', 'TRUMP', 'LDO', 'HBAR', 'SAGA', 'ICP', 'QTUM', 'DASH', 'TIA', 'WIF', 'BIO', 'FET', 'DOGS', 'GIGGLE', 'ARB', 'ATOM', 'MOVE', 'JUP', 'ENS', 'VIRTUAL', 'WAL', 'ORDI', 'WLFI', 'ETC', 'KITE', 'XPL', 'OP', 'CRV', 'STRK', 'RENDER', 'JTO', 'ORCA', 'ALGO', 'PENDLE', 'INJ', 'XMR', 'ZRO', 'CFG', 'ZBT', 'NIL', 'MEGA', 'GALA', 'ZEN', 'CHZ', 'DYM', 'SAND', 'JASMY', 'ETHFI', 'KAT', 'SPK', 'NOT', 'APE', 'EIGEN', 'POL', 'LIT', 'HAEDAL', 'CETUS', 'CAKE', 'BERA', 'BOME', 'CVC', 'REZ', 'BABY', 'AR', 'NEO', 'GTC', 'NEIRO', 'BANANA', 'AXS', 'PLUME', 'PARTI', 'FF', 'TST', 'EGLD', 'PNUT', 'GRT', 'TURBO', 'HUMA', 'MANA', 'ENJ', 'DEXE', 'ZK', 'BLUR', 'ME', 'IO', 'VANA', 'DYDX', 'VET', 'MINA', 'OPEN', 'RUNE', 'MOVR', 'MITO', 'SOPH', 'PYTH', 'SUSHI', 'HIVE', 'TRB', 'MMT', 'STX', 'MORPHO', 'LPT', 'BSV', 'ROBO', 'IOTA', 'HEMI', 'NIGHT', 'OPN', 'CFX', 'LINEA', 'ROSE', 'STG', 'COMP', 'ENSO', 'THETA', 'ALLO', 'AIXBT', 'HOLO', 'EDU', 'DUSK', 'STORJ', 'TNSR', 'GMT', 'ARKM', 'MEME', 'SKY', 'AVNT', 'IMX', 'KAITO'].map(s => {
            const active = symbols.includes(s)
            return (
              <button
                key={s} onClick={() => toggleSymbol(s)}
                style={{
                  padding: '4px 10px', fontSize: 13, fontFamily: 'var(--font-mono)',
                  borderRadius: 4,
                  background: active ? 'var(--accent-blood)' : 'var(--surface-2)',
                  color: active ? '#fff' : 'var(--text-secondary)',
                  border: '1px solid var(--border)', cursor: 'pointer',
                }}
              >{s}</button>
            )
          })}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, marginTop: 16 }}>
        <div><label style={labelStyle}>{t('交易所')}</label>
          <select value={exchange} onChange={e => setExchange(e.target.value)} style={inputStyle}>
            {EXCHANGES.map(x => <option key={x} value={x}>{x}</option>)}
          </select>
        </div>
        <div><label style={labelStyle}>{t('回测天数')}</label>
          <select value={days} onChange={e => setDays(Number(e.target.value))} style={inputStyle}>
            {[1, 3, 7, 14, 30].map(d => <option key={d} value={d}>{d}天</option>)}
          </select>
        </div>
        <div><label style={labelStyle}>K 线周期</label>
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)} style={inputStyle}>
            {['1m', '5m', '15m'].map(x => <option key={x} value={x}>{x}</option>)}
          </select>
        </div>
        <div><label style={labelStyle}>{t('起始资金 $')}</label>
          <input type="number" value={capital} onChange={e => setCapital(Number(e.target.value))} step={100} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>{t('单笔 notional $')}</label>
          <input type="number" value={notional} onChange={e => setNotional(Number(e.target.value))} step={10} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>{t('同时持仓上限')}</label>
          <input type="number" value={maxConc} onChange={e => setMaxConc(Number(e.target.value))} min={1} step={1} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>{t('入场基差 %')}</label>
          <input type="number" value={entryPct} onChange={e => setEntryPct(Number(e.target.value))} step={0.05} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>premium 阈值 %</label>
          <input type="number" value={entryPrem} onChange={e => setEntryPrem(Number(e.target.value))} step={0.05} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>discount 阈值 %</label>
          <input type="number" value={entryDisc} onChange={e => setEntryDisc(Number(e.target.value))} step={0.05} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>{t('收敛平仓 %')}</label>
          <input type="number" value={exitPct} onChange={e => setExitPct(Number(e.target.value))} step={0.05} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>最大持仓 h</label>
          <input type="number" value={maxHold} onChange={e => setMaxHold(Number(e.target.value))} step={1} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>min hold min</label>
          <input type="number" value={minHoldMin} onChange={e => setMinHoldMin(Number(e.target.value))} step={1} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>基差扩大止损 %</label>
          <input type="number" value={stopWiden} onChange={e => setStopWiden(Number(e.target.value))} step={0.05} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>峰值滑窗 min</label>
          <input type="number" value={peakWindow} onChange={e => setPeakWindow(Number(e.target.value))} step={1} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>入场回落 %</label>
          <input type="number" value={peakDropoff} onChange={e => setPeakDropoff(Number(e.target.value))} step={0.01} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>方向</label>
          <select value={direction} onChange={e => setDirection(e.target.value as 'premium' | 'discount' | 'both')} style={inputStyle}>
            <option value="both">both</option>
            <option value="premium">premium</option>
            <option value="discount">discount</option>
          </select>
        </div>
        <div><label style={labelStyle}>滑点 %</label>
          <input type="number" value={slippage} onChange={e => setSlippage(Number(e.target.value))} step={0.01} style={inputStyle} />
        </div>
        <div><label style={labelStyle}>费率</label>
          <input type="number" value={feeRate} onChange={e => setFeeRate(Number(e.target.value))} step={0.0001} style={inputStyle} />
        </div>
      </div>

      <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <button onClick={handleRun} disabled={loading || symbols.length === 0} style={{
          padding: '8px 18px', background: 'var(--accent-blood)', color: '#fff',
          border: 'none', borderRadius: 6, fontWeight: 600, cursor: loading ? 'wait' : 'pointer',
          opacity: (loading || symbols.length === 0) ? 0.5 : 1,
        }}>
          {loading ? t('运行中…') : t('运行 #04 回测')}
        </button>
        {error && <span style={{ color: '#ef4444', fontSize: 13 }}>{error}</span>}
      </div>

      {result && (
        <div style={{ marginTop: 20 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 16 }}>
            <Metric label="总笔数" value={String(summary.total_trades ?? 0)} />
            <Metric label="胜率" value={`${summary.win_rate_pct ?? 0}%`} />
            <Metric label="净 PnL" value={`$${summary.total_pnl_usd ?? 0}`} color={pnlColor} />
            <Metric label="总手续费" value={`$${summary.total_fees_usd ?? 0}`} />
            <Metric label="平均持仓" value={`${summary.avg_held_hours ?? 0}h`} />
            <Metric label="最大回撤" value={`${summary.max_drawdown_pct ?? 0}%`} />
            <Metric label="最终资金" value={`$${summary.final_equity_usd ?? 0}`} />
            <Metric label="总回报" value={`${summary.total_return_pct ?? 0}%`} color={pnlColor} />
          </div>
          <div style={{ marginTop: 12, fontSize: 13, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            被 dropoff 拒: {String(summary.rejected_count ?? 0)} · APR 跳过: {String(summary.skipped_count ?? 0)}
            <span style={{ marginLeft: 12 }}>退出原因: {Object.entries(((summary.by_exit_reason ?? {}) as Record<string, number>)).map(([k, v]) => `${k}:${v}`).join(' / ') || '—'}</span>
          </div>

          {/* 交易明细 */}
          {result.trades.length > 0 && (
            <div style={{ marginTop: 20, maxHeight: 360, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead style={{ position: 'sticky', top: 0, background: 'var(--surface-1)' }}>
                  <tr style={{ textAlign: 'left' }}>
                    {['标的', '方向', '入场基差', '出场基差', '持仓 h', '退出原因', 'PnL'].map(h => (
                      <th key={h} style={{ padding: '8px', color: 'var(--text-tertiary)', fontWeight: 500, fontSize: 12 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((tr, i) => {
                    const pnl = parseFloat(tr.realized_pnl)
                    return (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>{tr.symbol}</td>
                        <td style={{ padding: '6px 8px', color: tr.direction === 'premium' ? '#10b981' : '#ef4444' }}>{tr.direction}</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>{tr.entry_basis_pct}%</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>{tr.exit_basis_pct}%</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>{tr.held_hours}</td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-tertiary)' }}>{tr.exit_reason}</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', fontWeight: 600,
                          color: pnl >= 0 ? '#10b981' : '#ef4444' }}>
                          {pnl >= 0 ? '+' : ''}{tr.realized_pnl}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </CardElevated>
  )
}


// ---------------------------------------------------------------------------
// #02 perp-basis 跨所 funding 差套利回测区块
// ---------------------------------------------------------------------------


function PerpBasisBacktestSection() {
  const { t } = useT()
  const [symbolsCsv, setSymbolsCsv] = useState('BTC/USDT,ETH/USDT,FIL/USDT,SOL/USDT,TIA/USDT')
  const [days, setDays] = useState(14)
  const [minDiff, setMinDiff] = useState(50)
  const [notional, setNotional] = useState(50)
  const [maxConcurrent, setMaxConcurrent] = useState(3)
  const [maxHold, setMaxHold] = useState(48)
  const [minHold, setMinHold] = useState(4)
  const [exitDiff, setExitDiff] = useState(5)

  const [result, setResult] = useState<PerpBasisBacktestResult | null>(null)
  const [sweep, setSweep] = useState<PerpBasisSweepResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [sweepLoading, setSweepLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const inputStyle: React.CSSProperties = {
    background: 'var(--surface-2)', border: '1px solid var(--border)',
    borderRadius: 6, color: 'var(--text-primary)', padding: '6px 10px',
    fontSize: 14, width: '100%',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 12, color: 'var(--text-muted)', marginBottom: 4,
    display: 'block', textTransform: 'uppercase', letterSpacing: '0.05em',
  }

  const symbols = useMemo(
    () => symbolsCsv.split(',').map(s => s.trim()).filter(Boolean),
    [symbolsCsv],
  )

  async function handleRun() {
    setLoading(true); setError(null); setResult(null)
    try {
      const res = await runPerpBasisBacktest({
        symbols, days,
        min_diff_apr_pct: minDiff,
        notional_per_position: notional,
        max_concurrent: maxConcurrent,
        max_hold_hours: maxHold,
        min_hold_hours: minHold,
        exit_diff_apr_pct: exitDiff,
      })
      setResult(res)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '回测失败')
    } finally {
      setLoading(false)
    }
  }

  async function handleSweep() {
    setSweepLoading(true); setError(null); setSweep(null)
    try {
      const res = await runPerpBasisSweep({
        symbols, days,
        min_diff_apr_pct_list: [15, 30, 50, 75, 100, 150],
        notional_per_position: notional,
        min_hold_hours: minHold,
      })
      setSweep(res)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'sweep 失败')
    } finally {
      setSweepLoading(false)
    }
  }

  return (
    <CardElevated style={{ padding: 20 }}>
      <SectionHeader
        title={t('#02 跨所基差套利回测')}
        subtitle="PERP-BASIS · CCXT funding history → engine + multi-threshold sweep"
      />

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
        gap: 12, marginTop: 16,
      }}>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>{t('交易对（逗号分隔）')}</label>
          <input
            value={symbolsCsv} onChange={e => setSymbolsCsv(e.target.value)}
            style={{ ...inputStyle, fontFamily: 'var(--font-mono)' }}
          />
        </div>
        <div>
          <label style={labelStyle}>{t('回测天数')}</label>
          <select value={days} onChange={e => setDays(Number(e.target.value))} style={inputStyle}>
            {[3, 7, 14, 30, 60].map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
        <div>
          <label style={labelStyle}>min_diff_apr (%)</label>
          <input type="number" value={minDiff} onChange={e => setMinDiff(Number(e.target.value))}
                 style={inputStyle} step="5" />
        </div>
        <div>
          <label style={labelStyle}>{t('单笔名义')}</label>
          <input type="number" value={notional} onChange={e => setNotional(Number(e.target.value))}
                 style={inputStyle} step="10" />
        </div>
        <div>
          <label style={labelStyle}>max_concurrent</label>
          <input type="number" value={maxConcurrent} onChange={e => setMaxConcurrent(Number(e.target.value))}
                 style={inputStyle} step="1" />
        </div>
        <div>
          <label style={labelStyle}>max_hold (h)</label>
          <input type="number" value={maxHold} onChange={e => setMaxHold(Number(e.target.value))}
                 style={inputStyle} step="1" />
        </div>
        <div>
          <label style={labelStyle}>min_hold (h)</label>
          <input type="number" value={minHold} onChange={e => setMinHold(Number(e.target.value))}
                 style={inputStyle} step="1" />
        </div>
        <div>
          <label style={labelStyle}>exit_diff_apr (%)</label>
          <input type="number" value={exitDiff} onChange={e => setExitDiff(Number(e.target.value))}
                 style={inputStyle} step="1" />
        </div>
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <button
          onClick={handleRun} disabled={loading}
          style={{
            padding: '8px 18px', background: 'var(--accent-blood)', color: 'white',
            border: 'none', borderRadius: 4, fontFamily: 'var(--font-mono)',
            fontSize: 13, fontWeight: 600, cursor: 'pointer',
            opacity: loading ? 0.5 : 1,
          }}
        >
          {loading ? t('回测中...') : t('运行回测')}
        </button>
        <button
          onClick={handleSweep} disabled={sweepLoading}
          style={{
            padding: '8px 18px', background: 'var(--accent-emerald)', color: 'white',
            border: 'none', borderRadius: 4, fontFamily: 'var(--font-mono)',
            fontSize: 13, fontWeight: 600, cursor: 'pointer',
            opacity: sweepLoading ? 0.5 : 1,
          }}
        >
          {sweepLoading ? t('Sweep 中...') : t('阈值 Sweep（6 组对比）')}
        </button>
      </div>

      {error && (
        <div style={{
          marginTop: 16, padding: 12, background: 'rgba(227,64,88,0.1)',
          border: '1px solid var(--accent-blood)', borderRadius: 4,
          color: 'var(--accent-blood)', fontFamily: 'var(--font-mono)', fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* 单次回测结果 */}
      {result && (
        <div style={{ marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border-subtle)' }}>
          <SectionHeader title={t('回测结果')} subtitle={`${result.summary.snapshots_loaded ?? '?'} snapshots · ${result.trades.length} trades`} />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 12, marginTop: 12,
          }}>
            {Object.entries(result.summary).map(([k, v]) => (
              <div key={k} style={{
                padding: 12, background: 'var(--bg-deepest)',
                border: '1px solid var(--border-subtle)', borderRadius: 4,
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                }}>{k}</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 16, marginTop: 4,
                  color: 'var(--text-primary)',
                }}>
                  {String(v)}
                </div>
              </div>
            ))}
          </div>

          {result.trades.length > 0 && (
            <div style={{
              marginTop: 16, maxHeight: 300, overflow: 'auto',
              border: '1px solid var(--border-subtle)', borderRadius: 4,
            }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead style={{ background: 'var(--bg-deepest)', position: 'sticky', top: 0 }}>
                  <tr>
                    {[t('symbol'), t('long→short'), t('开仓'), t('held'), t('入场 diff%'), t('funding'), t('fees'), t('PnL'), t('reason')].map(h => (
                      <th key={h} style={{
                        padding: '8px', textAlign: 'left', fontWeight: 500,
                        fontSize: 11, textTransform: 'uppercase',
                        color: 'var(--text-tertiary)', borderBottom: '1px solid var(--border-default)',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.trades.slice(0, 50).map((tr, i) => {
                    const pnl = parseFloat(tr.realized_pnl)
                    return (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '6px 8px', fontWeight: 600 }}>{tr.symbol}</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                          {tr.long_exchange}→{tr.short_exchange}
                        </td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)' }}>
                          {tr.open_at.slice(5, 16)}
                        </td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>{tr.held_hours}h</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>{tr.entry_diff_apr_pct}%</td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', color: 'var(--accent-emerald)' }}>
                          +{tr.funding_collected}
                        </td>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                          -{tr.fees_paid}
                        </td>
                        <td style={{
                          padding: '6px 8px', fontFamily: 'var(--font-mono)', fontWeight: 600,
                          color: pnl >= 0 ? '#10b981' : '#ef4444',
                        }}>
                          {pnl >= 0 ? '+' : ''}{tr.realized_pnl}
                        </td>
                        <td style={{ padding: '6px 8px', fontSize: 11, color: 'var(--text-tertiary)' }}>
                          {tr.exit_reason}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Sweep 多阈值对比表 */}
      {sweep && (
        <div style={{ marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--border-subtle)' }}>
          <SectionHeader
            title={t('阈值 Sweep 对比')}
            subtitle={`${sweep.snapshots_loaded} snapshots · 6 thresholds`}
          />
          <div style={{ marginTop: 12, overflow: 'auto', border: '1px solid var(--border-subtle)', borderRadius: 4 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead style={{ background: 'var(--bg-deepest)' }}>
                <tr>
                  {['min_diff_apr (%)', 'trades', 'win%', 'funding $', 'fees $', 'PnL $', 'PnL %'].map(h => (
                    <th key={h} style={{
                      padding: '10px 12px', textAlign: 'left', fontWeight: 500,
                      fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em',
                      color: 'var(--text-tertiary)', borderBottom: '1px solid var(--border-default)',
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sweep.rows.map((r) => {
                  const pnl = parseFloat(r.total_pnl_usd)
                  // 找最优行（PnL 最大）
                  const maxPnl = Math.max(...sweep.rows.map(x => parseFloat(x.total_pnl_usd)))
                  const isBest = pnl === maxPnl && pnl > 0
                  return (
                    <tr key={r.min_diff_apr_pct} style={{
                      borderBottom: '1px solid var(--border-subtle)',
                      background: isBest ? 'rgba(16,185,129,0.08)' : 'transparent',
                    }}>
                      <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                        {isBest && '★ '}{r.min_diff_apr_pct}
                      </td>
                      <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)' }}>{r.num_trades}</td>
                      <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)' }}>{r.win_rate_pct}%</td>
                      <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: 'var(--accent-emerald)' }}>
                        +{r.total_funding_usd}
                      </td>
                      <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                        -{r.total_fees_usd}
                      </td>
                      <td style={{
                        padding: '10px 12px', fontFamily: 'var(--font-mono)', fontWeight: 600,
                        color: pnl >= 0 ? '#10b981' : '#ef4444',
                      }}>
                        {pnl >= 0 ? '+' : ''}{r.total_pnl_usd}
                      </td>
                      <td style={{
                        padding: '10px 12px', fontFamily: 'var(--font-mono)',
                        color: pnl >= 0 ? '#10b981' : '#ef4444',
                      }}>
                        {pnl >= 0 ? '+' : ''}{r.total_pnl_pct}%
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div style={{
            marginTop: 12, fontFamily: 'var(--font-mono)', fontSize: 11,
            color: 'var(--text-muted)',
          }}>
            ★ {t('标记 = PnL 最优阈值。win_rate 越高 + PnL 越大 = 实盘建议参数')}
          </div>
        </div>
      )}
    </CardElevated>
  )
}
