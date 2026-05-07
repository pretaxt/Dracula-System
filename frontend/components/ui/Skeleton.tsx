'use client'
import type { CSSProperties } from 'react'

const RADIUS_MAP = {
  sm:   'var(--radius-sm)',
  md:   'var(--radius-md)',
  lg:   'var(--radius-lg)',
  full: '999px',
} as const

type SkeletonProps = {
  width?: number | string
  height?: number | string
  rounded?: keyof typeof RADIUS_MAP
  style?: CSSProperties
}

export function Skeleton({ width = '100%', height = 16, rounded = 'sm', style }: SkeletonProps) {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: RADIUS_MAP[rounded],
        background: `linear-gradient(
          90deg,
          var(--bg-card) 0%,
          var(--bg-card-hover) 50%,
          var(--bg-card) 100%
        )`,
        backgroundSize: '200% 100%',
        animation: 'skeleton-shimmer 1.6s ease-in-out infinite',
        ...style,
      }}
    />
  )
}

type SkeletonRowsProps = {
  count?: number
  heights?: number[]
  gap?: number
}

export function SkeletonRows({ count = 5, heights, gap = 8 }: SkeletonRowsProps) {
  const items = Array.from({ length: count })
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }}>
      {items.map((_, i) => (
        <Skeleton key={i} height={heights ? heights[i % heights.length] : 16} />
      ))}
    </div>
  )
}

export function SkeletonKpiCard() {
  return (
    <div
      style={{
        background: 'linear-gradient(180deg, var(--bg-card) 0%, var(--bg-base) 100%)',
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-md)',
        padding: '18px 20px',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <Skeleton width={80} height={10} style={{ marginBottom: 12 }} />
      <Skeleton width="60%" height={28} />
      <Skeleton width="40%" height={10} style={{ marginTop: 8 }} />
    </div>
  )
}
