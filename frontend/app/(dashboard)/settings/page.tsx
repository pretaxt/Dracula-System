'use client'
import { useState } from 'react'
import { Key, CheckCircle2, AlertCircle, Edit3, X } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, Button } from '@/components/ui/Button'
import { getHealth, formatUptime } from '@/lib/api/health'
import { getStrategyStatus } from '@/lib/api/strategies'
import {
  getExchangeCredentials,
  updateExchangeCredentials,
  type ExchangeCredential,
  type ExchangeCredentialPatch,
} from '@/lib/api/system'

const EXCHANGE_LABEL: Record<string, string> = {
  binance: 'Binance',
  okx: 'OKX',
}

export default function SettingsPage() {
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 60_000 })
  const { data: stratStatus } = useQuery({ queryKey: ['strategy-status'], queryFn: getStrategyStatus, refetchInterval: 30_000 })
  const { data: credsData } = useQuery({
    queryKey: ['exchange-credentials'],
    queryFn: getExchangeCredentials,
    refetchInterval: 60_000,
  })

  const tradingMode = (stratStatus?.trading_mode ?? 'paper').toLowerCase()
  const isLive = tradingMode === 'live'

  const ABOUT: { label: string; value: string; tone?: 'live' | 'normal' }[] = [
    { label: '系统版本',     value: health?.version ? `v${health.version}` : '—' },
    { label: '交易模式',     value: isLive ? 'LIVE (实盘)' : 'PAPER (模拟)', tone: isLive ? 'live' : 'normal' },
    { label: '已运行时间',   value: health ? formatUptime(health.uptime_seconds ?? 0) : '—' },
    { label: '数据库',       value: 'PostgreSQL 16 + TimescaleDB' },
    { label: '事件总线',     value: 'Redis 7.2' },
    { label: '部署位置',     value: 'Tencent Cloud · Tokyo' },
    { label: '主密钥版本',   value: '—' },
    { label: '最近备份',     value: '—' },
    { label: '仓库',         value: 'github.com/pretaxt/Dracula-System' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 交易所 API 凭据 */}
      <ExchangeCredentialsSection credentials={credsData?.data ?? []} />

      {/* 关于系统 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="关于系统"
          subtitle="ABOUT"
          right={<Badge tone="active">{health?.version ? `v${health.version}` : '—'}</Badge>}
        />
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '12px 32px',
          fontSize: 14,
        }}>
          {ABOUT.map((row) => (
            <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border-subtle)', paddingBottom: 10 }}>
              <span style={{ color: 'var(--text-secondary)' }}>{row.label}</span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                color: row.tone === 'live' ? 'var(--accent-blood)' : 'var(--text-primary)',
                fontWeight: row.tone === 'live' ? 700 : 400,
              }}>{row.value}</span>
            </div>
          ))}
        </div>
      </CardElevated>
    </div>
  )
}


// ---------------------------------------------------------------------------
// 交易所 API 凭据管理
// ---------------------------------------------------------------------------

function ExchangeCredentialsSection({ credentials }: { credentials: ExchangeCredential[] }) {
  const [editing, setEditing] = useState<string | null>(null)
  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: ({ exchange, patch }: { exchange: string; patch: ExchangeCredentialPatch }) =>
      updateExchangeCredentials(exchange, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['exchange-credentials'] })
      setEditing(null)
    },
  })

  // 兜底：如果 API 还没返回数据，至少显示 binance + okx 两张未配置卡
  const display: ExchangeCredential[] = credentials.length
    ? credentials
    : (['binance', 'okx'] as const).map((ex) => ({
        exchange: ex,
        configured: false,
        api_key_preview: '',
        has_passphrase: false,
        updated_at: null,
      }))

  return (
    <CardElevated style={{ padding: 20 }} className="animate-in">
      <SectionHeader
        title="交易所 API 凭据"
        subtitle="EXCHANGE API CREDENTIALS"
        right={
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
            修改后需重启容器生效
          </span>
        }
      />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
        {display.map((c) => (
          <div
            key={c.exchange}
            style={{
              padding: 16,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)',
              background: 'var(--bg-card)',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{
                fontFamily: 'var(--font-display)',
                fontSize: 18,
                fontWeight: 600,
                letterSpacing: '0.04em',
                color: 'var(--text-primary)',
              }}>
                {EXCHANGE_LABEL[c.exchange] ?? c.exchange.toUpperCase()}
              </span>
              {c.configured
                ? <Badge tone="active"><CheckCircle2 size={10} style={{marginRight:4}}/>已配置</Badge>
                : <Badge tone="paused"><AlertCircle size={10} style={{marginRight:4}}/>未配置</Badge>}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Key size={11} />
              <span>{c.api_key_preview || '(未填写)'}</span>
            </div>
            {c.has_passphrase && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                Passphrase: ●●●●●
              </div>
            )}
            {c.updated_at && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
                更新于 {new Date(c.updated_at).toLocaleString()}
              </div>
            )}
            <Button
              variant="secondary"
              onClick={() => setEditing(c.exchange)}
              style={{ marginTop: 4, fontSize: 13 }}
            >
              <Edit3 size={11} style={{marginRight:6}}/>
              {c.configured ? '更新凭据' : '配置凭据'}
            </Button>
          </div>
        ))}
      </div>

      {editing && (
        <CredentialEditModal
          exchange={editing}
          onClose={() => setEditing(null)}
          onSave={(patch) => mutation.mutate({ exchange: editing, patch })}
          isPending={mutation.isPending}
          error={mutation.error ? String(mutation.error) : null}
        />
      )}
    </CardElevated>
  )
}


function CredentialEditModal({
  exchange, onClose, onSave, isPending, error,
}: {
  exchange: string
  onClose: () => void
  onSave: (patch: ExchangeCredentialPatch) => void
  isPending: boolean
  error: string | null
}) {
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const requiresPassphrase = exchange === 'okx'

  const canSubmit = apiKey.trim() && apiSecret.trim() && (!requiresPassphrase || passphrase.trim())

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 480, maxWidth: '90vw',
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-strong)',
          borderRadius: 'var(--radius-md)',
          padding: 24,
          display: 'flex', flexDirection: 'column', gap: 16,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{
            margin: 0, fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-primary)',
          }}>
            {EXCHANGE_LABEL[exchange] ?? exchange} API
          </h3>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        <Field label="API Key" value={apiKey} onChange={setApiKey} placeholder="例: P2Xn4ybYQLzAr..." />
        <Field label="API Secret" value={apiSecret} onChange={setApiSecret} placeholder="对应的 secret（提交后不再显示）" type="password" />
        {requiresPassphrase && (
          <Field label="Passphrase" value={passphrase} onChange={setPassphrase} placeholder="OKX 创建 API 时设置的口令" type="password" />
        )}

        <div style={{ fontSize: 13, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
          ⚠️ 凭据会以 <code>chmod 600</code> 写入服务器 <code>/opt/dracula/state/</code>。<br/>
          ✅ API key 应勾选 Spot+Perp Trading 与 Universal Transfer 权限，绑定服务器 IP 白名单。<br/>
          🔄 保存后<strong>需重启 api 容器</strong>才能让新凭据初始化适配器。
        </div>

        {error && (
          <div style={{ padding: 8, background: 'rgba(227,64,88,0.10)', color: 'var(--accent-blood)', fontSize: 13, borderRadius: 'var(--radius-sm)' }}>
            ❌ {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <Button
            variant="primary"
            disabled={!canSubmit || isPending}
            onClick={() => onSave({
              api_key: apiKey.trim(),
              api_secret: apiSecret.trim(),
              ...(requiresPassphrase ? { passphrase: passphrase.trim() } : {}),
            })}
            style={{ flex: 1 }}
          >
            {isPending ? '保存中…' : '保存'}
          </Button>
          <Button variant="secondary" onClick={onClose} style={{ flex: 1 }}>
            取消
          </Button>
        </div>
      </div>
    </div>
  )
}


function Field({
  label, value, onChange, placeholder, type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  type?: 'text' | 'password'
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-tertiary)' }}>
        {label}
      </span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        style={{
          padding: '8px 10px',
          background: 'var(--bg-deepest)',
          border: '1px solid var(--border-default)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-mono)',
          fontSize: 14,
          outline: 'none',
        }}
      />
    </label>
  )
}
