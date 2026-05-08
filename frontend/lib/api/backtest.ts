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
