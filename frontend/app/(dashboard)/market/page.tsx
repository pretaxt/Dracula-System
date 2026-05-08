'use client'
import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Search } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { StatusDot } from '@/components/ui/Button'
import { useT } from '@/components/i18n/I18nProvider'
import type { MarketTicker } from '@/lib/api/market'
import KlineDrawer from '@/components/market/KlineDrawer'
import { useTickerWS } from '@/hooks/useTickerWS'

type SortKey = 'change' | 'volume' | 'funding' | 'symbol'
type SortDir = 'asc' | 'desc'
type Filter = 'all' | 'gainers' | 'losers'

type CompareRow = {
  symbol: string
  binance: { funding: number; last: number } | null
  okx:     { funding: number; last: number } | null
  spread: number
}

function formatNumber(n: number, dp = 2): string {
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString('en-US', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })
}

function formatPrice(last: number): string {
  if (last === 0) return '—'
  if (last >= 1000) return `$${formatNumber(last, 2)}`
  if (last >= 1) return `$${formatNumber(last, 4)}`
  return `$${last.toFixed(6)}`
}

function formatVolume(vol: number): string {
  if (vol >= 1_000_000_000) return `$${(vol / 1_000_000_000).toFixed(2)}B`
  if (vol >= 1_000_000) return `$${(vol / 1_000_000).toFixed(2)}M`
  if (vol >= 1_000) return `$${(vol / 1_000).toFixed(1)}K`
  return `$${vol.toFixed(0)}`
}

function CountdownToFunding({ nextMs }: { nextMs: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  if (!nextMs) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  const remaining = Math.max(0, nextMs - now)
  const h = Math.floor(remaining / 3_600_000)
  const m = Math.floor((remaining % 3_600_000) / 60_000)
  const s = Math.floor((remaining % 60_000) / 1000)
  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-tertiary)' }}>
      {h.toString().padStart(2, '0')}:{m.toString().padStart(2, '0')}:{s.toString().padStart(2, '0')}
    </span>
  )
}

