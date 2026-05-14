import { apiClient } from './client'

export type ExchangeHealth = {
  name: string
  status: 'active' | 'warn' | 'critical' | 'unconfigured'
  ping_ms: number | null
}

export type ExchangeHealthResponse = {
  data: ExchangeHealth[]
}

export type ActivityItem = {
  icon: 'up' | 'check' | 'warn' | 'zap' | 'x'
  text: string
  time: string
}

export type ActivityResponse = {
  data: ActivityItem[]
}

export async function getExchangeHealth(): Promise<ExchangeHealthResponse> {
  const { data } = await apiClient.get<ExchangeHealthResponse>('/system/exchanges/health')
  return data
}

export async function getActivity(limit = 10): Promise<ActivityResponse> {
  const { data } = await apiClient.get<ActivityResponse>('/system/activity', {
    params: { limit },
  })
  return data
}

export type SymbolsResponse = {
  total: number
  symbols: string[]  // ["BTC/USDT", "ETH/USDT", ...]
}

export async function getSymbols(): Promise<SymbolsResponse> {
  const { data } = await apiClient.get<SymbolsResponse>('/system/symbols')
  return data
}

// ---------------------------------------------------------------------------
// 交易所 API 凭据管理 — 设置页用
// ---------------------------------------------------------------------------

export type ExchangeCredential = {
  exchange: 'binance' | 'okx' | string
  configured: boolean
  api_key_preview: string  // 'abc123...wxyz' or ''
  has_passphrase: boolean
  updated_at: string | null
}

export type ExchangeCredentialsResponse = {
  data: ExchangeCredential[]
}

export type ExchangeCredentialPatch = {
  api_key?: string
  api_secret?: string
  passphrase?: string
}

export async function getExchangeCredentials(): Promise<ExchangeCredentialsResponse> {
  const { data } = await apiClient.get<ExchangeCredentialsResponse>('/system/exchange-credentials')
  return data
}

export async function updateExchangeCredentials(
  exchange: string,
  patch: ExchangeCredentialPatch,
): Promise<ExchangeCredential> {
  const { data } = await apiClient.post<ExchangeCredential>(
    `/system/exchange-credentials/${exchange}`,
    patch,
  )
  return data
}

export type Web3CredentialsMeta = {
  configured: boolean
  wallet_address: string | null
  rpc_url_preview: string | null
  updated_at: string | null
}

export type Web3CredentialsPatch = {
  private_key: string
  rpc_url: string
}

export async function getWeb3Credentials(): Promise<Web3CredentialsMeta> {
  const { data } = await apiClient.get<Web3CredentialsMeta>('/system/web3-credentials')
  return data
}

export async function updateWeb3Credentials(patch: Web3CredentialsPatch): Promise<Web3CredentialsMeta> {
  const { data } = await apiClient.post<Web3CredentialsMeta>('/system/web3-credentials', patch)
  return data
}

// ---------------------------------------------------------------------------
// 通知配置
// ---------------------------------------------------------------------------

export interface NotificationConfig {
  telegram_enabled: boolean
  telegram_bot_token_preview: string   // "abcd1234…" or ""
  telegram_chat_id: string
  email_enabled: boolean
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password_set: boolean
  smtp_from_email: string
  smtp_to_email: string
  updated_at: string | null
}

export interface NotificationConfigPatch {
  telegram_enabled?: boolean
  telegram_bot_token?: string
  telegram_chat_id?: string
  email_enabled?: boolean
  smtp_host?: string
  smtp_port?: number
  smtp_user?: string
  smtp_password?: string
  smtp_from_email?: string
  smtp_to_email?: string
}

export async function getNotificationConfig(): Promise<NotificationConfig> {
  const { data } = await apiClient.get<NotificationConfig>('/system/notifications')
  return data
}

export async function updateNotificationConfig(patch: NotificationConfigPatch): Promise<NotificationConfig> {
  const { data } = await apiClient.post<NotificationConfig>('/system/notifications', patch)
  return data
}

export async function testNotification(channel: 'telegram' | 'email'): Promise<{ status: string; channel: string }> {
  const { data } = await apiClient.post<{ status: string; channel: string }>(`/system/notifications/test?channel=${channel}`)
  return data
}
