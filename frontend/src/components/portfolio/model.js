import { ASSET_TYPE_TO_KEY, typeOfSub } from './constants'
import { currencyOfCode, fundCategoryOf, stockCategoryOf, stockMarketOfCode } from './helpers'

// Normalize raw holding → unified row (股票 / 期货 T / 期权 O 同一管线)
export function normalizeHolding(h) {
  // 展示分类唯一标准: assets_classify.sub_assets_type → 大类/中类派生(后端透传), 不看 main_assets_type(引擎列, 钉住的衍生品判定)
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
    // 分类: A 股按后端东财一级行业; 基金按 assets_classify 中类(assets_type_name/main_assets_type)
    category: rowType === 'A' ? stockCategoryOf(h) : rowType === 'F' ? fundCategoryOf(h) : null,
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

// Normalize 场外 asset → unified row
export function normalizeAsset(a) {
  const key = ASSET_TYPE_TO_KEY[a.asset_type] || 'R'
  const mv = a.current_value
  // 基金/加密用摊薄成本(周期内减仓盈亏摊进成本, 与券商'成本价'同口径), 行内盈亏才和券商一致
  const cost = a.diluted_cost ?? a.cost_amount
  const q = a.quote
  // BOT 的 today 用 OKX floatProfit (浮动盈亏，未实现) 当代理 — 比 lifetime pnl 更接近"今日"
  let today = null
  if (a.asset_type === 'BOT' && q?.float_profit_usdt != null && mv) {
    const rate = q.usdcny || 7.2
    const float_cny = q.float_profit_usdt * rate
    // 反推等价 pct: float / (mv - float)
    const baseValue = mv - float_cny
    today = baseValue > 0 ? (float_cny / baseValue) * 100 : 0
  } else if (a.asset_type === 'FUND') {
    // 净值 T+1: 后端已折算"今日"口径 (净值当天 → change_pct; 滞后 → 底层 proxy; 都没有 → null 不计入)
    today = q?.today_change_pct ?? null
  } else if (q?.change_pct != null) {
    today = q.change_pct
  }
  // /api/assets 现透传 main_assets_type + assets_type_name(assets_classify 中类), 同持仓一个口径
  const cat = key === 'F' ? fundCategoryOf(a) : null
  return {
    id: `${key}-${a.id}`,
    src: 'asset',                 // 行来源标记: external_assets(/api/assets) —— 走场外链路
    type: key,
    category: cat,
    code: a.code,
    name: a.name,
    mv,
    cost,
    pnl: a.pnl,
    pnlPct: a.pnl_pct,
    today,
    _raw: a,
    broker: a.broker || null,
    extra: {
      platform: a.platform,
      broker: a.broker || null,
      nav: q?.nav ?? q?.est_nav,  // 优先官方净值，对齐 App "持有金额" 口径
      realtime: q?.realtime,
      price: q?.price,
      priceCny: q?.price_cny,
      amount: a.shares,
      lockUntil: null,
      okxSynced: q?.auto_synced,
      syncError: q?.sync_error,
      // 同步正常但策略已停止: 数字是最终态而非实时, 钱其实已回现货账户 —— 该归档退出在持
      okxStopped: q?.auto_synced === true && q?.active === false,
      okxState: q?.state,
      okxPnlPct: q?.pnl_pct,
      // OKX 马丁: 总预算 / 已投入 / 未投入 (反推自策略参数 initOrdAmt + safetyOrdAmt × volMult^k)
      okxInvestmentUsdt: q?.investment_usdt,
      okxTotalBudgetUsdt: q?.total_budget_usdt,
      okxAvailableUsdt: q?.available_usdt,
      okxBudgetSource: q?.budget_source,    // 'manual' | 'estimated'
      okxMaxSafetyOrders: q?.max_safety_orders,
      okxSafetyOrderAmt: q?.safety_order_amt,
      okxInitOrderAmt: q?.init_order_amt,
      okxVolMult: q?.vol_mult,
      assetIdRaw: a.id,
      annualYield: q?.annual_yield_rate ?? a.annual_yield_rate,
      impliedYield: q?.implied_yield_rate,  // 用 manual_value 反推的隐含年化
      daysHeld: q?.days_held,
      accruedInterest: q?.accrued_interest,
      // CASH 估算利息流 (基于年化 × 余额)
      dailyInterestEst: q?.daily_interest_est,
      monthlyInterestEst: q?.monthly_interest_est,
      yearlyInterestEst: q?.yearly_interest_est,
      // FUND 代理标的实时涨跌 (底层市场预判基金当日走势)
      proxyChangePct: q?.proxy_change_pct,
      proxyLabel: q?.proxy_label,
      proxyDetails: q?.proxy_details,
    },
  }
}

// Normalize 固定收益资产 (fix_income_assets, /api/fix-income) → unified row (W 桶)。
// 固定收益没有"今日波动": mv = 本金 + 应计利息(单利计提或手动市值), pnl = 应计利息,
// today=null 不进当日盈亏 —— 与 aggregate 里 W 属 T+1 市场不计周末的口径一致。
export function normalizeFixIncomeAsset(a) {
  const q = a.quote || {}
  const mv = a.current_value || 0
  const principal = a.principal || 0
  return {
    id: `W-fix-${a.id}`,
    src: 'fix-income',            // 行来源标记: fix_income_assets(/api/fix-income) —— 走固定收益链路
    type: 'W',
    category: null,
    code: a.code,
    name: a.name,
    mv,
    cost: principal,
    pnl: a.pnl ?? (mv - principal),
    pnlPct: a.pnl_pct,
    today: null,
    _raw: a,
    broker: null,
    extra: {
      _fixIncome: true,           // 弹窗表单按它切 API base (/api/fix-income vs /api/assets)
      assetIdRaw: a.id,
      platform: a.platform,
      sub: a.sub_assets_type,
      main: a.main_assets_type,
      currency: a.currency || 'CNY',
      principal,
      annualYield: q.annual_yield_rate ?? a.annual_yield_rate,
      rateType: q.rate_type,
      valueDate: q.value_date,
      daysHeld: q.days_held,
      maturityDate: q.maturity_date,
      daysToMaturity: q.days_to_maturity,
      termDays: q.term_days,
      interestType: q.interest_type,
      expectedMaturityValue: q.expected_maturity_value,
      accruedInterest: a.accrued_interest,
      valueSource: q.value_source,
      status: q.status || a.status,
      realizedPnl: a.ledger?.realized_pnl,
      incomeRealized: a.ledger?.income_realized,
      manualValue: a.manual_value,
      note: a.note,
    },
  }
}

// Aggregate by type → totals per group + grand total.
// `aShareTradingDay` controls whether T+1 markets (A-share, fund) contribute today.
// Crypto/Bot are 24/7 so they always contribute.
export function aggregate(rows, aShareTradingDay = true) {
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
    if (r.type === 'A' && currency && currency !== 'CNY') {
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
    // T+1 markets (A-share + fund + wealth + cash货基) — `today` is stale on weekends/holidays.
    // Crypto (C) and Bot (R) trade 24/7 and stay correct.
    // 港美股按各自市场: 后端给 today_valid(港股看港交所交易日, 美股看那场收盘距今), 不跟 A 股日历走
    const foreign = r.type === 'A' && (r.extra?.market === 'HK' || r.extra?.market === 'US')
    if (foreign) {
      if (r._raw?.today_valid === false) continue
    } else {
      const isT1Market = r.type === 'A' || r.type === 'F' || r.type === 'W' || r.type === 'M'
      if (isT1Market && !aShareTradingDay) continue
    }
    if (r.today != null && r.mv != null) {
      g.todayPnl += (r.mv * r.today / 100) / (1 + r.today / 100)
    }
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
