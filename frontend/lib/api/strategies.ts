import { apiClient } from './client'

export type StrategyConfig = {
  min_apr_pct: string | null
  max_position_notional_usd: string | null
  max_concurrent_positions: number | null
  scan_interval_seconds: number | null
}

export type StrategyStatus = {
  paper_running: boolean
  runner_running: boolean
  last_scan_at: string | null
  open_positions: number
  current_config: StrategyConfig
  trading_mode: 'paper' | 'live'
}

export async function getStrategyStatus(): Promise<StrategyStatus> {
  const { data } = await apiClient.get<StrategyStatus>('/strategies/status')
  return data
}

export async function startStrategy() {
  const { data } = await apiClient.post('/strategies/funding-rate/start')
  return data
}

export async function stopStrategy() {
  const { data } = await apiClient.post('/strategies/funding-rate/stop')
  return data
}

export async function patchStrategyConfig(patch: Record<string, unknown>) {
  const { data } = await apiClient.patch('/strategies/funding-rate/config', patch)
  return data
}

/** 通用多策略 start — funding-rate 真处理,其他返回 mock 响应壳子 */
export async function startStrategyById(strategyId: string) {
  const { data } = await apiClient.post(`/strategies/${strategyId}/start`)
  return data
}

/** 通用多策略 stop */
export async function stopStrategyById(strategyId: string) {
  const { data } = await apiClient.post(`/strategies/${strategyId}/stop`)
  return data
}

export type SpotPerpOpportunity = {
  symbol: string
  exchange: string
  spot_price: string
  perp_price: string
  basis_abs: string
  basis_pct: string
  direction: 'premium' | 'discount'
  timestamp_ms: number
}

export type SpotPerpOpportunitiesResponse = {
  running: boolean
  last_scan_at: string | null
  data: SpotPerpOpportunity[]
}

export async function getSpotPerpOpportunities(): Promise<SpotPerpOpportunitiesResponse> {
  const { data } = await apiClient.get<SpotPerpOpportunitiesResponse>(
    '/strategies/spot-perp/opportunities',
  )
  return data
}
