import { apiClient } from './client'
export async function getOpportunities() {
  const { data } = await apiClient.get('/funding-rates/opportunities')
  return data
}
