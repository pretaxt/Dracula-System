import axios from 'axios'
import { useAuthStore } from '../auth/token-store'

// 同域: dev 由 Next rewrites 代理到生产后端 (绕过 CORS)
//       prod docker nginx 反代 /api/v1/* 到后端容器
export const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

apiClient.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      useAuthStore.getState().clearToken()
      if (typeof window !== 'undefined') window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)
