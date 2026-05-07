import { apiClient } from './client'

export async function getRiskLimits() {
  const { data } = await apiClient.get('/risk/limits')
  return data
}

export async function patchRiskLimits(patch: Record<string, unknown>, confirmWidening = false) {
  const { data } = await apiClient.patch('/risk/limits', { ...patch, confirm_widening: confirmWidening })
  return data
}

export type RiskEvent = {
  time: string
  tier: string
  event: string
  trigger: string
  value: string
  action: string
  auto_recovered: boolean
}

export type RiskEventsResponse = {
  data: RiskEvent[]
  total: number
  days: number
}

export async function getRiskEvents(days = 30): Promise<RiskEventsResponse> {
  const { data } = await apiClient.get<RiskEventsResponse>('/risk/events', { params: { days } })
  return data
}
