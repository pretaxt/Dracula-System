import { useEffect, useRef, useState } from 'react'
import { useAuthStore } from '@/lib/auth/token-store'
import type { MarketTicker } from '@/lib/api/market'

interface WSPayload {
  data: MarketTicker[]
  snapshot_at: number
}

export function useTickerWS(exchange: string = 'binance') {
  const [data, setData] = useState<MarketTicker[]>([])
  const [snapshotAt, setSnapshotAt] = useState<number>(0)
  const [isConnected, setIsConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const token = useAuthStore((s) => s.token)

  useEffect(() => {
    if (!token) return

    let cancelled = false

    function connect() {
      if (cancelled) return
      let base: string
      if (typeof window !== 'undefined') {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        base = `${proto}//${window.location.host}`
      } else {
        base = (process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000')
          .replace(/^https/, 'wss')
          .replace(/^http/, 'ws')
      }
      const url = `${base}/api/v1/market/ws/tickers?token=${token}&exchange=${exchange}`
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => { if (!cancelled) setIsConnected(true) }
      ws.onmessage = (e: MessageEvent) => {
        if (cancelled) return
        try {
          const msg: WSPayload = JSON.parse(e.data as string)
          setData(msg.data)
          setSnapshotAt(msg.snapshot_at)
        } catch { /* ignore malformed */ }
      }
      ws.onclose = () => {
        if (cancelled) return
        setIsConnected(false)
        retryRef.current = setTimeout(connect, 3_000)
      }
      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      cancelled = true
      if (retryRef.current) clearTimeout(retryRef.current)
      wsRef.current?.close()
      setIsConnected(false)
    }
  }, [token, exchange])

  return { data, snapshotAt, isConnected }
}
