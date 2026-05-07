import { apiClient } from './client'

export type AccountBalance = {
  total_equity_usd: string
  available_usd: string
  locked_usd: string
  positions_notional_usd: string
  realized_pnl_usd: string
  unrealized_pnl_usd: string
  currency: string
  updated_at: string
}

export async function getAccountBalance(): Promise<AccountBalance> {
  const { data } = await apiClient.get<AccountBalance>('/account/balance')
  return data
}
