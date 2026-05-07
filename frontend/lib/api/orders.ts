import { apiClient } from './client'

export type Order = {
  time: string
  exchange: string
  symbol: string
  order_type: string
  side: string
  amount: string
  price: string
  status: string
  position_uuid: string
}

export type OrdersResponse = {
  data: Order[]
  total: number
}

export async function getOrders(limit = 20): Promise<OrdersResponse> {
  const { data } = await apiClient.get<OrdersResponse>('/orders', { params: { limit } })
  return data
}
