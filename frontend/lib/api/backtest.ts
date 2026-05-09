import { apiClient } from './client'

export interface BacktestRequest {
  symbol: string
  exchange: string
  days: number
  initial_capital_usd: number
  size_per_trade_usd: number
  min_apr_pct: number
  max_positions: number
  stop_loss_pct: number
  max_hold_hours: number
}

export interface EquityPoint {
  ts: number
  equity: number
  open_pos: number
}

export interface TradeRow {
  opened_at: string
  closed_at: string | null
  symbol: string
  funding: number
  fees: number
  pnl: number
}

export interface BacktestResult {
  total_return_pct: number
  annualized_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  win_rate_pct: number
  total_trades: number
  total_funding_usd: number
  total_fees_usd: number
  final_equity_usd: number
  periods_days: number
  equity_curve: EquityPoint[]
  trades: TradeRow[]
}

export async function runBacktest(req: BacktestRequest): Promise<BacktestResult> {
  const { data } = await apiClient.post<BacktestResult>('/backtest/run', req)
  return data
}


// ---------------------------------------------------------------------------
// #04 spot-perp 回测
// ---------------------------------------------------------------------------

export interface SpotPerpBacktestRequest {
  symbols: string[]
  exchange: string
  days: number
  timeframe: string
  initial_capital_usd: number
  notional_per_position: number
  max_concurrent: number
  entry_pct: number
  entry_pct_premium: number
  entry_pct_discount: number
  exit_pct: number
  max_hold_hours: number
  min_hold_minutes: number
  stop_basis_widening_pct: number
  peak_window_minutes: number
  min_peak_dropoff_pct: number
  direction_filter: 'premium' | 'discount' | 'both'
  slippage_pct: number
  fee_rate: number
}

export interface SpotPerpTradeRow {
  symbol: string
  direction: string
  entry_time: string
  exit_time: string | null
  entry_basis_pct: string
  exit_basis_pct: string
  held_hours: string
  fees_paid: string
  realized_pnl: string
  exit_reason: string
}

export interface SpotPerpBacktestResult {
  summary: Record<string, unknown>
  trades: SpotPerpTradeRow[]
  equity_curve: { ts: number; equity: number; open_pos: number }[]
}

export async function runSpotPerpBacktest(
  req: SpotPerpBacktestRequest,
): Promise<SpotPerpBacktestResult> {
  const { data } = await apiClient.post<SpotPerpBacktestResult>(
    '/backtest/spot-perp', req,
  )
  return data
}
