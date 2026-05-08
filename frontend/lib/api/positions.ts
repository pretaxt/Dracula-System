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
  [k: string]: string | undefined
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
