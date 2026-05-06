import { apiClient } from './client'
export async function getDashboardSummary() {
  const { data } = await apiClient.get('/dashboard/summary')
  return data
}
