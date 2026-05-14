/**
 * Dracula System — 中英文字典
 * 字典 key = 中文原文; value = 英文翻译。
 * t('中文') 在 lang='en' 时返回 dict['中文'], 否则原样返回。
 *
 * 增量维护: 各视图开发时按需追加, 缺失 key 自动 fallback 到原文。
 */
export const dict: Record<string, string> = {
  // ===== 通用 =====
  '全部': 'All',
  '查看详情': 'View Details',
  '配置': 'Configure',
  '删除': 'Delete',
  '复制': 'Copy',
  '撤销': 'Revoke',
  '添加': 'Add',
  '暂停': 'Pause',
  '解除': 'Resume',
  '保存': 'Save',
  '取消': 'Cancel',
  '确认': 'Confirm',
  '今天': 'Today',
  '加载中…': 'Loading…',

  // ===== 侧边栏 =====
  '总览': 'Overview',
  '策略中心': 'Strategies',
  '持仓与订单': 'Positions',
  '风控中心': 'Risk',
  '用户管理': 'Users',
  '设置': 'Settings',
  '系统状态': 'SYSTEM STATUS',
  '运行中': 'RUNNING',
  '已停止': 'STOPPED',
  '待启动': 'PLANNED',
  '监控只读': 'MONITOR',
  '已停用': 'DISABLED',
  '回撤中': 'UNDERWATER',
  '模拟': 'PAPER',

  // ===== 总览 KPI =====
  '总资本': 'Total Capital',
  '今日 PnL': 'Today PnL',
  '月度 PnL': 'Monthly PnL',
  '单日回撤': 'Daily DD',
  'USDT 等值': 'USDT equiv.',
  '距 Tier 3 红线': 'To Tier 3 Limit',

  // ===== 总览区块 =====
  '权益曲线': 'Equity Curve',
  '起始': 'Start',
  '最高': 'High',
  '最低': 'Low',
  '策略表现': 'Strategy Performance',
  '查看全部 12 个策略 →': 'View All 12 Strategies →',
  '风控状态': 'Risk Status',
  '周回撤': 'Weekly DD',
  '最低保证金率': 'Min Margin Rate',
  'API 错误率 (5m)': 'API Error (5m)',
  'WS 连接稳定性': 'WS Stability',
  '三层风控全部正常': 'All 3-tier risk normal',
  '实时套利机会': 'Live Opportunities',
  '实时': 'Live',
  '已建仓': 'Open',
  '监控中': 'Monitor',
  '手动': 'Manual',
  '系统活动': 'Activity Feed',

  // ===== 表头通用 =====
  '策略': 'Strategy',
  '币对': 'Pair',
  '交易所': 'Exchange',
  '指标': 'Metric',
  '规模': 'Size',
  '方向': 'Side',
  '入场价': 'Entry',
  '现价': 'Mark',
  '浮动盈亏': 'Unrealized',
  '持仓': 'Holding',
  '状态': 'Status',
  '时间': 'Time',
  '类型': 'Type',
  '数量': 'Amount',
  '成交均价': 'Avg Price',
  '操作': 'Actions',

  // ===== 策略名 =====
  '资金费率套利': 'Funding Rate Arb',
  '期现套利': 'Spot-Perp Arb',
  '三角套利': 'Triangular Arb',
  '跨所基差套利': 'Perp Basis Arb',
  '跨所价差套利': 'Spot Spread Arb',
  '配对交易': 'Pairs Trading',
  'CEX-DEX 套利': 'CEX-DEX Arb',
  'CEX-DEX 监控': 'CEX-DEX Monitor',
  '期权波动率套利': 'Options Vol Arb',
  '基差套利': 'Basis Arb',
  '资金费率': 'Funding',
  '跨所基差': 'Cross-Ex Basis',
  '网格策略': 'Grid Strategy',
  '做市策略': 'Market Making',
  '趋势跟踪': 'Trend Following',
  '稳定币利率套利': 'Stablecoin Yield',

  // ===== 用户名 =====
  '老虎': 'Tiger',
  '老': 'T',
  'SUPER ADMIN': 'SUPER ADMIN',

  // ===== 顶栏 =====
  'ARBITRAGE SYSTEM': '套利系统',

  // ===== 策略详情通用 =====
  '入场条件': 'ENTRY',
  '出场条件': 'EXIT',
  '参数': 'PARAMETERS',
  '任一触发': 'any triggers',

  // ===== #04 期现套利 entry / exit =====
  '基差绝对值 ≥ 0.25%(实盘入场阈值,留出手续费 + 资金费缓冲)':
    '|basis| ≥ 0.25% (live entry threshold, fees + funding buffer)',
  '基差绝对值 ≥ 0.10%(候选展示用)': '|basis| ≥ 0.10% (candidate display)',
  '基差绝对值 ≥ 0.25%(实盘开仓)': '|basis| ≥ 0.25% (live open)',
  '方向为升水(永续 > 现货)或贴水(永续 < 现货),双向均已实盘':
    'Direction = premium (perp > spot) or discount (perp < spot); both live',
  '升水 + 贴水双向(贴水自动借币卖空)':
    'Premium + discount (discount auto-borrows for spot short)',
  'PnL 计算': 'PnL Computation',
  '真实成交价 + 资金费 + 借币利息(实时显示)':
    'Real fill prices + funding + borrow interest (real-time)',
  '扫描候选门槛 |basis| ≥ 0.10%（仅展示），实盘入场阈值 |basis| ≥ 0.25%（升水/贴水双向自动开平仓） · 60 秒扫描':
    'Scan candidate threshold |basis| ≥ 0.10% (display only); live entry |basis| ≥ 0.25% (auto open/close, premium + discount) · 60s scan',

  // ===== 订单 side / event 文案 =====
  '开仓': 'Open',
  '平仓': 'Close',
  'MARKET': 'Market',
  '止损触发': 'Stop Loss',
  '基差止损': 'Basis Stop',
  '基差扩大触发止损': 'Basis Widening Stop',
  '资金费率反转': 'Funding Rate Reversal',
  '持仓超时平仓': 'Max Hold Close',
  '基差收敛平仓': 'Basis Convergence Close',
  '强平': 'Force Close',
  '手动平仓': 'Manual Close',
  '实时基差机会': 'Live Basis Opportunities',
  '扫描中': 'Scanning',
  '未启动': 'Not Started',
  '上次扫描': 'Last scan',
  '方向为升水(永续价 > 现货价；贴水方向需要现货保证金,二期开放)':
    'Direction = premium (perp > spot; discount requires spot margin, opens in Phase 2)',
  '同时持仓数小于 3 笔': 'Concurrent positions < 3',
  '同一标的不可重复开仓': 'No duplicate symbol entry',
  '候选币种在 30 标的列表内': 'Symbol within the 30-token whitelist',
  '当前基差绝对值 ≤ 0.03%(基差收敛,主要盈利路径)':
    'Current |basis| ≤ 0.03% (convergence, primary PnL path)',
  '持仓时长达到 12 小时(超时强平)': 'Holding ≥ 12h (timeout force-close)',
  '永续单腿被交易所强平 → 强平监控自动平掉现货腿兜底':
    'Perp leg liquidated → LiquidationWatcher auto-unwinds spot leg',

  // ===== #04 参数 label =====
  '候选币种': 'Candidate symbols',
  '同时持仓上限': 'Max concurrent positions',
  '基差扩大止损': 'Basis Widening Stop',
  '实时跨所 funding 差': 'Live Cross-Exchange Funding Diff',
  'long 端': 'Long side',
  'short 端': 'Short side',
  'long APR': 'Long APR',
  'short APR': 'Short APR',
  '差 APR': 'Diff APR',
  'long 周期': 'Long interval',
  'short 周期': 'Short interval',
  '当前无 funding 差超过门槛的标的': 'No symbols with funding diff above threshold',
  '入场门槛 funding diff APR ≥': 'Entry threshold funding diff APR ≥',
  '个交易所组合 · 30 秒扫描 · 数据来自 MarketDataHub': 'exchange pairs · 30s scan · MarketDataHub backed',
  '峰值滑窗': 'Peak window',
  '入场回落要求': 'Entry dropoff',
  '扫描候选门槛': 'Scan threshold',
  '实盘入场阈值': 'Live entry threshold',
  '仅展示': 'display only',
  '升水/贴水双向自动开平仓': 'premium/discount both auto open/close',
  '秒扫描': 's scan',
  'premium 阈值': 'Premium threshold',
  'discount 阈值': 'Discount threshold',
  '回退': 'Fallback',
  '禁用': 'Disabled',

  // ===== #01 实时费率机会表 =====
  '实时费率机会': 'Live Funding-Rate Opportunities',
  '当前 APR': 'Current APR',
  '费率/期': 'Rate / period',
  '距入场': 'To entry',
  '历史正费率': 'History positive',
  '已达': 'Reached',
  '可开仓': 'Open-able',
  '等窗口': 'Awaiting window',
  '接近': 'Near',
  '候选': 'Candidate',
  '当前无符合候选门槛的标的': 'No candidates above scan threshold',
  '扫描候选门槛 APR ≥': 'Scan threshold APR ≥',
  '候选展示门槛': 'Candidate threshold',
  '候选展示门槛 — APR ≥ 该值进 UI 候选表（仅展示，不实盘开仓；0 = 回退用扫描最低 APR）':
    'Candidate threshold — symbols with APR ≥ this value enter UI candidate list (display only, no live entry; 0 = fallback to scan min APR)',
  '（仅展示），实盘入场阈值 APR ≥': '(display only); live entry APR ≥',
  '（结算前 15 分钟内自动开仓） · 60 秒扫描': '(auto-open in 15min pre-funding window) · 60s scan',
  '单笔名义规模': 'Per-position notional',
  '永续杠杆': 'Perp leverage',
  '单笔保证金': 'Per-position margin',
  '单笔总占用': 'Total capital per trade',
  '入场阈值': 'Entry threshold',
  '收敛平仓阈值': 'Convergence exit threshold',
  '最大持仓时长': 'Max holding duration',
  '总名义敞口上限': 'Max total notional exposure',
  '扫描间隔': 'Scan interval',
  '允许方向': 'Allowed direction',

  // ===== #04 参数 value =====
  '币安 + 欧易 各 30 主流 USDT 永续合约':
    'Binance + OKX, 30 major USDT perps each',
  '3 笔': '3',
  '50 美元(一期实盘起步)': '$50 (Phase 1 live launch)',
  '3 倍(逐仓)': '3x (isolated)',
  '约 17 美元(=50/3)': '~$17 (=$50 / 3)',
  '67 美元(现货 50 + 保证金 17)':
    '$67 ($50 spot + $17 margin)',
  '基差绝对值 ≤ 0.03%': '|basis| ≤ 0.03%',
  '扫描门槛': 'Scan threshold',
  '12 小时': '12 hours',
  '150 美元(3 × 50)': '$150 (3 × $50)',
  '60 秒': '60 seconds',
  '仅升水方向(一期；二期开放贴水)':
    'Premium only (Phase 1; discount opens in Phase 2)',
}

export type Lang = 'zh' | 'en'

export function translate(text: string, lang: Lang): string {
  if (lang === 'zh') return text
  return dict[text] ?? text
}
