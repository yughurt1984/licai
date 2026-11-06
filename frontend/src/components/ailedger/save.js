// AI 记账 · 草稿入账: 把 LLM 解析出的交易草稿写到**现有**端点上。
// 没有任何新端点 —— 股票/期货/期权走 portfolio 路由, 其余走 assets 路由,
// 和持仓页各表单的提交目标是同一批。
import { isDerivSub } from '../portfolio/constants'
import { fetchJSON, api } from '../../hooks/useApi'
import { isOnchainEtf } from '../../helpers'
import { stockCodeForMarket } from '../portfolio/helpers'

const num = (v) => {
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : null
}

// 每类草稿在卡片上的"必填校验 + 目标端点"各不相同, 一个分支一个函数。
export async function saveDraft(d, ctx) {
  switch (d.type) {
    case 'A': return saveStock(d, ctx)
    case 'F': return saveFund(d, ctx)
    case 'C': return saveCrypto(d, ctx)
    case 'W': return saveWealth(d)
    case 'M': return saveCash(d, ctx)
    case 'R': return saveBot(d)
    case 'T':
    case 'O': return saveFutures(d, ctx)
    default: throw new Error('未支持的资产类型')
  }
}

async function saveStock(d, ctx) {
  const code = stockCodeForMarket(d.market || 'A', d.code)
  if (!d.code) throw new Error('缺代码')
  const price = num(d.price)
  const shares = num(d.shares)
  if (!(price > 0)) throw new Error('价格必须大于 0')
  if (!(shares > 0)) throw new Error('数量必须大于 0')
  const holding = (ctx.holdings || []).find(
    h => h.stock_code === code && !isDerivSub(h.sub_assets_type))
  const body = { price, shares }
  if (d.tradeDate) body.trade_date = d.tradeDate
  if (d.tradeTime) body.trade_time = d.tradeTime
  if (num(d.fee) > 0) body.fee = num(d.fee)
  if (d.broker) body.broker = d.broker
  const sell = (d.side || 'BUY') === 'SELL'
  if (sell && !holding) throw new Error(`账上没有 ${code} 的持仓, 卖出请先在持仓页核对该标的`)
  if (holding) {
    await fetchJSON(`/api/portfolio/${encodeURIComponent(code)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action_type: sell ? 'SELL' : 'BUY', ...body }),
    })
    return `${sell ? '卖出' : '加仓'} ${holding.stock_name || code} ${shares}@${price}`
  }
  await api.addHolding({
    stock_code: code, stock_name: d.name || '', shares, cost_price: price, ...body,
  })
  return `建仓 ${d.name || code} ${shares}@${price}`
}

async function saveFutures(d, ctx) {
  const isOpt = d.type === 'O'
  const q = String(d.code || '').trim()
  if (!q) throw new Error('缺品种代码')
  // 品种行来自 symbol_dict(同 AddFuturesForm 的搜索端点): 拼合约代码要用交易所后缀
  const r = await fetchJSON(
    `/api/portfolio/products/search?q=${encodeURIComponent(q)}&kind=${isOpt ? 'opt' : 'fut'}`)
  const items = r?.items || []
  const product = items.find(p => (p.code || '').toUpperCase() === q.toUpperCase()) || items[0]
  if (!product) throw new Error(`symbol_dict 里没有品种「${q}」(需先在品种库维护)`)
  const month = String(d.month || '').trim()
  if (!/^\d{4}$/.test(month)) throw new Error('合约月份应为 YYMM 四位数字')
  const thscode = `${product.code}${month}${product.exchange ? '.' + product.exchange : ''}`.toUpperCase()
  const direction = (d.direction || 'LONG').toUpperCase() === 'SHORT' ? 'SHORT' : 'LONG'
  const lots = num(d.shares)
  const price = num(d.price)
  if (!(lots > 0)) throw new Error('手数必须大于 0')
  if (!(price > 0)) throw new Error('价格必须大于 0')
  const mult = num(d.multiplier) || product.trade_amount || 1
  const holding = (ctx.holdings || []).find(h => h.stock_code === thscode && h.direction === direction)
  const close = (d.side || 'OPEN') === 'CLOSE'
  const body = { price, shares: lots, direction, contract_multiplier: mult }
  if (d.tradeDate) body.trade_date = d.tradeDate
  if (d.tradeTime) body.trade_time = d.tradeTime
  if (d.fee !== '' && num(d.fee) >= 0) body.fee = num(d.fee)   // 留空 = 后端按品种费率自动
  if (d.broker) body.broker = d.broker
  if (close || holding) {
    if (!holding) throw new Error(`没有 ${thscode} ${direction === 'LONG' ? '多' : '空'}仓, 平仓需先有持仓`)
    await fetchJSON(`/api/portfolio/${encodeURIComponent(thscode)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action_type: close ? 'CLOSE' : 'OPEN', ...body }),
    })
    return `${close ? '平仓' : '加仓'} ${thscode} ${lots}手@${price}`
  }
  await fetchJSON('/api/portfolio', {
    method: 'POST',
    body: JSON.stringify({
      stock_code: thscode,
      stock_name: (product.name || product.code || '') + month,
      asset_kind: isOpt ? 'opt' : 'fut',
      ...body,
    }),
  })
  return `开仓 ${thscode} ${lots}手@${price}`
}

