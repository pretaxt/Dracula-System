'use client'
import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { useT } from '@/components/i18n/I18nProvider'
import { runBacktest, type BacktestResult, type EquityPoint } from '@/lib/api/backtest'
import { getSymbols } from '@/lib/api/system'

const FALLBACK_SYMBOLS = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP']
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
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
    </div>
  )
}
