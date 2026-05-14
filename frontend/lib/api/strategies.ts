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
  /** 通用入场基差阈值（兜底，per-direction 为 0 时使用） */
  entry_pct?: string
  /** premium 方向独立入场阈值（"0" 回退 entry_pct） */
  entry_pct_premium?: string
  /** discount 方向独立入场阈值（"0" 回退 entry_pct） */
  entry_pct_discount?: string
  /** 候选展示门槛（UI footer 用） */
  scan_threshold_pct?: string
  data: SpotPerpOpportunity[]
}

export async function getSpotPerpOpportunities(): Promise<SpotPerpOpportunitiesResponse> {
  const { data } = await apiClient.get<SpotPerpOpportunitiesResponse>(
    '/strategies/spot-perp/opportunities',
  )
  return data
}

// ---------------------------------------------------------------------------
// funding-rate 实时机会（候选展示，含 passes_entry 标志）
// ---------------------------------------------------------------------------

export type FundingRateOpportunity = {
  symbol: string
  exchange: string
  apr_pct: string
  funding_rate: string
  funding_interval_hours: number
  next_funding_time_ms: number
  history_positive_count: number
  history_total_count: number
  spot_depth_usd: string
  perp_depth_usd: string
  /** True = APR ≥ min_apr_pct，会真实开仓；False = 仅展示候选 */
  passes_entry: boolean
  /** max(0, min_apr_pct - apr_pct) — 距离入场门槛百分点 */
  distance_to_entry_pct: string
}

export type FundingRateOpportunitiesResponse = {
  running: boolean
  last_scan_at: string | null
  min_apr_pct: string
  scan_threshold_apr_pct: string
  /** 结算前 N 分钟入场窗口；UI 区分「可开仓」与「等窗口」 */
  pre_funding_window_minutes?: number
  data: FundingRateOpportunity[]
}

export async function getFundingRateOpportunities(): Promise<FundingRateOpportunitiesResponse> {
  const { data } = await apiClient.get<FundingRateOpportunitiesResponse>(
    '/strategies/funding-rate/opportunities',
  )
  return data
}


// ---------------------------------------------------------------------------
// #02 perp-basis 跨所 funding 差套利
// ---------------------------------------------------------------------------

export type PerpBasisOpportunity = {
  symbol: string
  long_exchange: string
  short_exchange: string
  long_funding_rate: string
  short_funding_rate: string
  long_apr_pct: string
  short_apr_pct: string
  diff_apr_pct: string
  long_funding_interval_hours: number
  short_funding_interval_hours: number
  long_next_funding_ms: number
  short_next_funding_ms: number
  timestamp_ms: number
  health_tier?: 'safe' | 'risky' | 'dirty'
}

export type PerpBasisOpportunitiesResponse = {
  running: boolean
  last_scan_at: string | null
  min_diff_apr_pct: string
  exchange_pair_count: number
  data: PerpBasisOpportunity[]
}

export async function getPerpBasisOpportunities(): Promise<PerpBasisOpportunitiesResponse> {
  const { data } = await apiClient.get<PerpBasisOpportunitiesResponse>(
    '/strategies/perp-basis/opportunities',
  )
  return data
}

// #02 配置 GET/PATCH
export type PerpBasisConfig = {
  enabled: boolean
  paper_running: boolean
  min_diff_apr_pct: string
  max_concurrent: number
  notional_per_position: string
  max_hold_hours: string
  min_hold_hours: string
  exit_diff_apr_pct: string
  stop_price_divergence_pct: string
  candidate_symbols: string[]
  scan_interval_seconds: number
}

export type PerpBasisConfigPatch = Partial<{
  min_diff_apr_pct: string
  max_concurrent: number
  notional_per_position: string
  max_hold_hours: string
  min_hold_hours: string
  exit_diff_apr_pct: string
  stop_price_divergence_pct: string
}>

export async function getPerpBasisConfig(): Promise<PerpBasisConfig> {
  const { data } = await apiClient.get<PerpBasisConfig>('/strategies/perp-basis/config')
  return data
}

export async function patchPerpBasisConfig(p: PerpBasisConfigPatch): Promise<PerpBasisConfig> {
  const { data } = await apiClient.patch<PerpBasisConfig>('/strategies/perp-basis/config', p)
  return data
}

// #02 per-exchange perp 余额
export type PerpBasisExchangeBalance = {
  exchange: string
  perp_usdt_free: string
  perp_usdt_total: string
  ready: boolean
  wallet_breakdown?: Record<string, number>
}

