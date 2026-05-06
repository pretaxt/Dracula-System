import { apiClient } from './client'

export async function login(username: string, password: string) {
  const { data } = await apiClient.post('/auth/login', { username, password })
  return data as { access_token: string; expires_at: string }
}

export async function getMe() {
  const { data } = await apiClient.get('/auth/me')
  return data as { username: string; expires_at: string }
}
