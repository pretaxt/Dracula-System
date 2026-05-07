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

/** 通用多策略 start — funding-rate 真处理,其他返回 mock 响应壳子 */
export async function startStrategyById(strategyId: string) {
  const { data } = await apiClient.post(`/strategies/${strategyId}/start`)
  return data
}

/** 通用多策略 stop */
export async function stopStrategyById(strategyId: string) {
  const { data } = await apiClient.post(`/strategies/${strategyId}/stop`)
  return data
}
