/**
 * Dracula 12 策略目录(2026-05-07 决策版,砍掉 #8 #11 #12 #15 #17)
 * 列表页与详情页共享数据源
 */

export type StrategyPhase = 'P0' | 'P1' | 'P3'
export type StrategyStatus = 'RUNNING' | 'PLANNED' | 'MONITOR' | 'DISABLED' | 'UNDERWATER'

export type Strategy = {
  num: string
  id: string
  zhName: string
  enLabel: string
  phase: StrategyPhase
  status: StrategyStatus
  capital: string
  monthly: string | null
  positions: string
  posLabel: string
  desc: string
  monthlyTone?: 'positive' | 'negative'
  thesis?: string
  risks?: string[]
}

export const STRATEGIES: Strategy[] = [
  {
    num: '01', id: 'funding-rate', zhName: '资金费率套利',
    enLabel: 'FUNDING RATE ARBITRAGE · 主力 P0', phase: 'P0', status: 'RUNNING',
    capital: '$2,000', monthly: '+1.74%', positions: '3 / 5', posLabel: '持仓',
    desc: '用 Delta 中性的姿势收资金费率,白嫖多头给空头交的钱。',
    monthlyTone: 'positive',
    thesis: '永续合约多头需要持续向空头支付资金费,Delta 中性持仓可以稳定收取费率,与现货价格波动解耦。资金费率为正且大于借币利率时即有利可图。',
    risks: ['资金费率反转(连续两期为负即触发自动平仓)', '现货-合约展期成本侵蚀收益', '交易所风险(API 中断 / 强平)'],
  },
  {
    num: '04', id: 'spot-perp', zhName: '期现套利',
    enLabel: 'SPOT-PERP PREMIUM · P0', phase: 'P0', status: 'RUNNING',
    capital: '$1,000', monthly: '+1.42%', positions: '2 / 5', posLabel: '持仓',
    desc: '抓"短期溢价扩大→收敛"的窗口,跟资金费率套利不同时间尺度。',
    monthlyTone: 'positive',
    thesis: '永续合约价格在情绪极端时可短期偏离指数价格(溢价/折价),通过现货-合约对冲锁定 basis,等基差均值回归后获利。',
    risks: ['基差进一步扩大(浮亏阶段)', '资金费率突变', '极端行情下现货-合约成交滑点'],
  },
  {
    num: '13', id: 'triangular', zhName: '三角套利',
    enLabel: 'TRIANGULAR ARBITRAGE · P0', phase: 'P0', status: 'RUNNING',
    capital: '$500', monthly: '+0.62%', positions: '14 次', posLabel: '今日触发',
    desc: '用 3 笔交易吃同一交易所内不同币对之间的微小价差。',
    monthlyTone: 'positive',
    thesis: '同一交易所内 A/B、B/C、C/A 三角路径价格不平衡时存在无风险套利窗口,延迟敏感、单次利润极薄。',
    risks: ['延迟竞速(领先方吃肉)', 'taker fee 吃掉利润', '单笔失败导致裸露敞口'],
  },
  {
    num: '02', id: 'perp-basis', zhName: '跨所基差套利',
    enLabel: 'PERP BASIS ARB · P1', phase: 'P1', status: 'RUNNING',
    capital: '$1,000', monthly: '+0.83%', positions: '1 / 3', posLabel: '持仓',
    desc: '做多便宜的合约,做空贵的合约,等基差收敛。',
    monthlyTone: 'positive',
    thesis: '不同交易所同一标的的永续合约因流动性、用户结构、资金费率差异短期会有 basis,通过 long-cheap / short-expensive 锁定。',
    risks: ['持仓时间不确定(回归窗口可能拉长)', '保证金分两边管理', '提币转移成本'],
  },
  {
    num: '03', id: 'spot-spread', zhName: '跨所价差套利',
    enLabel: 'SPOT SPREAD ARB · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '抓不同交易所现货价格的瞬时差异 (低延迟+提币速度敏感)。',
    thesis: '冷启动期间用 paper trading 收集低延迟通道与提币确认时间数据,等到稳定后才启用实盘。',
    risks: ['提币延迟(链上拥堵期间无法转移)', '挂单价被先吃', '部分交易所需要 KYC 审查'],
  },
  {
    num: '16', id: 'pairs-trading', zhName: '配对交易',
    enLabel: 'PAIRS TRADING · P1', phase: 'P1', status: 'UNDERWATER',
    capital: '$5,000', monthly: '-0.64%', positions: '2 / 5', posLabel: '配对',
    desc: 'ETH-BNB 配对当前 z-score = -2.4,等待回归到 0。',
    monthlyTone: 'negative',
    thesis: '统计套利:历史相关性高的两个标的偏离均值时反向开仓,等价差回归。当前持仓浮亏但 z-score 已极端,概率上有利。',
    risks: ['关联性结构破坏(两标的脱钩)', 'z-score 持续扩大触发止损', '资金占用周期长'],
  },
  {
    num: '05', id: 'cex-dex', zhName: 'CEX-DEX 套利',
    enLabel: 'CROSS-EXCHANGE · MONITOR ONLY', phase: 'P1', status: 'MONITOR',
    capital: '$0', monthly: null, positions: '23 次', posLabel: '本月推送',
    desc: '监控 CEX 和 DEX 之间的价差,推送有利润机会(MEV 风险下不自动执行)。',
    thesis: 'DEX 上 swap 滑点 + MEV bot 抢跑使得自动化执行 EV 为负,因此 Phase 1 仅监控并推送给人工判断。',
    risks: ['MEV 抢跑/三明治攻击', 'gas 成本 + 滑点蚕食利润', 'DEX 流动性突然枯竭'],
  },
  {
    num: '06', id: 'options-vol', zhName: '期权波动率套利',
    enLabel: 'OPTIONS VOL ARB · P3', phase: 'P3', status: 'DISABLED',
    capital: '$0', monthly: null, positions: '$30k+', posLabel: '解锁条件',
    desc: '资金已达解锁线,但高风险策略默认禁用。需要手动启用并先进入监控模式。',
    thesis: '通过隐含波动率 vs 已实现波动率 spread 做空/多 vol。需要 Greek 风险管理+对冲框架,资金门槛高。',
    risks: ['黑天鹅波动率 spike', 'Vega/Gamma 风险敞口管理复杂', 'Deribit / 链上期权流动性碎片化'],
  },
  {
    num: '07', id: 'grid', zhName: '网格策略',
    enLabel: 'GRID STRATEGY · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '区间内自动买卖,震荡行情友好,趋势行情吃亏。',
    thesis: '在选定区间内按等差/等比挂买卖网格,价格震荡时反复套利。适合横盘时期,趋势行情下会被动持续买入下跌资产。',
    risks: ['趋势突破网格区间(浮亏失控)', '区间选择失误', '挂单在交易所被踢'],
  },
  {
    num: '09', id: 'market-making', zhName: '做市策略',
    enLabel: 'MARKET MAKING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '订单簿挂单赚价差,需要做市返佣资格 + 极低延迟。',
    thesis: '在 best bid / ask 附近双边挂单,赚买卖价差 + maker rebate。需要交易所做市资格(VIP+ 或邀请制)。',
    risks: ['毒流量(被知情交易者扫单)', '库存风险(持仓偏向风险)', '需要专用低延迟基础设施'],
  },
  {
    num: '10', id: 'trend', zhName: '趋势跟踪',
    enLabel: 'TREND FOLLOWING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '技术指标识别中长期趋势,胜率不稳定,赚大输小。',
    thesis: '基于 MA/Donchian 通道/动量指标识别趋势启动,顺势开仓直到信号反转。胜率 30-40% 但盈亏比可达 3:1。',
    risks: ['震荡行情连续假突破', '反转信号滞后', '回撤期资金心理压力大'],
  },
  {
    num: '14', id: 'stablecoin-yield', zhName: '稳定币利率套利',
    enLabel: 'STABLECOIN YIELD · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '跨平台借贷利率差套利,低风险但收益微薄。',
    thesis: '在借入方便宜+借出方贵的平台之间转移稳定币,赚利差。低收益(年化几个%)但风险也低。',
    risks: ['平台清算风险', '稳定币脱锚事件', '链上转移延迟'],
  },
]

export function getStrategyById(id: string): Strategy | undefined {
  return STRATEGIES.find((s) => s.id === id)
}
