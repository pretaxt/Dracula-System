import { apiClient } from './client'

export type ExchangeHealth = {
  name: string
  status: 'active' | 'warn' | 'critical' | 'unconfigured'
  ping_ms: number | null
}

export type ExchangeHealthResponse = {
  data: ExchangeHealth[]
}

export type ActivityItem = {
  icon: 'up' | 'check' | 'warn' | 'zap' | 'x'
  text: string
  time: string
}

export type ActivityResponse = {
  data: ActivityItem[]
}

export async function getExchangeHealth(): Promise<ExchangeHealthResponse> {
  const { data } = await apiClient.get<ExchangeHealthResponse>('/system/exchanges/health')
  return data
}

export async function getActivity(limit = 10): Promise<ActivityResponse> {
  const { data } = await apiClient.get<ActivityResponse>('/system/activity', {
    params: { limit },
  })
  return data
}

export type SymbolsResponse = {
  total: number
  symbols: string[]  // ["BTC/USDT", "ETH/USDT", ...]
}

export async function getSymbols(): Promise<SymbolsResponse> {
  const { data } = await apiClient.get<SymbolsResponse>('/system/symbols')
  return data
}
