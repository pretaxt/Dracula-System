'use client'
import { useState } from 'react'
import { Key, CheckCircle2, AlertCircle, Edit3, X, Bell, Mail } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge, Button } from '@/components/ui/Button'
import { getHealth, formatUptime } from '@/lib/api/health'
import { getStrategyStatus } from '@/lib/api/strategies'
import {
  getExchangeCredentials,
  updateExchangeCredentials,
  getWeb3Credentials,
  updateWeb3Credentials,
  getNotificationConfig,
  updateNotificationConfig,
  testNotification,
  type ExchangeCredential,
  type ExchangeCredentialPatch,
  type Web3CredentialsMeta,
  type Web3CredentialsPatch,
  type NotificationConfig,
  type NotificationConfigPatch,
} from '@/lib/api/system'

const EXCHANGE_LABEL: Record<string, string> = {
  binance: 'Binance',
  okx: 'OKX',
  bitget: 'Bitget',
  bybit: 'Bybit',
  htx: 'HTX',
}

const EXCHANGES_REQUIRING_PASSPHRASE: Set<string> = new Set(['okx', 'bitget'])

const ALL_EXCHANGES = ['binance', 'okx', 'bitget', 'bybit', 'htx'] as const

export default function SettingsPage() {
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 60_000 })
  const { data: stratStatus } = useQuery({ queryKey: ['strategy-status'], queryFn: getStrategyStatus, refetchInterval: 30_000 })
  const { data: credsData } = useQuery({
    queryKey: ['exchange-credentials'],
    queryFn: getExchangeCredentials,
    refetchInterval: 60_000,
  })
  const { data: web3Data } = useQuery({
    queryKey: ['web3-credentials'],
    queryFn: getWeb3Credentials,
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

      {/* Web3 凭据 */}
      <Web3CredentialsSection meta={web3Data ?? null} />

      {/* 推送通知配置 */}
      <NotificationConfigSection />

      {/* 关于系统 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="关于系统"
          subtitle="ABOUT"
          right={<Badge tone="active">{health?.version ? `v${health.version}` : '—'}</Badge>}
        />
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
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

  // 兜底：如果 API 还没返回数据，至少显示全部 5 家未配置卡
  const display: ExchangeCredential[] = credentials.length
    ? credentials
    : ALL_EXCHANGES.map((ex) => ({
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
  const requiresPassphrase = EXCHANGES_REQUIRING_PASSPHRASE.has(exchange)

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
          <Field
            label="Passphrase"
            value={passphrase}
            onChange={setPassphrase}
            placeholder={`${EXCHANGE_LABEL[exchange] ?? exchange} 创建 API 时设置的口令`}
            type="password"
          />
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


// ---------------------------------------------------------------------------
// Web3 凭据管理 (CEX-DEX 套利)
// ---------------------------------------------------------------------------

function Web3CredentialsSection({ meta }: { meta: Web3CredentialsMeta | null }) {
  const [showModal, setShowModal] = useState(false)
  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: (patch: Web3CredentialsPatch) => updateWeb3Credentials(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['web3-credentials'] })
      setShowModal(false)
    },
  })

  const configured = meta?.configured ?? false

  return (
    <CardElevated style={{ padding: 20 }} className="animate-in">
      <SectionHeader
        title="Web3 凭据 (CEX-DEX 套利)"
        subtitle="ARBITRUM ONE"
        right={
          configured
            ? <Badge tone="active"><CheckCircle2 size={10} style={{marginRight:4}}/>已配置</Badge>
            : <Badge tone="paused"><AlertCircle size={10} style={{marginRight:4}}/>未配置</Badge>
        }
      />

      <div style={{
        padding: 16,
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border-default)',
        background: 'var(--bg-card)',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
          <span style={{ color: 'var(--text-secondary)' }}>钱包地址</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: configured ? 'var(--text-primary)' : 'var(--text-muted)' }}>
            {meta?.wallet_address
              ? `${meta.wallet_address.slice(0,8)}...${meta.wallet_address.slice(-6)}`
              : '(未配置)'}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
          <span style={{ color: 'var(--text-secondary)' }}>Arbitrum RPC</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: configured ? 'var(--text-primary)' : 'var(--text-muted)' }}>
            {meta?.rpc_url_preview || '(未配置)'}
          </span>
        </div>
        {meta?.updated_at && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)' }}>
            更新于 {new Date(meta.updated_at).toLocaleString()}
          </div>
        )}
        <Button
          variant="secondary"
          onClick={() => setShowModal(true)}
          style={{ marginTop: 4, fontSize: 13, alignSelf: 'flex-start' }}
        >
          <Edit3 size={11} style={{marginRight:6}}/>
          {configured ? '更新凭据' : '配置凭据'}
        </Button>
      </div>

      {showModal && (
        <Web3CredentialModal
          onClose={() => setShowModal(false)}
          onSave={(patch) => mutation.mutate(patch)}
          isPending={mutation.isPending}
          error={mutation.error ? String(mutation.error) : null}
        />
      )}
    </CardElevated>
  )
}


function Web3CredentialModal({
  onClose, onSave, isPending, error,
}: {
  onClose: () => void
  onSave: (patch: Web3CredentialsPatch) => void
  isPending: boolean
  error: string | null
}) {
  const [privateKey, setPrivateKey] = useState('')
  const [rpcUrl, setRpcUrl] = useState('')

  const canSubmit = privateKey.trim().length >= 64 && rpcUrl.trim().startsWith('https://')

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
          width: 520, maxWidth: '90vw',
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-strong)',
          borderRadius: 'var(--radius-md)',
          padding: 24,
          display: 'flex', flexDirection: 'column', gap: 16,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-primary)' }}>
            Web3 凭据
          </h3>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        <Field
          label="钱包私钥 (0x...)"
          value={privateKey}
          onChange={setPrivateKey}
          placeholder="0x你的热钱包私钥"
          type="password"
        />
        <Field
          label="Arbitrum RPC URL"
          value={rpcUrl}
          onChange={setRpcUrl}
          placeholder="https://arb-mainnet.g.alchemy.com/v2/..."
        />

        <div style={{ fontSize: 13, color: 'var(--text-tertiary)', lineHeight: 1.7 }}>
          ⚠️ 私钥以 <code>chmod 600</code> 写入 <code>/app/state/web3_credentials.json</code>。<br/>
          🔒 热钱包仅存放 CEX-DEX 套利所需资金，不要充入大额资产。<br/>
          🔄 保存后<strong>需重启 api 容器</strong>以使用新凭据。
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
            onClick={() => onSave({ private_key: privateKey.trim(), rpc_url: rpcUrl.trim() })}
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


// ---------------------------------------------------------------------------
// 推送通知配置
// ---------------------------------------------------------------------------

/** CSS-only pill toggle，无外部依赖 */
function Toggle({ enabled, onChange, disabled }: { enabled: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      onClick={() => !disabled && onChange(!enabled)}
      aria-checked={enabled}
      role="switch"
      style={{
        width: 42,
        height: 22,
        borderRadius: 11,
        border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        padding: 0,
        background: enabled ? 'var(--accent-blood)' : 'var(--border-strong)',
        position: 'relative',
        flexShrink: 0,
        transition: 'background 200ms',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 2,
          left: enabled ? 22 : 2,
          width: 18,
          height: 18,
          borderRadius: '50%',
          background: 'var(--text-primary)',
          transition: 'left 200ms',
        }}
      />
    </button>
  )
}

function NotificationConfigSection() {
  const qc = useQueryClient()
  const { data: cfg, isLoading } = useQuery({
    queryKey: ['notification-config'],
    queryFn: getNotificationConfig,
    refetchInterval: 60_000,
    retry: false,
  })

  const mutation = useMutation({
    mutationFn: (patch: NotificationConfigPatch) => updateNotificationConfig(patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['notification-config'] })
    },
  })

  const [showTgModal, setShowTgModal] = useState(false)
  const [showEmailModal, setShowEmailModal] = useState(false)
  const [tgTestStatus, setTgTestStatus] = useState<{ ok: boolean; msg: string } | null>(null)
  const [emailTestStatus, setEmailTestStatus] = useState<{ ok: boolean; msg: string } | null>(null)

  async function handleTest(channel: 'telegram' | 'email') {
    const setter = channel === 'telegram' ? setTgTestStatus : setEmailTestStatus
    try {
      await testNotification(channel)
      setter({ ok: true, msg: '✓ 已发送' })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setter({ ok: false, msg: `✗ ${msg}` })
    }
    setTimeout(() => setter(null), 3000)
  }

  // 乐观更新开关
  function handleToggle(field: 'telegram_enabled' | 'email_enabled', current: boolean) {
    mutation.mutate({ [field]: !current })
  }

  const tgEnabled = cfg?.telegram_enabled ?? false
  const emailEnabled = cfg?.email_enabled ?? false
  const isPending = mutation.isPending

  return (
    <CardElevated style={{ padding: 20 }} className="animate-in">
      <SectionHeader
        title="推送通知"
        subtitle="NOTIFICATION CHANNELS"
        right={
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Bell size={12} />
            {isLoading ? '加载中…' : cfg ? '已加载' : '后端未就绪'}
          </span>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* ── Telegram 区块 ── */}
        <div style={{
          padding: 16,
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-default)',
          background: 'var(--bg-card)',
        }}>
          {/* 标题行 + 开关 */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontFamily: 'var(--font-display)', fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
              Telegram
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: tgEnabled ? 'var(--accent-emerald)' : 'var(--text-muted)' }}>
                {tgEnabled ? '已开启' : '已关闭'}
              </span>
              <Toggle enabled={tgEnabled} onChange={() => handleToggle('telegram_enabled', tgEnabled)} disabled={isPending || !cfg} />
            </div>
          </div>
          <div style={{ height: 1, background: 'var(--border-subtle)', marginBottom: 12 }} />

          {/* 字段展示 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 90 }}>Bot Token</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.telegram_bot_token_preview ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                  {cfg?.telegram_bot_token_preview || '(未填写)'}
                </span>
                <button
                  onClick={() => setShowTgModal(true)}
                  style={{
                    padding: '2px 8px', fontSize: 12, fontFamily: 'var(--font-mono)',
                    background: 'transparent', border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
                    cursor: 'pointer',
                  }}
                >
                  修改
                </button>
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 90 }}>Chat ID</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.telegram_chat_id ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {cfg?.telegram_chat_id || '(未填写)'}
              </span>
            </div>
          </div>

          {/* 测试按钮 + 状态 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Button
              variant="secondary"
              onClick={() => handleTest('telegram')}
              disabled={!tgEnabled || !cfg?.telegram_bot_token_preview}
              style={{ fontSize: 13 }}
            >
              发送测试消息
            </Button>
            {tgTestStatus && (
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 13,
                color: tgTestStatus.ok ? 'var(--accent-emerald)' : 'var(--accent-blood)',
              }}>
                {tgTestStatus.msg}
              </span>
            )}
          </div>
        </div>

        {/* ── 邮件 SMTP 区块 ── */}
        <div style={{
          padding: 16,
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-default)',
          background: 'var(--bg-card)',
        }}>
          {/* 标题行 + 开关 */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontFamily: 'var(--font-display)', fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Mail size={14} />
              邮件 (SMTP)
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: emailEnabled ? 'var(--accent-emerald)' : 'var(--text-muted)' }}>
                {emailEnabled ? '已开启' : '已关闭'}
              </span>
              <Toggle enabled={emailEnabled} onChange={() => handleToggle('email_enabled', emailEnabled)} disabled={isPending || !cfg} />
            </div>
          </div>
          <div style={{ height: 1, background: 'var(--border-subtle)', marginBottom: 12 }} />

          {/* 字段展示 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 100 }}>SMTP 主机</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.smtp_host ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {cfg?.smtp_host || '(未填写)'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 100 }}>SMTP 端口</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.smtp_port ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {cfg?.smtp_port || '(未填写)'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 100 }}>发件账号</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.smtp_user ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {cfg?.smtp_user || '(未填写)'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 100 }}>密码</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.smtp_password_set ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                  {cfg?.smtp_password_set ? '●●●●● (已设置)' : '(未设置)'}
                </span>
                <button
                  onClick={() => setShowEmailModal(true)}
                  style={{
                    padding: '2px 8px', fontSize: 12, fontFamily: 'var(--font-mono)',
                    background: 'transparent', border: '1px solid var(--border-default)',
                    borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
                    cursor: 'pointer',
                  }}
                >
                  修改
                </button>
              </div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 100 }}>发件人邮箱</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.smtp_from_email ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {cfg?.smtp_from_email || '(同发件账号)'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
              <span style={{ color: 'var(--text-secondary)', minWidth: 100 }}>收件人邮箱</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: cfg?.smtp_to_email ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {cfg?.smtp_to_email || '(未填写)'}
              </span>
            </div>
          </div>

          {/* 测试按钮 + 状态 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Button
              variant="secondary"
              onClick={() => handleTest('email')}
              disabled={!emailEnabled || !cfg?.smtp_host}
              style={{ fontSize: 13 }}
            >
              发送测试邮件
            </Button>
            {emailTestStatus && (
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 13,
                color: emailTestStatus.ok ? 'var(--accent-emerald)' : 'var(--accent-blood)',
              }}>
                {emailTestStatus.msg}
              </span>
            )}
          </div>
        </div>

      </div>

      {/* Telegram 修改 Modal */}
      {showTgModal && (
        <TelegramConfigModal
          current={cfg}
          onClose={() => setShowTgModal(false)}
          onSave={(patch) => {
            mutation.mutate(patch, { onSuccess: () => setShowTgModal(false) })
          }}
          isPending={isPending}
          error={mutation.error ? String(mutation.error) : null}
        />
      )}

      {/* 邮件修改 Modal */}
      {showEmailModal && (
        <EmailConfigModal
          current={cfg}
          onClose={() => setShowEmailModal(false)}
          onSave={(patch) => {
            mutation.mutate(patch, { onSuccess: () => setShowEmailModal(false) })
          }}
          isPending={isPending}
          error={mutation.error ? String(mutation.error) : null}
        />
      )}
    </CardElevated>
  )
}


