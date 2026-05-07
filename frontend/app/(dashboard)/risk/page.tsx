'use client'
import { useQuery } from '@tanstack/react-query'
import { Lock, CheckCircle2 } from 'lucide-react'
import { getRiskLimits } from '@/lib/api/risk'
import { CardElevated, SectionHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/Stats'
import { useT } from '@/components/i18n/I18nProvider'

type RiskLimits = {
  max_positions: number
  stop_loss_pct: string
  max_hold_hours: string
  min_apr_pct: string
  max_total_notional_usd: string
}

// 风控事件日志 mock — 后端无事件日志 API
const RISK_EVENTS: { time: string; tier: 'TIER 1' | 'TIER 2' | 'TIER 3'; event: string; trigger: string; value: string; valueColor?: 'positive' | 'negative'; action: string; actionColor?: string }[] = [
  { time: '04-28 22:14', tier: 'TIER 2', event: 'API 错误率告警', trigger: 'HTX 5m err rate',  value: '3.2% / 5%',     action: '自动恢复',   actionColor: 'var(--accent-emerald)' },
  { time: '04-21 09:42', tier: 'TIER 1', event: 'WebSocket 断连', trigger: 'Bybit WS',         value: '42s',            action: '自动重连',   actionColor: 'var(--accent-emerald)' },
  { time: '04-15 14:08', tier: 'TIER 2', event: '资金费率反转',   trigger: 'ARB/USDT funding', value: '-0.018%',        valueColor: 'negative', action: '已平仓',     actionColor: 'var(--accent-emerald)' },
  { time: '04-08 03:21', tier: 'TIER 1', event: '建仓滑点超阈值', trigger: 'Binance ETH/USDT', value: '0.34%',          action: '已撤单',     actionColor: 'var(--text-tertiary)' },
]

export default function RiskPage() {
  const { t } = useT()
  const { data, isLoading } = useQuery<RiskLimits>({ queryKey: ['risk'], queryFn: getRiskLimits })

  if (isLoading || !data) {
    return <div style={{ padding: 48, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{t('加载中…')}</div>
  }

  const minApr = parseFloat(data.min_apr_pct || '0')
  const maxPos = data.max_positions
  const maxNot = parseFloat(data.max_total_notional_usd || '0')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {/* 三层风控状态 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
        {/* Tier 1 */}
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader title={t('Tier 1 · 仪表盘可调')} right={<Badge tone="active">SAFE</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{t('扫描最低 APR')}</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>{minApr.toFixed(0)}%</span>
              </div>
              <ProgressBar pct={Math.min(100, (minApr / 25) * 100)} tone="success" />
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{t('默认仓位规模')}</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>$500</span>
              </div>
              <ProgressBar pct={50} tone="success" />
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{t('最大同时仓位')}</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>{maxPos}</span>
              </div>
              <ProgressBar pct={Math.min(100, (maxPos / 10) * 100)} tone="success" />
            </div>
          </div>
        </CardElevated>

        {/* Tier 2 */}
        <CardElevated style={{ padding: 20 }} className="animate-in">
          <SectionHeader title={t('Tier 2 · 延迟生效')} right={<Badge tone="active">SAFE</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>单交易所占比</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>38.4% / 50%</span>
              </div>
              <ProgressBar pct={77} tone="success" />
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>单币种占比</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>12.1% / 20%</span>
              </div>
              <ProgressBar pct={60} tone="success" />
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>最高名义敞口</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>${maxNot.toFixed(0)}</span>
              </div>
              <ProgressBar pct={50} tone="success" />
            </div>
          </div>
        </CardElevated>

        {/* Tier 3 — 锁定红线 */}
        <CardElevated style={{ padding: 20, borderColor: 'var(--accent-blood)' }} className="animate-in">
          <SectionHeader title={t('Tier 3 · 锁定红线')} right={<Badge tone="active">SAFE</Badge>} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>单日回撤红线</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>-3.0%</span>
              </div>
              <ProgressBar pct={11} tone="success" />
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 -0.32%</div>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>周回撤红线</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent-blood)' }}>-8.0%</span>
              </div>
              <ProgressBar pct={13} tone="success" />
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 -1.04%</div>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{t('最低保证金率')}</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>50%</span>
              </div>
              <ProgressBar pct={87} tone="success" />
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, marginTop: 4, color: 'var(--text-tertiary)' }}>当前 87.3%</div>
            </div>
          </div>
          <div style={{
            marginTop: 16,
            paddingTop: 12,
            borderTop: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            color: 'var(--text-tertiary)',
          }}>
            <Lock size={12} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>需修改 config.yaml 重启系统才能调整</span>
          </div>
        </CardElevated>
      </div>

      {/* 风控事件日志 */}
      <CardElevated style={{ padding: 20 }} className="animate-in">
        <SectionHeader
          title="风控事件日志"
          subtitle="RISK EVENT LOG · LAST 30 DAYS"
          right={
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-tertiary)' }}>
              <CheckCircle2 size={12} style={{ color: 'var(--accent-emerald)' }} />
              <span>{t('三层风控全部正常')}</span>
            </span>
          }
        />
        <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          <thead>
            <tr>
              {[t('时间'), '层级', '事件', '触发指标', '数值', '处理'].map((h, i) => (
                <th key={i} style={{
                  textAlign: i === 4 ? 'right' : 'left',
                  padding: '8px 12px',
                  color: 'var(--text-tertiary)',
                  fontSize: 10,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  borderBottom: '1px solid var(--border-default)',
                  background: 'var(--bg-deepest)',
                  fontWeight: 500,
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {RISK_EVENTS.map((e, i) => (
              <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{e.time}</td>
                <td style={{ padding: '10px 12px' }}><Badge tone="warn">{e.tier}</Badge></td>
                <td style={{ padding: '10px 12px', color: 'var(--text-primary)' }}>{e.event}</td>
                <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>{e.trigger}</td>
                <td style={{ padding: '10px 12px', textAlign: 'right', color: e.valueColor === 'negative' ? 'var(--accent-blood)' : 'var(--text-primary)' }}>{e.value}</td>
                <td style={{ padding: '10px 12px', fontSize: 10, color: e.actionColor }}>{e.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardElevated>
    </div>
  )
}
