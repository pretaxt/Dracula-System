import { apiClient } from './client'

// =============================================================================
// HTX 溢价筛选器 API client
// =============================================================================

export type HTXResultRow = {
  symbol: string                // e.g. "ENJ/USDT"
  ref_exchange: string
  avg_diff_apr_pct: string
  persistence_pct: string
  stddev_diff_apr_pct: string
  score: string
  sample_count: number
  max_diff_apr_pct: string
  last_diff_apr_pct: string
  htx_avg_apr_pct: string
  ref_avg_apr_pct: string
  break_even_hours: string
  recommendation: 'STRONG' | 'MODERATE' | 'WEAK' | 'NOISE'
}

export type HTXScreenerStatus = {
  available: boolean
  is_running?: boolean
  days?: number
  last_run_at?: string | null
  last_elapsed_secs?: number
  last_error?: string | null
  strong_count?: number
  moderate_count?: number
  symbols_screened?: number
  last_applied_at?: string | null
  last_applied_symbols?: string[]
  last_apply_diff?: {
    added: string[]
    removed: string[]
    kept: string[]
  }
}

export type HTXRunResponse = {
  ok: boolean
  days: number
  elapsed_secs: number
  symbols_screened: number
  strong: string[]      // ["COMP/USDT", "ENJ/USDT", ...]
  moderate: string[]
}

export type HTXApplyMode = 'replace' | 'union'
export type HTXTier = 'STRONG' | 'MODERATE' | 'STRONG+MODERATE'

export type HTXApplyBody = {
  tier?: HTXTier
  max_symbols?: number
  protect_open_positions?: boolean
  mode?: HTXApplyMode
}

export type HTXApplyResponse = {
  ok: boolean
  mode: HTXApplyMode
  applied: string[]
  screener_picked: string[]
  protect: string[]
  added: string[]
  removed: string[]
  kept: string[]
}

export async function getHTXScreenerStatus(): Promise<HTXScreenerStatus> {
  const { data } = await apiClient.get<HTXScreenerStatus>(
    '/scanner/htx-premium/status',
  )
  return data
}

export async function runHTXScreener(days?: number): Promise<HTXRunResponse> {
  const body = days ? { days } : {}
  const { data } = await apiClient.post<HTXRunResponse>(
    '/scanner/htx-premium/run', body,
  )
  return data
}

export async function applyHTXScreener(
  body: HTXApplyBody,
): Promise<HTXApplyResponse> {
  const { data } = await apiClient.post<HTXApplyResponse>(
    '/scanner/htx-premium/apply', body,
  )
  return data
}