async function saveFund(d, ctx) {
  const code = String(d.code || '').trim()
  if (!/^\d{6}$/.test(code)) throw new Error('基金代码应为 6 位数字')
  const kind = d.fundKind === 'etf' || d.fundKind === 'open'
    ? d.fundKind
    : (isOnchainEtf(code) ? 'etf' : 'open')
  const price = num(d.price)
  const shares = num(d.shares)
  const fee = num(d.fee) || 0
  const asset = (ctx.assets || []).find(a => a.asset_type === 'FUND' && a.code === code)
  if ((d.side || 'BUY') === 'SELL') {
    if (!asset) throw new Error(`账上没有基金 ${code}, 卖出前需先持有`)
    if (kind === 'open') {
      if (!(shares > 0)) throw new Error('场外基金赎回必须填份额(T+1 按净值确认)')
      await fetchJSON(`/api/assets/${asset.id}/reduce-lot`, {
        method: 'POST',
        body: JSON.stringify({
          amount: 0, shares,
          trade_date: d.tradeDate || undefined, trade_time: d.tradeTime || undefined,
        }),
      })
      return `赎回 ${asset.name || code} ${shares} 份 (待净值确认)`
    }
    const amount = num(d.amount) || (shares > 0 && price > 0 ? shares * price : null)
    if (!(amount > 0)) throw new Error('ETF 卖出需要金额(份额×净值)')
    await fetchJSON(`/api/assets/${asset.id}/reduce-lot`, {
      method: 'POST',
      body: JSON.stringify({
        amount, shares: shares || undefined, unit_price: price || undefined,
        trade_date: d.tradeDate || undefined,
      }),
    })
    return `卖出 ${asset.name || code} ¥${amount.toFixed(2)}`
  }
  // 买入: 场内按份额×净值+费, 场外有份额走确认、只有金额走 pending(T+1)
  const gross = (shares > 0 && price > 0) ? shares * price : num(d.amount)
  if (!(gross > 0)) throw new Error(kind === 'etf' ? 'ETF 买入需份额和净值' : '申购需金额(或份额×净值)')
  const res = await fetchJSON('/api/assets', {
    method: 'POST',
    body: JSON.stringify({
      asset_type: 'FUND', fund_kind: kind, code, name: d.name || code,
      cost_amount: Math.round((gross + fee) * 100) / 100,
      shares: shares || null, fee: fee > 0 ? fee : null,
      broker: d.broker || null,
      start_date: d.tradeDate || null, trade_time: d.tradeTime || null,
    }),
  })
  const label = d.name || code
  return res?.merged
    ? `已并入 ${label} 的持仓流水`
    : (res?.pending ? `申购 ${label} ¥${gross.toFixed(2)} (T+1 待确认)` : `申购 ${label} ${shares || ''}${shares ? '份' : ''}`)
}

