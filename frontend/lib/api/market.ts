import { apiClient } from './client'

export type MarketTicker = {
  symbol: string
  exchange: string
  last: string
  change_24h_pct: string
  volume_24h_usd: string
  high_24h?: string
  low_24h?: string
  funding_rate: string
  funding_rate_pct: string
  next_funding_time_ms: number
  ts: number
}

export type MarketTickersResponse = {
  data: MarketTicker[]
  snapshot_at: number
}

export async function getMarketTickers(opts?: {
  symbols?: string[]
  exchange?: string
}): Promise<MarketTickersResponse> {
  const params: Record<string, string> = {}
  if (opts?.symbols && opts.symbols.length > 0) {
    params.symbols = opts.symbols.join(',')
  }
  if (opts?.exchange) {
    params.exchange = opts.exchange
  }
  const { data } = await apiClient.get<MarketTickersResponse>('/market/tickers', {
    params,
  })
  return data
}

export type KlineBar = {
  time: number
  open: string
  high: string
  low: string
  close: string
  volume: string
}

export type KlinesResponse = {
  symbol: string
  interval: string
  data: KlineBar[]
}

export type KlineInterval = '15m' | '1h' | '4h' | '1d'

export async function getKlines(opts: {
  symbol: string
  interval?: KlineInterval
  limit?: number
  exchange?: string
}): Promise<KlinesResponse> {
  const { data } = await apiClient.get<KlinesResponse>('/market/klines', {
    params: {
      symbol: opts.symbol,
      interval: opts.interval ?? '1h',
      limit: opts.limit ?? 100,
      exchange: opts.exchange ?? 'binance',
    },
  })
  return data
}

export type OrderbookResponse = {
  symbol: string
  bids: [string, string][]
  asks: [string, string][]
  ts: number
}

export async function getOrderbook(opts: {
  symbol: string
  depth?: number
  exchange?: string
}): Promise<OrderbookResponse> {
  const { data } = await apiClient.get<OrderbookResponse>('/market/orderbook', {
    params: {
      symbol: opts.symbol,
      depth: opts.depth ?? 20,
      exchange: opts.exchange ?? 'binance',
    },
  })
  return data
}
