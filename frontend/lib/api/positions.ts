import { apiClient } from './client'
export async function getPositions(params?: { status?: string; page?: number; page_size?: number }) {
  const { data } = await apiClient.get('/positions', { params })
  return data
}
export async function closePosition(uuid: string) {
  const { data } = await apiClient.post(`/positions/${uuid}/close`, { confirm: true, reason: 'manual' })
  return data
}