export async function getPerpBasisExchangeBalance(): Promise<{
  data: PerpBasisExchangeBalance[]
  ready: boolean
}> {
  const { data } = await apiClient.get<{
    data: PerpBasisExchangeBalance[]
    ready: boolean
  }>('/strategies/perp-basis/exchange-balance')
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
  /** b — 入场时机过滤：滑窗时长（分钟） */
  peak_window_minutes: string
  /** b — 入场时机过滤：最少回落幅度（pct，"0" 禁用） */
  min_peak_dropoff_pct: string
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
  peak_window_minutes: string
  min_peak_dropoff_pct: string
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


// ---------------------------------------------------------------------------
// #05 CEX-DEX 套利 (Arbitrum One)
// ---------------------------------------------------------------------------

export type CexDexStatus = {
  running: boolean
  mode: 'paper' | 'live' | 'unconfigured'
  open_trades: number
  daily_loss_usd: number
  max_daily_loss_usd: number
  eth_usd: number
  last_scan_at: string | null
}

export type CexDexConfig = {
  execution_mode: string
  scan_interval_seconds: number
  max_trade_usd: number
  max_daily_loss_usd: number
  max_gas_gwei: number
  min_net_profit_usd: number
  max_slippage_bps: number
  cex_exchange: string
  pairs: { base: string; quote: string; pool_fee: number; cex_symbol: string }[]
}

export type CexDexOpportunity = {
  pair: string
  direction: 'cex_cheap' | 'dex_cheap'
  cex_price: string
  dex_price: string
  raw_spread_bps: string
  estimated_gas_usd: string
  net_profit_usd: string
  trade_usd: string
}

export type CexDexOpportunitiesResponse = {
  running: boolean
  mode: string
  last_scan_at: string | null
  min_net_profit_usd: number
  eth_usd: number
  data: CexDexOpportunity[]
}

export type CexDexConfigPatch = Partial<{
  execution_mode: 'paper' | 'live'
  min_net_profit_usd: number
  max_trade_usd: number
  max_daily_loss_usd: number
  max_gas_gwei: number
}>

export async function getCexDexStatus(): Promise<CexDexStatus> {
  const { data } = await apiClient.get<CexDexStatus>('/strategies/cex-dex/status')
  return data
}

export async function getCexDexConfig(): Promise<CexDexConfig> {
  const { data } = await apiClient.get<CexDexConfig>('/strategies/cex-dex/config')
  return data
}

export async function patchCexDexConfig(patch: CexDexConfigPatch): Promise<CexDexConfig> {
  const { data } = await apiClient.patch<CexDexConfig>('/strategies/cex-dex/config', patch)
  return data
}

export async function getCexDexOpportunities(): Promise<CexDexOpportunitiesResponse> {
  const { data } = await apiClient.get<CexDexOpportunitiesResponse>('/strategies/cex-dex/opportunities')
  return data
}


export type CexDexWalletBalance = {
  configured: boolean
  wallet_address: string | null
  balances: { token: string; amount: string; usd: string }[]
  total_usd: string
}

export async function getCexDexWalletBalance(): Promise<CexDexWalletBalance> {
  const { data } = await apiClient.get<CexDexWalletBalance>('/strategies/cex-dex/wallet-balance')
  return data
}

// ── CEX-DEX 新增类型 ──────────────────────────────────────────────────────────

export type CexDexCexBalance = {
  configured: boolean
  eth: string
  usdt: string
  eth_price: string
  total_usd: string
}

export type CexDexSpreadEntry = {
  pair: string
  cex_bid: number
  cex_ask: number
  dex_buy: number
  dex_sell: number
  spread_cex_cheap_bps: number
  spread_dex_cheap_bps: number
  gas_usd: number
  ts: string
}

export type CexDexSpreadsResponse = {
  data: CexDexSpreadEntry[]
  threshold_usd: number
  max_trade_usd: number
  last_scan_at: string | null
  scan_count: number
}

export type CexDexPaperTrade = {
  timestamp: string
  pair: string
  direction: 'cex_cheap' | 'dex_cheap'
  cex_price: number
  dex_price: number
  spread_bps: number
  net_profit_usd: number
  gas_usd: number
  trade_usd: number
}

export type CexDexPaperHistoryResponse = {
  data: CexDexPaperTrade[]
  total: number
}

export async function getCexDexCexBalance(): Promise<CexDexCexBalance> {
  const { data } = await apiClient.get<CexDexCexBalance>('/strategies/cex-dex/cex-balance')
  return data
}

export async function getCexDexSpreads(): Promise<CexDexSpreadsResponse> {
  const { data } = await apiClient.get<CexDexSpreadsResponse>('/strategies/cex-dex/spreads')
  return data
}

export async function getCexDexPaperHistory(): Promise<CexDexPaperHistoryResponse> {
  const { data } = await apiClient.get<CexDexPaperHistoryResponse>('/strategies/cex-dex/paper-history')
  return data
}


// =============================================================================
// 聚合实时机会（dashboard 卡片用）
// =============================================================================

export type AllOpportunity = {
  strategy: string             // funding_rate | perp_basis | price_spread | spot_perp
  symbol: string
  exchange: string             // 单 ex 或 "long→short"
  apr_pct: string              // 主指标
  extra_pct: string
  meta: string
}

export type AllOpportunitiesResponse = {
  data: AllOpportunity[]
  count: number
}

export async function getAllOpportunities(): Promise<AllOpportunitiesResponse> {
  const { data } = await apiClient.get<AllOpportunitiesResponse>(
    "/strategies/all-opportunities",
  )
  return data
}

