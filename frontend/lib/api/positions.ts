import { apiClient } from './client'

export type PositionMeta = {
  exchange?: string
  direction?: 'premium' | 'discount'
  spot_size?: string
  perp_size?: string
  entry_spot_px?: string
  entry_perp_px?: string
  close_spot_px?: string
  close_perp_px?: string
  close_fees?: string
  funding_received?: string
  borrow_interest?: string
  client_id?: string
  // #02 perp_basis 跨所双腿元数据
  long_ex?: string
  short_ex?: string
  entry_diff_apr_pct?: string
  strategy?: string
  [k: string]: string | undefined
}

export type PositionLeg = {
  exchange: string
  side: string                          // 'long' | 'short' | 'buy' | 'sell'
  instrument_type: string               // 'perpetual' | 'spot'
  symbol: string
  size: string
  entry_price: string
  current_price?: string | null
  current_funding_rate?: string | null  // e.g. '0.0001' = 0.01%
  current_apr_pct?: string | null       // 折算年化 (rate × periods/year × 100)
  next_funding_time_ms?: number | null
  funding_interval_hours?: number | null
}

export type Position = {
  uuid: string
  symbol: string
  strategy_instance: string
  status: string
  notional_usd: string
  target_apr_pct: string | null
  unrealized_pnl: string
  realized_pnl: string
  funding_received: string
  fees_paid: string
  opened_at: string | null
  closed_at: string | null
  exit_reason: string | null
  days_held: string
  meta?: PositionMeta | null
  legs?: PositionLeg[]
  current_diff_apr_pct?: string | null         // #02 实时 short.apr - long.apr
  current_price_divergence_pct?: string | null // #02 跨所价格分歧 %
}

export type PositionListResponse = {
  data: Position[]
  meta: { page: number; page_size: number; total: number }
}

export async function getPositions(
  params?: { status?: string; page?: number; page_size?: number },
): Promise<PositionListResponse> {
  const { data } = await apiClient.get<PositionListResponse>('/positions', { params })
  return data
}

export async function getPosition(uuid: string): Promise<Position> {
  const { data } = await apiClient.get<Position>(`/positions/${uuid}`)
  return data
}

export async function closePosition(uuid: string) {
  const { data } = await apiClient.post(`/positions/${uuid}/close`, { confirm: true, reason: 'manual' })
  return data
}
