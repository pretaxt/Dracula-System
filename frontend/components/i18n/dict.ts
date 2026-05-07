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
  'SYSTEM STATUS': 'SYSTEM STATUS',
  'RUNNING': 'RUNNING',
  'STOPPED': 'STOPPED',

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
  '网格策略': 'Grid Strategy',
  '做市策略': 'Market Making',
  '趋势跟踪': 'Trend Following',
  '稳定币利率套利': 'Stablecoin Yield',

  // ===== 用户名 =====
  '老虎': 'Tiger',
  '老': 'T',
  'SUPER ADMIN': 'SUPER ADMIN',

  // ===== 顶栏 =====
  'ARBITRAGE SYSTEM': 'ARBITRAGE SYSTEM',
}

export type Lang = 'zh' | 'en'

export function translate(text: string, lang: Lang): string {
  if (lang === 'zh') return text
  return dict[text] ?? text
}
