import { apiClient } from './client'
export async function getStrategyStatus() {
  const { data } = await apiClient.get('/strategies/status')
  return data
}
export async function startStrategy() {
  const { data } = await apiClient.post('/strategies/funding-rate/start')
  return data
}
export async function stopStrategy() {
  const { data } = await apiClient.post('/strategies/funding-rate/stop')
  return data
}
export async function patchStrategyConfig(patch: Record<string, unknown>) {
  const { data } = await apiClient.patch('/strategies/funding-rate/config', patch)
  return data
}
