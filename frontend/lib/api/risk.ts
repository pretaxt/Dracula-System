import { apiClient } from './client'
export async function getRiskLimits() {
  const { data } = await apiClient.get('/risk/limits')
  return data
}
export async function patchRiskLimits(patch: Record<string, unknown>, confirmWidening = false) {
  const { data } = await apiClient.patch('/risk/limits', { ...patch, confirm_widening: confirmWidening })
  return data
}
