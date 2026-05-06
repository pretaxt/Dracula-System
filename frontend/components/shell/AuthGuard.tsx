'use client'
import { useAuthStore } from '@/lib/auth/token-store'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const router = useRouter()
  useEffect(() => {
    if (!isAuthenticated()) router.replace('/login')
  }, [isAuthenticated, router])
  if (!isAuthenticated()) return null
  return <>{children}</>
}
