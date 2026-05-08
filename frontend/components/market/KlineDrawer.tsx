'use client'
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useT } from '../i18n/I18nProvider'
import {
  getKlines,
  getOrderbook,
  type KlineBar,
  type KlineInterval,
} from '@/lib/api/market'

type Tab = 'kline' | 'orderbook'

const INTERVALS: { k: KlineInterval; l: string }[] = [
  { k: '15m', l: '15M' },
  { k: '1h', l: '1H' },
  { k: '4h', l: '4H' },
  { k: '1d', l: '1D' },
]

const CHART_W = 760
const PAD_L = 84
const PAD_R = 12
const PAD_T = 8
const PAD_B = 22
const PRICE_H = 200
const VOL_TOP = PAD_T + PRICE_H + 8           // 216
const VOL_H = 56
const RSI_TOP = VOL_TOP + VOL_H + 8           // 280
const RSI_H = 50
const MACD_TOP = RSI_TOP + RSI_H + 8          // 338
const MACD_H = 80
const KDJ_TOP = MACD_TOP + MACD_H + 8         // 426
const KDJ_H = 50
const CHART_H = KDJ_TOP + KDJ_H + PAD_B       // 498

const MA_SHORT = 20
const MA_LONG = 60
const VOL_MA_PERIOD = 20
const RSI_PERIOD = 14
const MACD_FAST = 12
const MACD_SLOW = 26
const MACD_SIGNAL = 9
const BB_PERIOD = 20
const BB_MULT = 2
const KDJ_PERIOD = 9

type KlineDrawerProps = {
  symbol: string
  onClose: () => void
}

function formatPrice(v: number): string {
  if (v >= 1000) return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (v >= 1) return v.toFixed(4)
  return v.toFixed(6)
}

function formatVolume(v: number): string {
  if (v >= 1_000_000_000) return (v / 1_000_000_000).toFixed(2) + 'B'
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(2) + 'M'
  if (v >= 1_000) return (v / 1_000).toFixed(1) + 'K'
  return v.toFixed(0)
}

function computeSMA(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  if (values.length < period) return out
  let sum = 0
  for (let i = 0; i < period; i++) sum += values[i]
  out[period - 1] = sum / period
  for (let i = period; i < values.length; i++) {
    sum += values[i] - values[i - period]
    out[i] = sum / period
  }
  return out
}

function computeEMA(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  if (values.length < period) return out
  const k = 2 / (period + 1)
  let sum = 0
  for (let i = 0; i < period; i++) sum += values[i]
  let prev = sum / period
  out[period - 1] = prev
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

function computeRSI(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null)
  if (closes.length < period + 1) return out
  let gain = 0
  let loss = 0
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1]
    if (diff >= 0) gain += diff
    else loss -= diff
  }
  let avgG = gain / period
  let avgL = loss / period
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1]
    const g = diff > 0 ? diff : 0
    const l = diff < 0 ? -diff : 0
    avgG = (avgG * (period - 1) + g) / period
    avgL = (avgL * (period - 1) + l) / period
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  }
  return out
}

function computeMACD(closes: number[]) {
  const emaFast = computeEMA(closes, MACD_FAST)
  const emaSlow = computeEMA(closes, MACD_SLOW)
  const macd: (number | null)[] = closes.map((_, i) => {
    const a = emaFast[i]
    const b = emaSlow[i]
    return a !== null && b !== null ? a - b : null
  })
  // signal = EMA9 of MACD (only on the non-null slice)
  const validStart = macd.findIndex((v) => v !== null)
  const signal: (number | null)[] = new Array(closes.length).fill(null)
  if (validStart >= 0) {
    const valid = macd.slice(validStart).map((v) => v as number)
    const sig = computeEMA(valid, MACD_SIGNAL)
    for (let i = 0; i < sig.length; i++) {
      if (sig[i] !== null) signal[validStart + i] = sig[i]
    }
  }
  const hist: (number | null)[] = macd.map((m, i) => {
    const s = signal[i]
    return m !== null && s !== null ? m - s : null
  })
  return { macd, signal, hist }
}

function computeBB(
  closes: number[],
  period = 20,
  mult = 2,
): { upper: (number | null)[]; lower: (number | null)[] } {
  const sma = computeSMA(closes, period)
  const upper: (number | null)[] = new Array(closes.length).fill(null)
  const lower: (number | null)[] = new Array(closes.length).fill(null)
  for (let i = period - 1; i < closes.length; i++) {
    const avg = sma[i] as number
    const slice = closes.slice(i - period + 1, i + 1)
    const variance = slice.reduce((s, v) => s + (v - avg) ** 2, 0) / period
    const sd = Math.sqrt(variance)
    upper[i] = avg + mult * sd
    lower[i] = avg - mult * sd
  }
  return { upper, lower }
}