export default function MarketPage() {
  const { t } = useT()
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('volume')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [filter, setFilter] = useState<Filter>('all')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  const { data: bnRaw, snapshotAt, isConnected: bnConnected } = useTickerWS('binance')
  const { data: okxRaw } = useTickerWS('okx')

  const data = useMemo(() => bnRaw.length ? { data: bnRaw } : null, [bnRaw])
  const okxData = useMemo(() => okxRaw.length ? { data: okxRaw } : null, [okxRaw])
  const isLoading = !bnConnected && bnRaw.length === 0
  const isError = false

  const compareRows = useMemo<CompareRow[]>(() => {
    const bnMap = new Map<string, MarketTicker>()
    for (const r of data?.data ?? []) bnMap.set(r.symbol, r)
    const okxMap = new Map<string, MarketTicker>()
    for (const r of okxData?.data ?? []) okxMap.set(r.symbol, r)

    const allSymbols = Array.from(new Set([...Array.from(bnMap.keys()), ...Array.from(okxMap.keys())]))
    const out: CompareRow[] = []
    for (const sym of allSymbols) {
      const bn = bnMap.get(sym)
      const ok = okxMap.get(sym)
      const bnF = bn ? parseFloat(bn.funding_rate_pct) : null
      const okF = ok ? parseFloat(ok.funding_rate_pct) : null
      const spread = bnF !== null && okF !== null ? Math.abs(bnF - okF) : 0
      out.push({
        symbol: sym,
        binance: bn ? { funding: parseFloat(bn.funding_rate_pct), last: parseFloat(bn.last) } : null,
        okx:     ok ? { funding: parseFloat(ok.funding_rate_pct), last: parseFloat(ok.last) } : null,
        spread,
      })
    }
    return out.sort((a, b) => b.spread - a.spread).slice(0, 15)
  }, [data, okxData])

  const rows: MarketTicker[] = useMemo(() => {
    const all = data?.data ?? []
    let filtered = search
      ? all.filter((r) => r.symbol.toLowerCase().includes(search.toLowerCase()))
      : all

    if (filter === 'gainers') {
      filtered = [...filtered]
        .filter((r) => parseFloat(r.change_24h_pct) > 0)
        .sort((a, b) => parseFloat(b.change_24h_pct) - parseFloat(a.change_24h_pct))
        .slice(0, 5)
      return filtered
    }
    if (filter === 'losers') {
      filtered = [...filtered]
        .filter((r) => parseFloat(r.change_24h_pct) < 0)
        .sort((a, b) => parseFloat(a.change_24h_pct) - parseFloat(b.change_24h_pct))
        .slice(0, 5)
      return filtered
    }

    const sorted = [...filtered].sort((a, b) => {
      const dir = sortDir === 'asc' ? 1 : -1
      switch (sortKey) {
        case 'symbol':
          return a.symbol.localeCompare(b.symbol) * dir
        case 'change':
          return (parseFloat(a.change_24h_pct) - parseFloat(b.change_24h_pct)) * dir
        case 'volume':
          return (parseFloat(a.volume_24h_usd) - parseFloat(b.volume_24h_usd)) * dir
        case 'funding':
          return (parseFloat(a.funding_rate_pct) - parseFloat(b.funding_rate_pct)) * dir
      }
    })
    return sorted
  }, [data, search, sortKey, sortDir, filter])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return <span style={{ width: 12, display: 'inline-block' }} />
    return sortDir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />
  }

  const lastUpdate = snapshotAt
    ? new Date(snapshotAt).toLocaleTimeString('en-US', { hour12: false })
    : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <CardElevated style={{ padding: 20 }}>
        <SectionHeader
          title={t('行情中心')}
          subtitle="LIVE TICKERS · BINANCE USDM PERPETUAL"
          right={
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                fontFamily: 'var(--font-mono)',
                fontSize: 13,
                color: 'var(--text-tertiary)',
              }}
            >
              <StatusDot tone={isError ? 'critical' : isLoading ? 'warn' : 'active'} />
              <span>
                {isError ? t('断流') : isLoading ? t('加载中…') : t('实时')}
              </span>
              {lastUpdate && (
                <span style={{ color: 'var(--text-muted)' }}>
                  {t('上次更新')} {lastUpdate}
                </span>
              )}
            </div>
          }
        />

        {/* Filter chips */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          {([
            { k: 'all' as const, l: t('全部') },
            { k: 'gainers' as const, l: t('涨幅榜 TOP 5') },
            { k: 'losers' as const, l: t('跌幅榜 TOP 5') },
          ]).map(({ k, l }) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 13,
                padding: '6px 12px',
                borderRadius: 'var(--radius-sm)',
                cursor: 'pointer',
                background:
                  filter === k
                    ? k === 'gainers'
                      ? 'var(--accent-emerald)'
                      : k === 'losers'
                      ? 'var(--accent-blood)'
                      : 'var(--accent-blood)'
                    : 'var(--bg-card)',
                color: filter === k ? '#fff' : 'var(--text-secondary)',
                border:
                  filter === k
                    ? '1px solid transparent'
                    : '1px solid var(--border-default)',
                transition: 'all var(--duration-fast)',
              }}
            >
              {l}
            </button>
          ))}
        </div>

        <div style={{ position: 'relative', marginBottom: 16 }}>
          <Search
            size={14}
            style={{
              position: 'absolute',
              left: 12,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--text-tertiary)',
              pointerEvents: 'none',
            }}
          />
          <input
            type="text"
            placeholder={t('搜索币种(如 BTC)')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: '100%',
              padding: '8px 12px 8px 36px',
              fontFamily: 'var(--font-mono)',
              fontSize: 15,
              background: 'var(--bg-card)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              outline: 'none',
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent-blood)'
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-default)'
            }}
          />
        </div>

        <div className="table-scroll-x">
          <table
            className="data-table"
            style={{
              width: '100%',
              borderCollapse: 'separate',
              borderSpacing: 0,
              fontFamily: 'var(--font-mono)',
              fontSize: 14,
            }}
          >
            <thead>
              <tr>
                {([
                  { k: 'symbol' as const,  l: t('币种'),     align: 'left' },
                  { k: null,               l: t('交易所'),   align: 'left' },
                  { k: null,               l: t('现价'),     align: 'right' },
                  { k: 'change' as const,  l: '24H',        align: 'right' },
                  { k: null,               l: t('24H 高'),  align: 'right' },
                  { k: null,               l: t('24H 低'),  align: 'right' },
                  { k: 'volume' as const,  l: t('24H 成交'), align: 'right' },
                  { k: 'funding' as const, l: t('资金费率'), align: 'right' },
                  { k: null,               l: t('下次结算'), align: 'right' },
                ]).map((h, i) => (
                  <th
                    key={i}
                    onClick={h.k ? () => toggleSort(h.k!) : undefined}
                    style={{
                      textAlign: h.align as 'left' | 'right',
                      padding: '10px 12px',
                      color: 'var(--text-tertiary)',
                      fontWeight: 500,
                      fontSize: 12,
                      letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                      borderBottom: '1px solid var(--border-default)',
                      background: 'var(--bg-deepest)',
                      cursor: h.k ? 'pointer' : 'default',
                      userSelect: 'none',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                        justifyContent: h.align === 'right' ? 'flex-end' : 'flex-start',
                      }}
                    >
                      {h.l}
                      {h.k && <SortIcon k={h.k} />}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && !isLoading && (
                <tr>
                  <td
                    colSpan={9}
                    style={{
                      padding: 40,
                      textAlign: 'center',
                      color: 'var(--text-tertiary)',
                    }}
                  >
                    {isError ? t('加载失败,5 秒后自动重试') : t('暂无行情数据')}
                  </td>
                </tr>
              )}
              {rows.map((r) => {
                const last = parseFloat(r.last)
                const change = parseFloat(r.change_24h_pct)
                const vol = parseFloat(r.volume_24h_usd)
                const funding = parseFloat(r.funding_rate_pct)
                const changeColor =
                  change > 0
                    ? 'var(--accent-emerald)'
                    : change < 0
                    ? 'var(--accent-blood)'
                    : 'var(--text-tertiary)'
                const fundingColor =
                  funding > 0.01
                    ? 'var(--accent-emerald)'
                    : funding < -0.01
                    ? 'var(--accent-blood)'
                    : 'var(--text-tertiary)'
                return (
                  <tr
                    key={r.symbol}
                    onClick={() => setSelectedSymbol(r.symbol.split('/')[0])}
                    style={{
                      borderBottom: '1px solid var(--border-subtle)',
                      cursor: 'pointer',
                      transition: 'background var(--duration-fast)',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = 'var(--bg-card-hover)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent'
                    }}
                  >
                    <td style={{ padding: '12px', color: 'var(--accent-blood)', fontWeight: 600 }}>
                      {r.symbol}
                    </td>
                    <td
                      style={{
                        padding: '12px',
                        color: 'var(--text-tertiary)',
                        textTransform: 'capitalize',
                      }}
                    >
                      {r.exchange}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-primary)' }}>
                      {formatPrice(last)}
                    </td>
                    <td
                      style={{
                        padding: '12px',
                        textAlign: 'right',
                        color: changeColor,
                        fontWeight: 500,
                      }}
                    >
                      {change > 0 ? '+' : ''}
                      {change.toFixed(2)}%
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', color: 'var(--accent-emerald)' }}>
                      {r.high_24h && parseFloat(r.high_24h) > 0
                        ? formatPrice(parseFloat(r.high_24h))
                        : '—'}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', color: 'var(--accent-blood)' }}>
                      {r.low_24h && parseFloat(r.low_24h) > 0
                        ? formatPrice(parseFloat(r.low_24h))
                        : '—'}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                      {formatVolume(vol)}
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right', color: fundingColor }}>
                      {funding > 0 ? '+' : ''}
                      {funding.toFixed(4)}%
                    </td>
                    <td style={{ padding: '12px', textAlign: 'right' }}>
                      <CountdownToFunding nextMs={r.next_funding_time_ms} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div
          style={{
            marginTop: 12,
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
            color: 'var(--text-muted)',
          }}
        >
          {t('刷新间隔 5 秒 · 数据来自 Binance USDM Perpetual · 点击行查看 K 线')}
        </div>
      </CardElevated>

      {/* 多交易所资金费对比 */}
      <CardElevated style={{ padding: 20 }}>
        <SectionHeader
          title={t('多交易所资金费对比')}
          subtitle="BINANCE vs OKX · USDM PERPETUAL · TOP 15 BY SPREAD"
          right={
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
              {t('按价差降序')}
            </div>
          }
        />
        <div className="table-scroll-x">
          <table
            className="data-table"
            style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 14 }}
          >
            <thead>
              <tr>
                {[
                  { l: t('币种'),         align: 'left' },
                  { l: 'Binance',         align: 'right' },
                  { l: 'OKX',             align: 'right' },
                  { l: t('价差 Δ'),       align: 'right' },
                  { l: t('套利方向'),     align: 'center' },
                ].map((h, i) => (
                  <th
                    key={i}
                    style={{
                      textAlign: h.align as 'left' | 'right' | 'center',
                      padding: '10px 12px',
                      color: 'var(--text-tertiary)',
                      fontWeight: 500,
                      fontSize: 12,
                      letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                      borderBottom: '1px solid var(--border-default)',
                      background: 'var(--bg-deepest)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {h.l}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {compareRows.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                    {t('加载中…')}
                  </td>
                </tr>
              )}
              {compareRows.map((row) => {
                const bnF = row.binance?.funding ?? null
                const okF = row.okx?.funding ?? null
                const spreadColor = row.spread >= 0.02
                  ? 'var(--accent-emerald)'
                  : row.spread >= 0.005
                  ? 'var(--accent-gold)'
                  : 'var(--text-tertiary)'
                let direction = '—'
                if (bnF !== null && okF !== null) {
                  if (bnF > okF) direction = `↑ Binance · ${t('做空')} Binance / ${t('做多')} OKX`
                  else if (okF > bnF) direction = `↑ OKX · ${t('做空')} OKX / ${t('做多')} Binance`
                }
                return (
                  <tr
                    key={row.symbol}
                    onClick={() => setSelectedSymbol(row.symbol.split('/')[0])}
                    style={{ borderBottom: '1px solid var(--border-subtle)', cursor: 'pointer', transition: 'background var(--duration-fast)' }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-card-hover)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                  >
                    <td style={{ padding: '10px 12px', color: 'var(--accent-blood)', fontWeight: 600 }}>{row.symbol}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: bnF === null ? 'var(--text-muted)' : bnF > 0 ? 'var(--accent-emerald)' : bnF < 0 ? 'var(--accent-blood)' : 'var(--text-tertiary)' }}>
                      {bnF !== null ? `${bnF >= 0 ? '+' : ''}${bnF.toFixed(4)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: okF === null ? 'var(--text-muted)' : okF > 0 ? 'var(--accent-emerald)' : okF < 0 ? 'var(--accent-blood)' : 'var(--text-tertiary)' }}>
                      {okF !== null ? `${okF >= 0 ? '+' : ''}${okF.toFixed(4)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', color: spreadColor, fontWeight: 600 }}>
                      {row.spread > 0 ? `Δ ${row.spread.toFixed(4)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
                      {direction}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 10, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
          {t('Binance 5 秒刷新 · OKX 10 秒刷新 · 价差 ≥ 0.02% 绿色 / ≥ 0.005% 金色 · 点击行查看 K 线')}
        </div>
      </CardElevated>

      {selectedSymbol && (
        <KlineDrawer symbol={selectedSymbol} onClose={() => setSelectedSymbol(null)} />
      )}
    </div>
  )
}
