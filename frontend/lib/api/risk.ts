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


export type RiskThresholds = {
  daily_dd_halt_pct: string
  weekly_dd_halt_pct: string
  min_margin_usage_pct: string
  max_exchange_concentration_pct: string
  max_symbol_concentration_pct: string
  max_binance_mmr_pct: string
}

export async function getRiskThresholds(): Promise<RiskThresholds> {
  const { data } = await apiClient.get<RiskThresholds>("/risk/thresholds")
  return data
}

