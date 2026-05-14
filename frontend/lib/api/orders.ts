import { apiClient } from './client'

export type Order = {
  time: string
  exchange: string
  symbol: string
  order_type: string
  side: string
  amount: string
  pnl: string        // 平仓: "+0.1234" / "-0.0056"；开仓: "—"
  status: string
  position_uuid: string
  exit_reason: string  // 平仓原因中文标签；开仓: "—"
}

export type OrdersResponse = {
  data: Order[]
  total: number
}

export async function getOrders(limit = 20): Promise<OrdersResponse> {
  const { data } = await apiClient.get<OrdersResponse>('/orders', { params: { limit } })
  return data
}