function computeKDJ(
  bars: KlineBar[],
  period = 9,
): { k: (number | null)[]; d: (number | null)[]; j: (number | null)[] } {
  const n = bars.length
  const k: (number | null)[] = new Array(n).fill(null)
  const d: (number | null)[] = new Array(n).fill(null)
  const j: (number | null)[] = new Array(n).fill(null)
  if (n < period) return { k, d, j }
  let prevK = 50
  let prevD = 50
  for (let i = period - 1; i < n; i++) {
    let lo = Infinity
    let hi = -Infinity
    for (let p = i - period + 1; p <= i; p++) {
      const l = parseFloat(bars[p].low)
      const h = parseFloat(bars[p].high)
      if (l < lo) lo = l
      if (h > hi) hi = h
    }
    const c = parseFloat(bars[i].close)
    const rsv = hi === lo ? 50 : ((c - lo) / (hi - lo)) * 100
    const kv = (prevK * 2 + rsv) / 3
    const dv = (prevD * 2 + kv) / 3
    k[i] = kv
    d[i] = dv
    j[i] = 3 * kv - 2 * dv
    prevK = kv
    prevD = dv
  }
  return { k, d, j }
}

export default function KlineDrawer({ symbol, onClose }: KlineDrawerProps) {
  const { t } = useT()
  const [tab, setTab] = useState<Tab>('kline')
  const [interval, setIntervalState] = useState<KlineInterval>('1h')
  const [hover, setHover] = useState<{ idx: number } | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const klineQuery = useQuery({
    queryKey: ['kline', symbol, interval],
    queryFn: () => getKlines({ symbol, interval, limit: 120 }),
    refetchInterval: 5_000,
    enabled: tab === 'kline',
  })

  const obQuery = useQuery({
    queryKey: ['orderbook', symbol],
    queryFn: () => getOrderbook({ symbol, depth: 20 }),
    refetchInterval: 3_000,
    enabled: tab === 'orderbook',
  })

  const bars: KlineBar[] = klineQuery.data?.data ?? []
  const numBars = bars.length

  const closes = useMemo(() => bars.map((b) => parseFloat(b.close)), [bars])
  const volumes = useMemo(() => bars.map((b) => parseFloat(b.volume)), [bars])
  const ma20 = useMemo(() => computeSMA(closes, MA_SHORT), [closes])
  const ma60 = useMemo(() => computeSMA(closes, MA_LONG), [closes])
  const volMa = useMemo(() => computeSMA(volumes, VOL_MA_PERIOD), [volumes])
  const rsi = useMemo(() => computeRSI(closes, RSI_PERIOD), [closes])
  const macdData = useMemo(() => computeMACD(closes), [closes])
  const bb = useMemo(() => computeBB(closes, BB_PERIOD, BB_MULT), [closes])
  const kdjData = useMemo(() => computeKDJ(bars, KDJ_PERIOD), [bars])

  const stats = useMemo(() => {
    if (numBars === 0) return null
    const last = parseFloat(bars[numBars - 1].close)
    const first = parseFloat(bars[0].open)
    const high = Math.max(...bars.map((b) => parseFloat(b.high)))
    const low = Math.min(...bars.map((b) => parseFloat(b.low)))
    const change = first === 0 ? 0 : ((last - first) / first) * 100
    return { last, first, high, low, change }
  }, [bars, numBars])

  const { yMin, yMax, vMax, priceTicks } = useMemo(() => {
    if (numBars === 0) {
      return { yMin: 0, yMax: 1, vMax: 1, priceTicks: [] as number[] }
    }
    let lo = Infinity
    let hi = -Infinity
    let vM = 0
    for (const b of bars) {
      const l = parseFloat(b.low)
      const h = parseFloat(b.high)
      const v = parseFloat(b.volume)
      if (l < lo) lo = l
      if (h > hi) hi = h
      if (v > vM) vM = v
    }
    for (const m of [...ma20, ...ma60, ...bb.upper, ...bb.lower]) {
      if (m === null) continue
      if (m < lo) lo = m
      if (m > hi) hi = m
    }
    const pad = (hi - lo) * 0.05 || hi * 0.01 || 1
    const yMinV = lo - pad
    const yMaxV = hi + pad
    const ticks: number[] = []
    for (let i = 0; i <= 4; i++) {
      ticks.push(yMinV + ((yMaxV - yMinV) * i) / 4)
    }
    return { yMin: yMinV, yMax: yMaxV, vMax: vM || 1, priceTicks: ticks }
  }, [bars, numBars, ma20, ma60, bb])

  // MACD 范围(对称取最大绝对值)
  const macdAbsMax = useMemo(() => {
    let m = 0
    for (const v of [...macdData.macd, ...macdData.signal, ...macdData.hist]) {
      if (v === null) continue
      const a = Math.abs(v)
      if (a > m) m = a
    }
    return m || 1
  }, [macdData])

  const innerW = CHART_W - PAD_L - PAD_R
  const barW = numBars > 0 ? innerW / numBars : 0
  const bodyW = Math.max(1, barW * 0.7)

  const xOf = (i: number) => PAD_L + i * barW + barW / 2
  const yOfPrice = (p: number) => {
    if (yMax === yMin) return PAD_T + PRICE_H / 2
    return PAD_T + ((yMax - p) / (yMax - yMin)) * PRICE_H
  }
  const yOfVol = (v: number) => VOL_TOP + ((vMax - v) / vMax) * VOL_H
  const yOfRsi = (r: number) => RSI_TOP + ((100 - r) / 100) * RSI_H
  const macdMid = MACD_TOP + MACD_H / 2
  const yOfMacd = (m: number) => macdMid - (m / macdAbsMax) * (MACD_H / 2 - 4)
  const yOfKdj = (v: number) => KDJ_TOP + ((100 - Math.min(Math.max(v, -20), 120)) / 140) * KDJ_H

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget
    const pt = svg.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const ctm = svg.getScreenCTM()
    if (!ctm) return
    const local = pt.matrixTransform(ctm.inverse())
    const idx = Math.max(0, Math.min(numBars - 1, Math.floor((local.x - PAD_L) / Math.max(barW, 1))))
    setHover({ idx })
  }

  const hoverBar = hover ? bars[hover.idx] : null

  const linePath = (arr: (number | null)[], yFn: (v: number) => number) => {
    let path = ''
    let started = false
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i]
      if (v === null) continue
      const x = xOf(i)
      const y = yFn(v)
      path += (started ? ' L ' : 'M ') + x.toFixed(1) + ' ' + y.toFixed(1)
      started = true
    }
    return path
  }

  return (
    <>
      <div className="notif-backdrop" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label={`${symbol} K-line`}
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          bottom: 0,
          width: 'min(900px, 100vw)',
          background: 'var(--bg-base)',
          borderLeft: '1px solid var(--border-default)',
          zIndex: 31,
          animation: 'notif-drawer-in 280ms var(--ease-out-expo)',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
        }}
      >
        <header
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--border-default)',
            background: 'var(--bg-deepest)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            position: 'sticky',
            top: 0,
            zIndex: 1,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
            <h3
              style={{
                margin: 0,
                fontFamily: 'var(--font-display)',
                fontSize: 20,
                letterSpacing: '0.04em',
                color: 'var(--text-primary)',
              }}
            >
              {symbol}
            </h3>
            {stats && (
              <>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: 'var(--text-primary)' }}>
                  ${formatPrice(stats.last)}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    color:
                      stats.change > 0
                        ? 'var(--accent-emerald)'
                        : stats.change < 0
                        ? 'var(--accent-blood)'
                        : 'var(--text-tertiary)',
                  }}
                >
                  {stats.change >= 0 ? '+' : ''}
                  {stats.change.toFixed(2)}%
                </span>
              </>
            )}
          </div>
          <button
            onClick={onClose}
            type="button"
            aria-label={t('关闭')}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-tertiary)',
              padding: 4,
              display: 'inline-flex',
            }}
          >
            <X size={18} />
          </button>
        </header>

        {/* Tab 切换 */}
        <div
          style={{
            display: 'flex',
            gap: 0,
            borderBottom: '1px solid var(--border-default)',
            background: 'var(--bg-deepest)',
          }}
        >
          {([
            { k: 'kline' as const, l: t('K 线') },
            { k: 'orderbook' as const, l: t('盘口') },
          ]).map((it) => (
            <button
              key={it.k}
              onClick={() => setTab(it.k)}
              style={{
                fontFamily: 'var(--font-display)',
                fontSize: 12,
                letterSpacing: '0.06em',
                padding: '10px 20px',
                cursor: 'pointer',
                background: 'transparent',
                color: tab === it.k ? 'var(--text-primary)' : 'var(--text-tertiary)',
                border: 'none',
                borderBottom:
                  tab === it.k
                    ? '2px solid var(--accent-blood)'
                    : '2px solid transparent',
              }}
            >
              {it.l}
            </button>
          ))}
        </div>

        {/* K 线视图 */}
        {tab === 'kline' && (
          <>
            <div
              style={{
                padding: '12px 20px',
                display: 'flex',
                gap: 4,
                borderBottom: '1px solid var(--border-subtle)',
                flexWrap: 'wrap',
              }}
            >
              {INTERVALS.map((it) => (
                <button
                  key={it.k}
                  onClick={() => setIntervalState(it.k)}
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-sm)',
                    cursor: 'pointer',
                    background: interval === it.k ? 'var(--accent-blood)' : 'var(--bg-card)',
                    color: interval === it.k ? '#fff' : 'var(--text-secondary)',
                    border:
                      interval === it.k
                        ? '1px solid var(--accent-blood)'
                        : '1px solid var(--border-default)',
                  }}
                >
                  {it.l}
                </button>
              ))}
              <div
                style={{
                  marginLeft: 'auto',
                  display: 'flex',
                  gap: 12,
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  alignItems: 'center',
                  color: 'var(--text-tertiary)',
                  flexWrap: 'wrap',
                }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, height: 2, background: 'var(--accent-gold)' }} />
                  MA20
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, height: 2, background: 'var(--accent-azure)' }} />
                  MA60
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, borderTop: '1px dashed rgba(147,112,219,0.8)' }} />
                  BB20
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, height: 2, background: 'var(--accent-blood)' }} />
                  RSI14
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, height: 2, background: 'var(--accent-emerald)' }} />
                  MACD
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, height: 2, background: '#FF9500' }} />
                  KDJ9
                </span>
              </div>
            </div>

            {stats && (
              <div
                style={{
                  padding: '12px 20px',
                  display: 'grid',
                  gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: 12,
                  borderBottom: '1px solid var(--border-subtle)',
                }}
              >
                {[
                  { l: t('开始'), v: `$${formatPrice(stats.first)}` },
                  { l: t('最高'), v: `$${formatPrice(stats.high)}`, c: 'var(--accent-emerald)' },
                  { l: t('最低'), v: `$${formatPrice(stats.low)}`, c: 'var(--accent-blood)' },
                  { l: t('当前'), v: `$${formatPrice(stats.last)}` },
                ].map((s, i) => (
                  <div key={i}>
                    <div
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 9,
                        color: 'var(--text-tertiary)',
                        textTransform: 'uppercase',
                        letterSpacing: '0.08em',
                      }}
                    >
                      {s.l}
                    </div>
                    <div
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 13,
                        marginTop: 4,
                        color: s.c || 'var(--text-primary)',
                      }}
                    >
                      {s.v}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div style={{ padding: 16, minHeight: CHART_H + 24 }}>
              {klineQuery.isLoading && numBars === 0 && (
                <div
                  style={{
                    height: CHART_H,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    color: 'var(--text-tertiary)',
                  }}
                >
                  {t('加载中…')}
                </div>
              )}
              {klineQuery.isError && (
                <div
                  style={{
                    height: CHART_H,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    color: 'var(--accent-blood)',
                  }}
                >
                  {t('加载失败')}
                </div>
              )}
              {numBars > 0 && (
                <svg
                  width="100%"
                  viewBox={`0 0 ${CHART_W} ${CHART_H}`}
                  onMouseMove={handleMove}
                  onMouseLeave={() => setHover(null)}
                  style={{ display: 'block', cursor: 'crosshair' }}
                >
                  {/* 价格网格 */}
                  {priceTicks.map((p, i) => (
                    <g key={`grid-${i}`}>
                      <line
                        x1={PAD_L}
                        x2={PAD_L + innerW}
                        y1={yOfPrice(p)}
                        y2={yOfPrice(p)}
                        stroke="var(--border-subtle)"
                        strokeDasharray="2 4"
                      />
                      <text
                        x={PAD_L - 8}
                        y={yOfPrice(p) + 4}
                        textAnchor="end"
                        fontSize={10}
                        fontFamily="var(--font-mono)"
                        fill="var(--text-tertiary)"
                      >
                        {formatPrice(p)}
                      </text>
                    </g>
                  ))}

                  {/* 蜡烛 + 成交量柱 */}
                  {bars.map((b, i) => {
                    const o = parseFloat(b.open)
                    const c = parseFloat(b.close)
                    const h = parseFloat(b.high)
                    const l = parseFloat(b.low)
                    const v = parseFloat(b.volume)
                    const up = c >= o
                    const color = up ? 'var(--accent-emerald)' : 'var(--accent-blood)'
                    const x = xOf(i)
                    const yHigh = yOfPrice(h)
                    const yLow = yOfPrice(l)
                    const yOpen = yOfPrice(o)
                    const yClose = yOfPrice(c)
                    const bodyTop = Math.min(yOpen, yClose)
                    const bodyH = Math.max(1, Math.abs(yOpen - yClose))
                    const volH = VOL_TOP + VOL_H - yOfVol(v)
                    return (
                      <g key={i}>
                        <line x1={x} x2={x} y1={yHigh} y2={yLow} stroke={color} strokeWidth={1} />
                        <rect
                          x={x - bodyW / 2}
                          y={bodyTop}
                          width={bodyW}
                          height={bodyH}
                          fill={color}
                          opacity={up ? 0.9 : 1}
                        />
                        <rect
                          x={x - bodyW / 2}
                          y={yOfVol(v)}
                          width={bodyW}
                          height={Math.max(0, volH)}
                          fill={color}
                          opacity={0.45}
                        />
                      </g>
                    )
                  })}

                  {/* MA 叠加 */}
                  <path d={linePath(ma20, yOfPrice)} stroke="var(--accent-gold)" strokeWidth={1.2} fill="none" />
                  <path d={linePath(ma60, yOfPrice)} stroke="var(--accent-azure)" strokeWidth={1.2} fill="none" />

                  {/* Bollinger Bands */}
                  {(() => {
                    const up: string[] = []
                    const lo: string[] = []
                    for (let i = 0; i < bb.upper.length; i++) {
                      if (bb.upper[i] !== null) up.push(`${xOf(i).toFixed(1)},${yOfPrice(bb.upper[i] as number).toFixed(1)}`)
                      if (bb.lower[i] !== null) lo.push(`${xOf(i).toFixed(1)},${yOfPrice(bb.lower[i] as number).toFixed(1)}`)
                    }
                    if (up.length === 0) return null
                    const fillPts = [...up, ...[...lo].reverse()].join(' ')
                    return (
                      <>
                        <polygon points={fillPts} fill="rgba(147,112,219,0.07)" stroke="none" />
                        <path d={linePath(bb.upper, yOfPrice)} stroke="rgba(147,112,219,0.6)" strokeWidth={1} fill="none" strokeDasharray="3 3" />
                        <path d={linePath(bb.lower, yOfPrice)} stroke="rgba(147,112,219,0.6)" strokeWidth={1} fill="none" strokeDasharray="3 3" />
                      </>
                    )
                  })()}

                  {/* VOL MA20 叠加 */}
                  <path d={linePath(volMa, yOfVol)} stroke="var(--accent-gold)" strokeWidth={1} fill="none" opacity={0.85} />
                  <line
                    x1={PAD_L}
                    x2={PAD_L + innerW}
                    y1={VOL_TOP + VOL_H}
                    y2={VOL_TOP + VOL_H}
                    stroke="var(--border-default)"
                  />
                  {/* VOL section label */}
                  <text
                    x={PAD_L - 8}
                    y={VOL_TOP + 12}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--text-tertiary)"
                  >
                    VOL
                  </text>
                  <text
                    x={PAD_L - 8}
                    y={VOL_TOP + VOL_H - 2}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--text-muted)"
                  >
                    {formatVolume(vMax)}
                  </text>

                  {/* RSI 副图 */}
                  <rect
                    x={PAD_L}
                    y={RSI_TOP}
                    width={innerW}
                    height={RSI_H}
                    fill="var(--bg-card)"
                    opacity={0.3}
                  />
                  <line
                    x1={PAD_L}
                    x2={PAD_L + innerW}
                    y1={yOfRsi(70)}
                    y2={yOfRsi(70)}
                    stroke="var(--accent-blood)"
                    strokeDasharray="2 3"
                    opacity={0.4}
                  />
                  <line
                    x1={PAD_L}
                    x2={PAD_L + innerW}
                    y1={yOfRsi(30)}
                    y2={yOfRsi(30)}
                    stroke="var(--accent-emerald)"
                    strokeDasharray="2 3"
                    opacity={0.4}
                  />
                  <text
                    x={PAD_L - 8}
                    y={RSI_TOP + 12}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--text-tertiary)"
                  >
                    RSI
                  </text>
                  <text
                    x={PAD_L - 8}
                    y={yOfRsi(70) + 3}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--accent-blood)"
                    opacity={0.7}
                  >
                    70
                  </text>
                  <text
                    x={PAD_L - 8}
                    y={yOfRsi(30) + 3}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--accent-emerald)"
                    opacity={0.7}
                  >
                    30
                  </text>
                  <path d={linePath(rsi, yOfRsi)} stroke="var(--accent-blood)" strokeWidth={1.2} fill="none" />

                  {/* MACD 副图 */}
                  <rect
                    x={PAD_L}
                    y={MACD_TOP}
                    width={innerW}
                    height={MACD_H}
                    fill="var(--bg-card)"
                    opacity={0.3}
                  />
                  {/* zero line */}
                  <line
                    x1={PAD_L}
                    x2={PAD_L + innerW}
                    y1={macdMid}
                    y2={macdMid}
                    stroke="var(--border-default)"
                  />
                  {/* histogram bars */}
                  {macdData.hist.map((h, i) => {
                    if (h === null) return null
                    const x = xOf(i)
                    const y = yOfMacd(h)
                    const baseY = macdMid
                    const top = Math.min(y, baseY)
                    const height = Math.max(1, Math.abs(y - baseY))
                    const color =
                      h >= 0 ? 'var(--accent-emerald)' : 'var(--accent-blood)'
                    return (
                      <rect
                        key={`hist-${i}`}
                        x={x - bodyW / 2}
                        y={top}
                        width={bodyW}
                        height={height}
                        fill={color}
                        opacity={0.65}
                      />
                    )
                  })}
                  {/* MACD line + signal */}
                  <path d={linePath(macdData.macd, yOfMacd)} stroke="var(--accent-emerald)" strokeWidth={1.2} fill="none" />
                  <path d={linePath(macdData.signal, yOfMacd)} stroke="var(--accent-gold)" strokeWidth={1.2} fill="none" />
                  <text
                    x={PAD_L - 8}
                    y={MACD_TOP + 12}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--text-tertiary)"
                  >
                    MACD
                  </text>
                  <text
                    x={PAD_L - 8}
                    y={MACD_TOP + MACD_H - 2}
                    textAnchor="end"
                    fontSize={9}
                    fontFamily="var(--font-mono)"
                    fill="var(--text-muted)"
                  >
                    12,26,9
                  </text>

                  {/* KDJ 副图 */}
                  <rect x={PAD_L} y={KDJ_TOP} width={innerW} height={KDJ_H} fill="var(--bg-card)" opacity={0.3} />
                  {([80, 50, 20] as const).map((ref) => (
                    <line
                      key={`kdj-ref-${ref}`}
                      x1={PAD_L} x2={PAD_L + innerW}
                      y1={yOfKdj(ref)} y2={yOfKdj(ref)}
                      stroke={ref === 50 ? 'var(--border-default)' : ref === 80 ? 'var(--accent-blood)' : 'var(--accent-emerald)'}
                      strokeDasharray="2 3"
                      opacity={0.4}
                    />
                  ))}
                  <path d={linePath(kdjData.k, yOfKdj)} stroke="#FF9500" strokeWidth={1.2} fill="none" />
                  <path d={linePath(kdjData.d, yOfKdj)} stroke="#FF2D55" strokeWidth={1.2} fill="none" />
                  <path d={linePath(kdjData.j, yOfKdj)} stroke="#5AC8FA" strokeWidth={1} fill="none" opacity={0.8} />
                  <text x={PAD_L - 8} y={KDJ_TOP + 12} textAnchor="end" fontSize={9} fontFamily="var(--font-mono)" fill="var(--text-tertiary)">KDJ</text>
                  <text x={PAD_L - 8} y={yOfKdj(80) + 3} textAnchor="end" fontSize={9} fontFamily="var(--font-mono)" fill="var(--accent-blood)" opacity={0.7}>80</text>
                  <text x={PAD_L - 8} y={yOfKdj(20) + 3} textAnchor="end" fontSize={9} fontFamily="var(--font-mono)" fill="var(--accent-emerald)" opacity={0.7}>20</text>

                  {/* X 轴时间 */}
                  {[0, Math.floor(numBars / 2), numBars - 1].map((i) => {
                    if (i < 0 || i >= numBars) return null
                    const ts = bars[i].time
                    const dt = new Date(ts)
                    const label = `${dt.getUTCMonth() + 1}/${dt.getUTCDate()} ${dt
                      .getUTCHours()
                      .toString()
                      .padStart(2, '0')}:${dt.getUTCMinutes().toString().padStart(2, '0')}`
                    return (
                      <text
                        key={`xt-${i}`}
                        x={xOf(i)}
                        y={CHART_H - 6}
                        textAnchor="middle"
                        fontSize={10}
                        fontFamily="var(--font-mono)"
                        fill="var(--text-tertiary)"
                      >
                        {label}
                      </text>
                    )
                  })}

                  {/* 十字光标 */}
                  {hover && hoverBar && (
                    <line
                      x1={xOf(hover.idx)}
                      x2={xOf(hover.idx)}
                      y1={PAD_T}
                      y2={CHART_H - PAD_B}
                      stroke="var(--text-tertiary)"
                      strokeDasharray="3 3"
                      strokeWidth={1}
                      pointerEvents="none"
                    />
                  )}
                </svg>
              )}
              {hoverBar && (
                <div
                  style={{
                    marginTop: 8,
                    padding: '8px 12px',
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--text-secondary)',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(4, 1fr)',
                    gap: 8,
                  }}
                >
                  {(() => {
                    const idx = hover!.idx
                    const close = parseFloat(hoverBar.close)
                    const bbU = bb.upper[idx]
                    const bbL = bb.lower[idx]
                    const bbPct = bbU !== null && bbL !== null && bbU !== bbL
                      ? ((close - bbL) / (bbU - bbL) * 100).toFixed(1)
                      : '—'
                    const kv = kdjData.k[idx]
                    const dv = kdjData.d[idx]
                    const jv = kdjData.j[idx]
                    return (
                      <>
                        <span>O: <b style={{ color: 'var(--text-primary)' }}>{formatPrice(parseFloat(hoverBar.open))}</b></span>
                        <span>H: <b style={{ color: 'var(--accent-emerald)' }}>{formatPrice(parseFloat(hoverBar.high))}</b></span>
                        <span>L: <b style={{ color: 'var(--accent-blood)' }}>{formatPrice(parseFloat(hoverBar.low))}</b></span>
                        <span>C: <b style={{ color: 'var(--text-primary)' }}>{formatPrice(close)}</b></span>
                        <span>V: <b style={{ color: 'var(--text-secondary)' }}>{formatVolume(parseFloat(hoverBar.volume))}</b></span>
                        <span>RSI: <b style={{ color: 'var(--accent-blood)' }}>{rsi[idx] !== null ? (rsi[idx] as number).toFixed(1) : '—'}</b></span>
                        <span>MACD: <b style={{ color: 'var(--accent-emerald)' }}>{macdData.macd[idx] !== null ? (macdData.macd[idx] as number).toFixed(3) : '—'}</b></span>
                        <span>BB%: <b style={{ color: 'rgba(147,112,219,0.9)' }}>{bbPct}</b></span>
                        <span>K: <b style={{ color: '#FF9500' }}>{kv !== null ? kv.toFixed(1) : '—'}</b></span>
                        <span>D: <b style={{ color: '#FF2D55' }}>{dv !== null ? dv.toFixed(1) : '—'}</b></span>
                        <span>J: <b style={{ color: '#5AC8FA' }}>{jv !== null ? jv.toFixed(1) : '—'}</b></span>
                      </>
                    )
                  })()}
                </div>
              )}
            </div>
          </>
        )}

        {/* 盘口视图 */}
        {tab === 'orderbook' && (
          <OrderbookView
            data={obQuery.data}
            isLoading={obQuery.isLoading}
            isError={obQuery.isError}
            t={t}
          />
        )}

        <div
          style={{
            padding: '12px 20px',
            borderTop: '1px solid var(--border-subtle)',
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            color: 'var(--text-muted)',
            textAlign: 'center',
            marginTop: 'auto',
          }}
        >
          {tab === 'kline'
            ? `${t('5 秒刷新')} · MA20/60 · BB20(2σ) · VOL MA20 · RSI14 · MACD(12,26,9) · KDJ9 · ${numBars} ${t('根')}`
            : `${t('3 秒刷新')} · Binance USDM Perpetual · ${t('盘口前 20 档(左买盘 / 右卖盘)')}`}
        </div>
      </aside>
    </>
  )
}

