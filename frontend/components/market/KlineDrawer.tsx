'use client'
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useT } from '../i18n/I18nProvider'
import { getKlines, type KlineBar, type KlineInterval } from '@/lib/api/market'

const INTERVALS: { k: KlineInterval; l: string }[] = [
  { k: '15m', l: '15M' },
  { k: '1h', l: '1H' },
  { k: '4h', l: '4H' },
  { k: '1d', l: '1D' },
]

const CHART_W = 760
const CHART_H = 360
const PAD_L = 84
const PAD_R = 12
const PAD_T = 12
const PAD_B = 36
const VOL_H = 70

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

export default function KlineDrawer({ symbol, onClose }: KlineDrawerProps) {
  const { t } = useT()
  const [interval, setInterval] = useState<KlineInterval>('1h')
  const [hover, setHover] = useState<{ idx: number; x: number; y: number } | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const { data, isLoading, isError } = useQuery({
    queryKey: ['kline', symbol, interval],
    queryFn: () => getKlines({ symbol, interval, limit: 120 }),
    refetchInterval: 5_000,
  })

  const bars: KlineBar[] = data?.data ?? []
  const numBars = bars.length

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
    const pad = (hi - lo) * 0.05 || hi * 0.01 || 1
    const yMinV = lo - pad
    const yMaxV = hi + pad
    const ticks: number[] = []
    for (let i = 0; i <= 4; i++) {
      ticks.push(yMinV + ((yMaxV - yMinV) * i) / 4)
    }
    return { yMin: yMinV, yMax: yMaxV, vMax: vM || 1, priceTicks: ticks }
  }, [bars, numBars])

  const innerW = CHART_W - PAD_L - PAD_R
  const innerH = CHART_H - PAD_T - PAD_B
  const priceH = innerH - VOL_H - 8
  const volTop = PAD_T + priceH + 8
  const barW = numBars > 0 ? innerW / numBars : 0
  const bodyW = Math.max(1, barW * 0.7)

  const xOf = (i: number) => PAD_L + i * barW + barW / 2
  const yOfPrice = (p: number) => {
    if (yMax === yMin) return PAD_T + priceH / 2
    return PAD_T + ((yMax - p) / (yMax - yMin)) * priceH
  }
  const yOfVol = (v: number) => volTop + ((vMax - v) / vMax) * VOL_H

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget
    const pt = svg.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const ctm = svg.getScreenCTM()
    if (!ctm) return
    const local = pt.matrixTransform(ctm.inverse())
    const idx = Math.max(0, Math.min(numBars - 1, Math.floor((local.x - PAD_L) / Math.max(barW, 1))))
    setHover({ idx, x: local.x, y: local.y })
  }

  const hoverBar = hover ? bars[hover.idx] : null

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

        <div
          style={{
            padding: '12px 20px',
            display: 'flex',
            gap: 4,
            borderBottom: '1px solid var(--border-subtle)',
          }}
        >
          {INTERVALS.map((it) => (
            <button
              key={it.k}
              onClick={() => setInterval(it.k)}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                padding: '4px 10px',
                borderRadius: 'var(--radius-sm)',
                cursor: 'pointer',
                background: interval === it.k ? 'var(--accent-blood)' : 'var(--bg-card)',
                color: interval === it.k ? '#fff' : 'var(--text-secondary)',
                border: interval === it.k ? '1px solid var(--accent-blood)' : '1px solid var(--border-default)',
              }}
            >
              {it.l}
            </button>
          ))}
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
          {isLoading && numBars === 0 && (
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
          {isError && (
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

              <line
                x1={PAD_L}
                x2={PAD_L + innerW}
                y1={volTop + VOL_H}
                y2={volTop + VOL_H}
                stroke="var(--border-default)"
              />

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
                const volH = volTop + VOL_H - yOfVol(v)
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
                    y={CHART_H - 12}
                    textAnchor="middle"
                    fontSize={10}
                    fontFamily="var(--font-mono)"
                    fill="var(--text-tertiary)"
                  >
                    {label}
                  </text>
                )
              })}

              {hover && hoverBar && (
                <g pointerEvents="none">
                  <line
                    x1={xOf(hover.idx)}
                    x2={xOf(hover.idx)}
                    y1={PAD_T}
                    y2={CHART_H - PAD_B}
                    stroke="var(--text-tertiary)"
                    strokeDasharray="3 3"
                    strokeWidth={1}
                  />
                </g>
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
                gridTemplateColumns: 'repeat(5, 1fr)',
                gap: 8,
              }}
            >
              <span>O: <b style={{ color: 'var(--text-primary)' }}>{formatPrice(parseFloat(hoverBar.open))}</b></span>
              <span>H: <b style={{ color: 'var(--accent-emerald)' }}>{formatPrice(parseFloat(hoverBar.high))}</b></span>
              <span>L: <b style={{ color: 'var(--accent-blood)' }}>{formatPrice(parseFloat(hoverBar.low))}</b></span>
              <span>C: <b style={{ color: 'var(--text-primary)' }}>{formatPrice(parseFloat(hoverBar.close))}</b></span>
              <span>V: <b style={{ color: 'var(--text-secondary)' }}>{formatVolume(parseFloat(hoverBar.volume))}</b></span>
            </div>
          )}
        </div>

        <div
          style={{
            padding: '12px 20px',
            borderTop: '1px solid var(--border-subtle)',
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            color: 'var(--text-muted)',
            textAlign: 'center',
          }}
        >
          {t('5 秒刷新 · Binance USDM Perpetual · 共')} {numBars} {t('根')}
        </div>
      </aside>
    </>
  )
}