async function saveCrypto(d, ctx) {
  const code = String(d.code || '').trim().toUpperCase()
  if (!code) throw new Error('缺币对(如 BTC-USDT)')
  const price = num(d.price)
  const shares = num(d.shares)
  const fee = num(d.fee) || 0
  const asset = (ctx.assets || []).find(a => a.asset_type === 'CRYPTO' && a.code === code)
  if ((d.side || 'BUY') === 'SELL') {
    if (!asset) throw new Error(`账上没有 ${code}, 卖出前需先持有`)
    const amount = num(d.amount) || (shares > 0 && price > 0 ? shares * price : null)
    if (!(amount > 0)) throw new Error('卖出需要金额(数量×单价)')
    await fetchJSON(`/api/assets/${asset.id}/reduce-lot`, {
      method: 'POST',
      body: JSON.stringify({
        amount, shares: shares || undefined, unit_price: price || undefined,
        trade_date: d.tradeDate || undefined,
      }),
    })
    return `卖出 ${asset.name || code}`
  }
  const gross = num(d.amount) || (shares > 0 && price > 0 ? shares * price : null)
  if (!(gross > 0)) throw new Error('买入需要金额(或数量×单价)')
  await fetchJSON('/api/assets', {
    method: 'POST',
    body: JSON.stringify({
      asset_type: 'CRYPTO', code, name: d.name || code,
      cost_amount: Math.round((gross + fee) * 100) / 100,
      shares: shares || null, fee: fee > 0 ? fee : null,
      broker: d.broker || null,
      start_date: d.tradeDate || null, trade_time: d.tradeTime || null,
    }),
  })
  return `买入 ${d.name || code}`
}

async function saveWealth(d) {
  const principal = num(d.amount)
  if (!(principal > 0)) throw new Error('投入本金必须大于 0')
  const name = String(d.name || '').trim() || '理财产品'
  const y = num(d.annualYieldPct)
  await fetchJSON('/api/assets', {
    method: 'POST',
    body: JSON.stringify({
      asset_type: 'WEALTH',
      code: String(d.code || '').trim() || name, name,
      platform: d.broker || d.platform || '',
      cost_amount: principal,
      annual_yield_rate: y != null ? y / 100 : null,
      start_date: d.startDate || d.tradeDate || null,
    }),
  })
  return `登记理财 ${name} ¥${principal.toFixed(2)}`
}

async function saveCash(d, ctx) {
  const balance = num(d.amount)
  if (balance == null || balance < 0) throw new Error('余额无效')
  const name = String(d.name || '').trim() || '现金账户'
  const code = String(d.code || '').trim() || name
  const y = num(d.annualYieldPct)
  // 同名/同标识的现金账户已存在 → 直接更新余额(现金不是流水型资产, 记的就是当下)
  const exist = (ctx.assets || []).find(
    a => a.asset_type === 'CASH' && (a.code === code || a.name === name))
  if (exist) {
    await fetchJSON(`/api/assets/${exist.id}`, {
      method: 'PUT',
      body: JSON.stringify({ cost_amount: balance, manual_value: balance }),
    })
    return `已更新 ${exist.name} 余额为 ¥${balance.toFixed(2)}`
  }
  await fetchJSON('/api/assets', {
    method: 'POST',
    body: JSON.stringify({
      asset_type: 'CASH', code, name,
      platform: d.broker || d.platform || '',
      cost_amount: balance, manual_value: balance,
      annual_yield_rate: y != null ? y / 100 : null,
    }),
  })
  return `登记现金 ${name} ¥${balance.toFixed(2)}`
}

async function saveBot(d) {
  const principal = num(d.amount)
  if (!(principal > 0)) throw new Error('投入本金必须大于 0')
  const name = String(d.name || '').trim() || '量化机器人'
  await fetchJSON('/api/assets', {
    method: 'POST',
    body: JSON.stringify({
      asset_type: 'BOT',
      code: String(d.code || '').trim() || name, name,
      platform: d.broker || d.platform || '',
      cost_amount: principal,
      manual_value: num(d.currentValue),
      note: d.note || '',
    }),
  })
  return `登记机器人 ${name} ¥${principal.toFixed(2)}`
}
