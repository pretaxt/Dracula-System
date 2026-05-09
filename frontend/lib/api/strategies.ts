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

// ---------------------------------------------------------------------------
// spot-perp 策略配置（D.1.5）
// ---------------------------------------------------------------------------

export type SpotPerpConfig = {
  enabled: boolean
  entry_pct: string
  /** c — premium 方向独立阈值（"0" 回退用 entry_pct） */
  entry_pct_premium: string
  /** c — discount 方向独立阈值（建议 ≥ premium + 0.20% 覆盖借币利息） */
  entry_pct_discount: string
  exit_pct: string
  max_hold_hours: string
  min_hold_minutes?: string
  /** a — 基差扩大止损阈值（"0" 禁用） */
  stop_basis_widening_pct: string
  max_concurrent: number
  notional_per_position: string
  direction_filter: 'premium' | 'discount' | 'both'
  scan_threshold_pct: string
  candidate_symbols: string[]
  exchanges: string[]
  live_mode: boolean
  session_running: boolean
}

export type SpotPerpConfigPatch = Partial<{
  entry_pct: string
  entry_pct_premium: string
  entry_pct_discount: string
  exit_pct: string
  max_hold_hours: string
  min_hold_minutes: string
  stop_basis_widening_pct: string
  max_concurrent: number
  notional_per_position: string
  direction_filter: 'premium' | 'discount' | 'both'
  scan_threshold_pct: string
}>

export async function getSpotPerpConfig(): Promise<SpotPerpConfig> {
  const { data } = await apiClient.get<SpotPerpConfig>('/strategies/spot-perp/config')
  return data
}

export async function patchSpotPerpConfig(patch: SpotPerpConfigPatch): Promise<SpotPerpConfig> {
  const { data } = await apiClient.patch<SpotPerpConfig>('/strategies/spot-perp/config', patch)
  return data
}
