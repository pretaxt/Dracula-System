/**
 * Dracula 12 策略目录(2026-05-07 决策版,砍掉 #8 #11 #12 #15 #17)
 * 列表页与详情页共享数据源
 */

export type StrategyPhase = 'P0' | 'P1' | 'P3'
export type StrategyStatus = 'RUNNING' | 'PLANNED' | 'MONITOR' | 'DISABLED' | 'UNDERWATER' | 'PAPER'

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
  rules?: {
    entry: string[]
    exit: string[]
    params: { label: string; value: string }[]
  }
}

export const STRATEGIES: Strategy[] = [
  {
    num: '01', id: 'funding-rate', zhName: '资金费率套利',
    enLabel: 'FUNDING RATE ARBITRAGE · 主力 P0', phase: 'P0', status: 'RUNNING',
    capital: '$5,000', monthly: '+3.0%', positions: '0 / 5', posLabel: '持仓',
    desc: '用 Delta 中性的姿势收资金费率,白嫖多头给空头交的钱。',
    monthlyTone: 'positive',
    thesis: '永续合约多头需要持续向空头支付资金费,Delta 中性持仓可以稳定收取费率,与现货价格波动解耦。资金费率为正且大于借币利率时即有利可图。',
    risks: ['资金费率反转(连续两期为负即触发自动平仓)', '现货-合约展期成本侵蚀收益', '交易所风险(API 中断 / 强平)'],
    rules: {
      entry: [
        'APR ≥ 50%',
        '24h 成交额 ≥ $10,000,000 USDT',
        '订单簿深度 ≥ $10,000(现货 + 永续)',
        '历史 9 期至少 7 期正费率(稳定性过滤)',
        '资金费结算前 15 分钟内开仓',
      ],
      exit: [
        '资金费率 ≤ 0(立即平仓)',
        '当前 APR < 10%(机会衰减)',
        '净收益 ≥ 10%(止盈)',
        '止损 -5%(净 PnL)',
        '永续单腿亏损 ≥ 80% 保证金(防强平,双腿同平)',
        '持仓满 240 小时',
      ],
      params: [
        { label: '候选币种', value: '~599(Binance + OKX 全 USDT 永续动态发现)' },
        { label: '同时持仓上限', value: '5 笔' },
        { label: '单笔规模 (notional)', value: '$50 (实盘第一阶段)' },
        { label: '永续杠杆', value: '5x (isolated)' },
        { label: '单笔保证金', value: '$10 (=$50/5)' },
        { label: '单笔总占用', value: '$60 ($50 现货 + $10 保证金)' },
        { label: '永续清算价', value: '约 +19.5% (entry × 1.195，short 方向)' },
        { label: '总名义敞口上限', value: '$3,000' },
        { label: '扫描间隔', value: '60 秒' },
        { label: '最少持仓', value: '24 小时(过滤短期噪音)' },
      ],
    },
  },
  {
    num: '02', id: 'perp-basis', zhName: '跨所 funding 差套利',
    enLabel: 'PERP BASIS ARB · P0', phase: 'P0', status: 'PAPER',
    capital: '$0', monthly: null, positions: '0 / 2', posLabel: '持仓',
    desc: '同一标的在不同交易所的 funding rate 差 → 高 funding 端 SHORT + 低 funding 端 LONG，跨所 delta-neutral。',
    thesis: '不同交易所流动性 / 用户结构 / 持仓比例差异导致同一永续合约的 funding rate 不同步，差额持续存在时可锁定 (short_apr - long_apr) × notional × 持仓时间，价格风险被跨所对冲抵消。回测显示 14 天最优阈值 ≥ 50% APR diff 才能覆盖手续费。',
    risks: [
      '跨所价格脱钩（市场极端 / 交易所故障 → 持续价差 = 持续亏损，已加 stop_price_divergence_pct 强平保护）',
      'funding 周期不同步（binance 8h vs htx 4h，需独立按时间戳累计）',
      '小所 funding 数据脏点（HTX -99% 类异常，已加 max_abs_apr_pct 过滤）',
      '资金分两边账户管理 + 提币转移成本',
      '需要在 ≥ 2 个交易所配 trading API key + perp 余额',
    ],
    rules: {
      entry: [
        '跨所 funding diff APR ≥ 50%（基于 sweep 14 天历史最优阈值）',
        '两侧单边 APR 绝对值 ≤ 500%（防小所脏数据）',
        '同时持仓数小于 2 笔',
        '同一 (symbol, long_ex, short_ex) 不可重复开仓',
        'long_exchange + short_exchange 都需 trading key + perp USDT 余额',
      ],
      exit: [
        'diff_apr 衰减到 ≤ 1%（min_hold 4h 后才检查）',
        '持仓时长达到 max_hold_hours（默认 240h / 10 天兜底）',
        '跨所价格脱钩 ≥ 5%（最高优先级强平，防价差扩大）',
      ],
      params: [
        { label: '入场门槛', value: 'funding diff APR ≥ 50%' },
        { label: '最少持仓', value: '4 小时' },
        { label: '最长持仓', value: '240 小时' },
        { label: '收敛平仓', value: 'diff_apr ≤ 1%' },
        { label: '价格脱钩强平', value: '|long_price - short_price| / mid ≥ 5%' },
        { label: '健康度分级', value: 'safe (≤200%) · risky (200-500%) · dirty (>500%)' },
        { label: '同时持仓上限', value: '2 笔' },
        { label: '单笔名义规模', value: '$50（每边 perp）' },
        { label: '永续杠杆', value: '5x（双边各 $10 margin）' },
        { label: '候选币种', value: '30 主流 USDT 永续' },
        { label: '交易所配对', value: 'binance / okx / bitget / bybit / htx 5 家两两 = 10 pairs' },
        { label: '扫描间隔', value: '30 秒（数据来自 MarketDataHub 缓存）' },
        { label: 'PnL 计算', value: '跨所 funding 差累计 + 反向单成交差 - 4 腿 fee' },
      ],
    },
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
    num: '04', id: 'spot-perp', zhName: '期现套利',
    enLabel: 'SPOT-PERP BASIS · P0', phase: 'P0', status: 'RUNNING',
    capital: '$150', monthly: null, positions: '0 / 3', posLabel: '持仓',
    desc: '抓"短期溢价/折价扩大→收敛"的窗口,跟资金费率套利不同时间尺度。升水 + 贴水双向已实盘。',
    thesis: '永续合约价格在情绪极端时可短期偏离现货价格(溢价/折价),通过现货-合约对冲锁定 basis,等基差均值回归后获利;PnL 由真实成交价 + 资金费率 + 借币利息显式计算。',
    risks: ['基差进一步扩大(浮亏阶段)', '资金费率反向变动侵蚀收益', '贴水方向借币利息累计', '极端行情下现货-合约成交滑点'],
    rules: {
      entry: [
        '基差绝对值 ≥ 0.25%(实盘入场阈值,留出手续费 + 资金费缓冲)',
        '方向为升水(永续 > 现货)或贴水(永续 < 现货),双向均已实盘',
        '同时持仓数小于 3 笔',
        '同一标的不可重复开仓',
        '候选币种在 30 标的列表内',
      ],
      exit: [
        '当前基差绝对值 ≤ 0.03%(基差收敛,主要盈利路径)',
        '持仓时长达到 12 小时(超时强平)',
        '永续单腿被交易所强平 → 强平监控自动平掉现货腿兜底',
      ],
      params: [
        { label: '候选币种', value: '币安 + 欧易 各 30 主流 USDT 永续合约' },
        { label: '同时持仓上限', value: '3 笔' },
        { label: '单笔名义规模', value: '50 美元(实盘启动)' },
        { label: '永续杠杆', value: '3 倍(逐仓)' },
        { label: '单笔保证金', value: '约 17 美元(=50/3)' },
        { label: '单笔总占用', value: '67 美元(现货 50 + 保证金 17)' },
        { label: '扫描门槛', value: '基差绝对值 ≥ 0.10%(候选展示用)' },
        { label: '入场阈值', value: '基差绝对值 ≥ 0.25%(实盘开仓)' },
        { label: '收敛平仓阈值', value: '基差绝对值 ≤ 0.03%' },
        { label: '最大持仓时长', value: '12 小时' },
        { label: '总名义敞口上限', value: '150 美元(3 × 50)' },
        { label: '扫描间隔', value: '60 秒' },
        { label: '允许方向', value: '升水 + 贴水双向(贴水自动借币卖空)' },
        { label: 'PnL 计算', value: '真实成交价 + 资金费 + 借币利息(实时显示)' },
      ],
    },
  },
  {
    num: '05', id: 'cex-dex', zhName: 'CEX-DEX 套利',
    enLabel: 'CROSS-EXCHANGE · MONITOR ONLY', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
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
    num: '08', id: 'market-making', zhName: '做市策略',
    enLabel: 'MARKET MAKING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '订单簿挂单赚价差,需要做市返佣资格 + 极低延迟。',
    thesis: '在 best bid / ask 附近双边挂单,赚买卖价差 + maker rebate。需要交易所做市资格(VIP+ 或邀请制)。',
    risks: ['毒流量(被知情交易者扫单)', '库存风险(持仓偏向风险)', '需要专用低延迟基础设施'],
  },
  {
    num: '09', id: 'trend', zhName: '趋势跟踪',
    enLabel: 'TREND FOLLOWING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '技术指标识别中长期趋势,胜率不稳定,赚大输小。',
    thesis: '基于 MA/Donchian 通道/动量指标识别趋势启动,顺势开仓直到信号反转。胜率 30-40% 但盈亏比可达 3:1。',
    risks: ['震荡行情连续假突破', '反转信号滞后', '回撤期资金心理压力大'],
  },
  {
    num: '10', id: 'triangular', zhName: '三角套利',
    enLabel: 'TRIANGULAR ARBITRAGE · P0', phase: 'P0', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '用 3 笔交易吃同一交易所内不同币对之间的微小价差。',
    thesis: '同一交易所内 A/B、B/C、C/A 三角路径价格不平衡时存在无风险套利窗口,延迟敏感、单次利润极薄。',
    risks: ['延迟竞速(领先方吃肉)', 'taker fee 吃掉利润', '单笔失败导致裸露敞口'],
  },
  {
    num: '11', id: 'stablecoin-yield', zhName: '稳定币利率套利',
    enLabel: 'STABLECOIN YIELD · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '跨平台借贷利率差套利,低风险但收益微薄。',
    thesis: '在借入方便宜+借出方贵的平台之间转移稳定币,赚利差。低收益(年化几个%)但风险也低。',
    risks: ['平台清算风险', '稳定币脱锚事件', '链上转移延迟'],
  },
  {
    num: '12', id: 'pairs-trading', zhName: '配对交易',
    enLabel: 'PAIRS TRADING · P1', phase: 'P1', status: 'PLANNED',
    capital: '$0', monthly: null, positions: '—', posLabel: '持仓',
    desc: '统计套利:历史相关性高的两个标的偏离均值时反向开仓,等价差回归。',
    thesis: '统计套利:历史相关性高的两个标的偏离均值时反向开仓,等价差回归。当前持仓浮亏但 z-score 已极端,概率上有利。',
    risks: ['关联性结构破坏(两标的脱钩)', 'z-score 持续扩大触发止损', '资金占用周期长'],
  },
]

export function getStrategyById(id: string): Strategy | undefined {
  return STRATEGIES.find((s) => s.id === id)
}