// ---------------------------------------------------------------------------
// Orderbook 子组件 — 左买盘 + 右卖盘 并排
// ---------------------------------------------------------------------------

function OrderbookView({
  data,
  isLoading,
  isError,
  t,
}: {
  data: { bids: [string, string][]; asks: [string, string][] } | undefined
  isLoading: boolean
  isError: boolean
  t: (s: string) => string
}) {
  const bids = data?.bids ?? []
  const asks = data?.asks ?? []
  const maxQty = useMemo(() => {
    let m = 0
    for (const [, q] of [...bids, ...asks]) {
      const v = parseFloat(q)
      if (v > m) m = v
    }
    return m || 1
  }, [bids, asks])

  if (isLoading && bids.length === 0) {
    return (
      <div style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
        {t('加载中…')}
      </div>
    )
  }
  if (isError) {
    return (
      <div style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-blood)' }}>
        {t('加载失败')}
      </div>
    )
  }

  const bestBid = bids.length > 0 ? parseFloat(bids[0][0]) : 0
  const bestAsk = asks.length > 0 ? parseFloat(asks[0][0]) : 0
  const spread = bestAsk && bestBid ? bestAsk - bestBid : 0
  const spreadPct = bestBid ? (spread / bestBid) * 100 : 0

  const ROWS = 20

  return (
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Spread 顶部 */}
      <div
        style={{
          padding: '10px 12px',
          background: 'var(--bg-card)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-sm)',
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span style={{ color: 'var(--text-tertiary)' }}>{t('最优买')} </span>
        <span style={{ color: 'var(--accent-emerald)' }}>{bestBid > 0 ? formatPrice(bestBid) : '—'}</span>
        <span style={{ color: 'var(--text-tertiary)' }}>
          {t('价差')}{' '}
          <b style={{ color: 'var(--text-primary)' }}>
            {spread.toFixed(2)} ({spreadPct.toFixed(4)}%)
          </b>
        </span>
        <span style={{ color: 'var(--accent-blood)' }}>{bestAsk > 0 ? formatPrice(bestAsk) : '—'}</span>
        <span style={{ color: 'var(--text-tertiary)' }}>{t('最优卖')}</span>
      </div>

      {/* 双栏 grid: 左 BID / 右 ASK */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* 左 — 买盘 BID */}
        <div>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--accent-emerald)',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              marginBottom: 6,
              padding: '0 12px',
              display: 'flex',
              justifyContent: 'space-between',
            }}
          >
            <span>{t('买盘 BID')}</span>
            <span>{t('数量')}</span>
          </div>
          {bids.slice(0, ROWS).map(([p, q], i) => {
            const qty = parseFloat(q)
            const widthPct = (qty / maxQty) * 100
            return (
              <div
                key={`bid-${i}`}
                style={{
                  position: 'relative',
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '4px 12px',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: 0,
                    bottom: 0,
                    width: `${widthPct}%`,
                    background: 'rgba(62, 212, 146, 0.12)',
                    pointerEvents: 'none',
                  }}
                />
                <span style={{ color: 'var(--accent-emerald)', position: 'relative' }}>
                  {formatPrice(parseFloat(p))}
                </span>
                <span style={{ color: 'var(--text-secondary)', position: 'relative' }}>
                  {qty.toFixed(3)}
                </span>
              </div>
            )
          })}
          {bids.length === 0 && (
            <div style={{ padding: 12, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)' }}>—</div>
          )}
        </div>

        {/* 右 — 卖盘 ASK */}
        <div>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--accent-blood)',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              marginBottom: 6,
              padding: '0 12px',
              display: 'flex',
              justifyContent: 'space-between',
            }}
          >
            <span>{t('卖盘 ASK')}</span>
            <span>{t('数量')}</span>
          </div>
          {asks.slice(0, ROWS).map(([p, q], i) => {
            const qty = parseFloat(q)
            const widthPct = (qty / maxQty) * 100
            return (
              <div
                key={`ask-${i}`}
                style={{
                  position: 'relative',
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '4px 12px',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: `${widthPct}%`,
                    background: 'rgba(227, 64, 88, 0.12)',
                    pointerEvents: 'none',
                  }}
                />
                <span style={{ color: 'var(--accent-blood)', position: 'relative' }}>
                  {formatPrice(parseFloat(p))}
                </span>
                <span style={{ color: 'var(--text-secondary)', position: 'relative' }}>
                  {qty.toFixed(3)}
                </span>
              </div>
            )
          })}
          {asks.length === 0 && (
            <div style={{ padding: 12, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)' }}>—</div>
          )}
        </div>
      </div>
    </div>
  )
}
