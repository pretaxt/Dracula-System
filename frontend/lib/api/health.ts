import { apiClient } from './client'

export type Health = {
  status: string
  version: string
  uptime_seconds: number
  startup_time: string | null
}

export async function getHealth(): Promise<Health> {
  const { data } = await apiClient.get<Health>('/health')
  return data
}

/** 把秒数格式化为 "12d 04h 23m" 风格 */
export function formatUptime(seconds: number): string {
  if (!seconds || seconds < 0) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${String(hours).padStart(2, '0')}h ${String(mins).padStart(2, '0')}m`
  if (hours > 0) return `${hours}h ${String(mins).padStart(2, '0')}m`
  return `${mins}m`
}
