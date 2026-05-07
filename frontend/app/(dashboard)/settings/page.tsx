'use client'
import { Info } from 'lucide-react'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Button'

const ABOUT: { label: string; value: string }[] = [
  { label: '系统版本',     value: 'v1.0.0' },
  { label: '已运行时间',   value: '12d 04h 23m' },
  { label: '主密钥版本',   value: 'v3 (已轮换 2 次)' },
  { label: '数据库',       value: 'PostgreSQL 16 + TimescaleDB' },
  { label: '事件总线',     value: 'Redis 7.2' },
  { label: '部署位置',     value: 'Tencent Cloud · Tokyo' },
  { label: '最近备份',     value: '04:00:01 UTC (今天)' },
  { label: '仓库',         value: 'github.com/pretaxt/Dracula-System' },
]

export default function SettingsPage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 占位提示: 通知矩阵 + API key 管理 */}
      <CardElevated style={{ padding: 20 }}>
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
          padding: '12px 16px',
          borderRadius: 'var(--radius-sm)',
          background: 'rgba(95, 176, 255, 0.06)',
          border: '1px solid rgba(95, 176, 255, 0.2)',
        }}>
          <Info size={16} style={{ color: 'var(--accent-azure)', marginTop: 2, flexShrink: 0 }} />
          <div>
            <div style={{ fontSize: 13, color: 'var(--text-primary)', marginBottom: 4 }}>
              通知矩阵 / API Key 管理 · 即将上线
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-tertiary)' }}>
              Phase 1+ · 多用户阶段同步开放
            </div>
          </div>
        </div>
      </CardElevated>

      {/* 关于系统 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="关于系统"
          subtitle="ABOUT"
          right={<Badge tone="active">v1.0.0</Badge>}
        />
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '12px 32px',
          fontSize: 12,
        }}>
          {ABOUT.map((row) => (
            <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border-subtle)', paddingBottom: 10 }}>
              <span style={{ color: 'var(--text-secondary)' }}>{row.label}</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{row.value}</span>
            </div>
          ))}
        </div>
      </CardElevated>
    </div>
  )
}
