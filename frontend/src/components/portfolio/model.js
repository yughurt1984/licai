import { typeOfSub } from './constants'
import { currencyOfCode, fundCategoryOf, stockCategoryOf, stockMarketOfCode } from './helpers'

// Normalize raw holding → unified row (股票 / 期货 T / 期权 O 同一管线)
export function normalizeHolding(h) {
  // 展示大类唯一标准: assets_classify.sub_assets_type → 大类派生(后端透传)。
  // 基金子分组按中类: 优先 main_assets_type, 非法(如 REITs 被钉成 common_share)时从 sub 推导。
  const rowType = typeOfSub(h.sub_assets_type, { assets_class: h.assets_class, main_assets_type: h.main_assets_type }) || 'A'
  const isShort = h.direction === 'SHORT'
  // 期货/期权/股票 都是双向(做多/做空), 没带方向时按做多兜底 —— 交易方向列统一展示
  const direction = h.direction || (rowType === 'A' || rowType === 'T' || rowType === 'O' ? 'LONG' : null)
  const mv = h.market_value || (h.current_price * h.shares) || 0
  const cost = h.cost_value ?? (h.cost_price * h.shares)
  const originalMarketValue = h.original_market_value ?? (h.current_price ? h.current_price * h.shares : null)
  const originalCostValue = h.original_cost_value ?? (h.cost_price * h.shares)
  return {
    id: `${rowType}-${h.stock_code}-${h.direction || ''}`,
    src: 'holding',               // 行来源标记: holdings(/api/portfolio) —— 流水/编辑/加减仓走持仓链路
    type: rowType,
    category: rowType === 'A' ? stockCategoryOf(h)
      : rowType === 'F' ? fundCategoryOf(h.sub_assets_type, h.main_assets_type, h.assets_type_name) : null,
    code: h.stock_code,
    name: h.stock_name,
    broker: h.broker || null,
    mv,
    cost,
    // 后端无行情时 unrealized_pnl=null 且 mv=0 —— 不能退到 mv−cost(那会显示成 -成本),
    // 置 null 让行内显示 -- 并被浮动合计排除, 等行情 bootstrap 补上后自然恢复。
    // 空头镜像: 浮动 = 成本 − 市值(价跌=赚)。
    pnl: h.unrealized_pnl ?? (mv > 0 ? (isShort ? cost - mv : mv - cost) : null),
    pnlPct: h.pnl_pct,
    // 今日涨跌: 后端已按方向折算好(空头已翻号, 期货/股票空头同一约定), 这里不再反转
    today: h.price_change_pct == null ? null : h.price_change_pct,
    _raw: h,
    extra: {
      direction,
      multiplier: h.contract_multiplier || 1,
      // 期货: 总资产的资产贡献 = 名义市值 × 保证金率(后端算好); 股票空头 = 成本+浮动; 其余为 null(按 mv 计)
      marginValue: h.margin_value ?? null,
      market: rowType === 'T' ? 'FUT' : rowType === 'O' ? 'OPT' : (h.market || stockMarketOfCode(h.stock_code)),
      currency: h.currency || currencyOfCode(h.stock_code),
      fxRate: h.fx_rate || 1,
      fxTime: h.fx_time || '',
      fxSource: h.fx_source || '',
      originalMarketValue,
      originalCostValue,
      shares: h.shares,
      price: h.current_price,
      avgCost: h.cost_price,
    },
  }
}

// 单行今日盈亏(元), 与大类汇总同口径, 供次级分类行复用:
// 后端 today 是涨跌幅%(已按方向折算), mv 是涨完后的市值 → 今日盈亏 = mv×p/(1+p), 不是 mv×p。
// 一律按行情自带的涨跌幅算 —— 行情源在休市日给的就是最近交易日的涨跌, 直接显示即可,
// 不再按交易日判断归零(否则非交易日所有类别都是 0, 看不到任何数据)。
export function todayPnlOf(r) {
  if (r.today == null || r.mv == null) return 0
  return (r.mv * r.today / 100) / (1 + r.today / 100)
}

// Aggregate by type → totals per group + grand total.
export function aggregate(rows) {
  // 资产贡献口径: 期货是杠杆交易, 计入总资产的是保证金占用(名义市值×保证金率),
  // 不是名义市值(那会把 10 倍杠杆的名义敞口全算成资产); 股票空头 = 成本+浮动
  // (开空收到的现金趴在这注仓里, 后端算好放 margin_value); 其余类型 = 市值。
  // 仅期货/期权(type=T)用保证金口径(成本+浮动); 股票空头与普通多头一律用市值口径,
  // 与顶栏「总资产」(Dashboard 只对有 fut 用 margin_value)保持一致, 否则空头会被算成 成本+浮动 虚高
  const assetVal = (r) =>
    r.extra?.marginValue != null && r.type === 'T'
      ? r.extra.marginValue : (r.mv || 0)
  const totalMv = rows.reduce((s, r) => s + assetVal(r), 0)
  const groups = {}
  const fxExposure = {}
  for (const r of rows) {
    if (!groups[r.type]) groups[r.type] = { items: [], mv: 0, asset: 0, cost: 0, pnl: 0, todayPnl: 0 }
    const g = groups[r.type]
    g.items.push(r)
    g.mv += r.mv || 0          // 名义市值(期货=价×乘数×手数, 用于行内/分组展示)
    g.asset += assetVal(r)     // 资产贡献(期货=保证金占用)
    g.cost += r.cost || 0
    g.pnl += r.pnl || 0
    const currency = r.extra?.currency
    if ((r.type === 'A' || r.type === 'T' || r.type === 'O') && currency && currency !== 'CNY') {
      if (!fxExposure[currency]) {
        fxExposure[currency] = {
          currency,
          originalMarketValue: 0,
          marketValue: 0,
          fxRate: r.extra?.fxRate || 1,
          fxTime: r.extra?.fxTime || '',
          fxSource: r.extra?.fxSource || '',
        }
      }
      fxExposure[currency].originalMarketValue += r.extra?.originalMarketValue || 0
      fxExposure[currency].marketValue += r.mv || 0
      fxExposure[currency].fxRate = r.extra?.fxRate || fxExposure[currency].fxRate
      fxExposure[currency].fxTime = r.extra?.fxTime || fxExposure[currency].fxTime
      fxExposure[currency].fxSource = r.extra?.fxSource || fxExposure[currency].fxSource
    }
    g.todayPnl += todayPnlOf(r)
  }
  for (const t in groups) {
    const g = groups[t]
    g.weight = totalMv > 0 ? g.asset / totalMv : 0   // 权重按资产贡献(期货=保证金)算, 与总资产同口径
    g.pnlPct = g.cost > 0 ? (g.pnl / g.cost) * 100 : 0
  }
  return {
    groups,
    totalMv,
    totalCost: rows.reduce((s, r) => s + (r.cost || 0), 0),
    // 行情未加载(有成本但市值缺失/为0)时, 这笔 pnl 不可信(会把成本瞬时当大额浮亏),
    // 不计入总浮动, 避免调流水/基金净值未确认的中间态出现吓人的负数。等市值到位再计入。
    totalPnl: rows.reduce((s, r) => {
      if ((r.cost || 0) > 0 && !(r.mv > 0)) return s
      return s + (r.pnl || 0)
    }, 0),
    totalToday: Object.values(groups).reduce((s, g) => s + g.todayPnl, 0),
    fxExposure: Object.values(fxExposure).filter(e => e.originalMarketValue > 0),
  }
}
