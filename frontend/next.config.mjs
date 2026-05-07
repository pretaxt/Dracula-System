/** @type {import('next').NextConfig} */
const API_TARGET = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://43.160.207.185'

const nextConfig = {
  output: 'standalone',
  env: {
    NEXT_PUBLIC_API_BASE_URL: API_TARGET,
  },
  // 同域代理 — 绕过生产后端 CORS 限制
  // 浏览器调 /api/v1/* 由 Next server 转发到 API_TARGET
  async rewrites() {
    return [
      { source: '/api/v1/:path*', destination: `${API_TARGET}/api/v1/:path*` },
    ]
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ]
  },
}
export default nextConfig