function TelegramConfigModal({
  current, onClose, onSave, isPending, error,
}: {
  current: NotificationConfig | undefined
  onClose: () => void
  onSave: (patch: NotificationConfigPatch) => void
  isPending: boolean
  error: string | null
}) {
  const [botToken, setBotToken] = useState('')
  const [chatId, setChatId] = useState(current?.telegram_chat_id ?? '')

  const canSubmit = botToken.trim() || chatId.trim()

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
          <h3 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-primary)' }}>
            Telegram 配置
          </h3>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        <Field
          label="Bot Token"
          value={botToken}
          onChange={setBotToken}
          placeholder="从 @BotFather 获取的 token（留空则不修改）"
          type="password"
        />
        <Field
          label="Chat ID"
          value={chatId}
          onChange={setChatId}
          placeholder="-1001234567890"
        />

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
              ...(botToken.trim() ? { telegram_bot_token: botToken.trim() } : {}),
              ...(chatId.trim() ? { telegram_chat_id: chatId.trim() } : {}),
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


function EmailConfigModal({
  current, onClose, onSave, isPending, error,
}: {
  current: NotificationConfig | undefined
  onClose: () => void
  onSave: (patch: NotificationConfigPatch) => void
  isPending: boolean
  error: string | null
}) {
  const [smtpHost, setSmtpHost] = useState(current?.smtp_host ?? '')
  const [smtpPort, setSmtpPort] = useState(String(current?.smtp_port ?? '587'))
  const [smtpUser, setSmtpUser] = useState(current?.smtp_user ?? '')
  const [smtpPassword, setSmtpPassword] = useState('')
  const [smtpFromEmail, setSmtpFromEmail] = useState(current?.smtp_from_email ?? '')
  const [smtpToEmail, setSmtpToEmail] = useState(current?.smtp_to_email ?? '')

  const canSubmit = smtpHost.trim() && smtpUser.trim() && smtpToEmail.trim()

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
          width: 520, maxWidth: '90vw',
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-strong)',
          borderRadius: 'var(--radius-md)',
          padding: 24,
          display: 'flex', flexDirection: 'column', gap: 16,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-primary)' }}>
            邮件 (SMTP) 配置
          </h3>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        <Field label="SMTP 主机" value={smtpHost} onChange={setSmtpHost} placeholder="smtp.gmail.com" />
        <Field label="SMTP 端口" value={smtpPort} onChange={setSmtpPort} placeholder="587" type="text" />
        <Field label="发件账号" value={smtpUser} onChange={setSmtpUser} placeholder="your@gmail.com" />
        <Field label="密码" value={smtpPassword} onChange={setSmtpPassword} placeholder="留空则不修改" type="password" />
        <Field label="发件人邮箱（可选，空=同发件账号）" value={smtpFromEmail} onChange={setSmtpFromEmail} placeholder="your@gmail.com" />
        <Field label="收件人邮箱" value={smtpToEmail} onChange={setSmtpToEmail} placeholder="alerts@you.com" />

        {error && (
          <div style={{ padding: 8, background: 'rgba(227,64,88,0.10)', color: 'var(--accent-blood)', fontSize: 13, borderRadius: 'var(--radius-sm)' }}>
            ❌ {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <Button
            variant="primary"
            disabled={!canSubmit || isPending}
            onClick={() => {
              const portNum = parseInt(smtpPort, 10)
              onSave({
                smtp_host: smtpHost.trim(),
                smtp_port: isNaN(portNum) ? 587 : portNum,
                smtp_user: smtpUser.trim(),
                ...(smtpPassword.trim() ? { smtp_password: smtpPassword.trim() } : {}),
                ...(smtpFromEmail.trim() ? { smtp_from_email: smtpFromEmail.trim() } : {}),
                smtp_to_email: smtpToEmail.trim(),
              })
            }}
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
