import { typeOfSub } from './portfolio/constants'
import { useState, useEffect } from 'react'
import { fetchJSON, MUTATED_EVENT } from '../hooks/useApi'
import { fmtMoney, priceColor } from '../helpers'
import Tooltip from './Tooltip'

function currencySymbol(currency = 'CNY') {
  if (currency === 'USD') return 'US$'
  if (currency === 'HKD') return 'HK$'
  return '¥'
}

function formatCurrencyMoney(currency, value) {
  return `${currencySymbol(currency)}${fmtMoney(value)}`
}

function fxSourceLabel(source) {
  if (source === 'sina_bid_ask_mid') return '新浪外汇买卖价中间价'
  if (source === 'fallback') return '备用汇率'
  if (source === 'last_known') return '上次成功汇率'
  if (source === 'CNY') return '人民币'
  return source || '汇率'
}

export default function Dashboard({ holdings }) {
  const [realized, setRealized] = useState({ stock: 0 })
  const [srvToday, setSrvToday] = useState(null)   // 券商口径当日盈亏(后端算, 与持仓总览同源)

  useEffect(() => {
    const load = async () => {
      try {
        const s = await fetchJSON('/api/portfolio/realized')
        // 加权平均口径: 浮动 = 市值 − 成本×份额, 从不含已实现 → 已实现直接用
        // (切换日后的平仓事件 + 分红), 总盈亏 = 浮动 + 已实现 直接相加
        const realizedTotal = s.total_realized_pnl != null
          ? s.total_realized_pnl
          : (s.items || []).reduce((sum, it) => sum + (it.realized_pnl || 0), 0)
        setRealized({ stock: Math.round(realizedTotal * 100) / 100 })
      } catch {}
    }
    load()
    const t = setInterval(load, 30000)
    window.addEventListener(MUTATED_EVENT, load)
    return () => { clearInterval(t); window.removeEventListener(MUTATED_EVENT, load) }
  }, [])


  useEffect(() => {
    const load = async () => {
      try {
        const d = await fetchJSON('/api/portfolio/today-pnl')
        setSrvToday(d && typeof d.total === 'number' ? d : null)
      } catch { setSrvToday(null) }
    }
    load()
    const t = setInterval(load, 30000)
    window.addEventListener(MUTATED_EVENT, load)
    return () => { clearInterval(t); window.removeEventListener(MUTATED_EVENT, load) }
  }, [])

  if (!holdings || holdings.length === 0) return null

  // --- Stock aggregates ---
  // 期货是杠杆交易: 计入总资产的是保证金占用(后端算好的 margin_value = 成本×保证金率+浮动盈亏),
  // 不是名义市值 —— 否则顶栏总资产和持仓总览(totalMv 同口径)对不上。
  const aValue = holdings.reduce((s, h) =>
    s + (h.margin_value != null && typeOfSub(h.sub_assets_type) === 'T' ? h.margin_value : (h.market_value || 0)), 0)
  const aCost = holdings.reduce((s, h) => s + (h.cost_value ?? h.cost_price * h.shares), 0)
  const aPnl = holdings.reduce((s, h) => s + (h.unrealized_pnl || 0), 0)
  const foreignExposure = Object.values(holdings.reduce((acc, h) => {
    const currency = h.currency
    if (!currency || currency === 'CNY') return acc
    if (!acc[currency]) {
      acc[currency] = {
        currency,
        originalMarketValue: 0,
        marketValue: 0,
        fxRate: h.fx_rate || 1,
        fxTime: h.fx_time || '',
        fxSource: h.fx_source || '',
      }
    }
    acc[currency].originalMarketValue += h.original_market_value || (h.current_price ? h.current_price * h.shares : 0)
    acc[currency].marketValue += h.market_value || 0
    acc[currency].fxRate = h.fx_rate || acc[currency].fxRate
    acc[currency].fxTime = h.fx_time || acc[currency].fxTime
    acc[currency].fxSource = h.fx_source || acc[currency].fxSource
    return acc
  }, {})).filter(e => e.originalMarketValue > 0)

  // 当日盈亏用后端券商口径(含今日清仓的已实现 + 今日新建仓按买入价为基准), 与持仓
  // 总览 SummaryStrip 同源, 两处必须一致 —— 否则顶栏和总览会给出两个数。
  const srvOk = srvToday && typeof srvToday.total === 'number'
  const todayPnl = srvOk ? srvToday.total : null

  // --- Combined ---
  const totalValue = aValue
  const totalCost = aCost
  const realizedTotal = realized?.stock || 0
  const unrealizedPnl = aPnl
  const totalPnl = unrealizedPnl + realizedTotal
  const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-border-subtle bg-surface/40 overflow-x-auto overflow-y-hidden"
      style={{ animation: 'fade-up 0.25s ease-out' }}>

      {/* Total (股票持仓口径) */}
      <div className="flex items-center gap-1.5 shrink-0">
        <Tooltip content={
          <div className="leading-relaxed">
            <div className="text-text-bright font-semibold mb-1">总资产口径</div>
            <div>主数字统一按人民币估值。</div>
            {foreignExposure.length > 0 && (
              <div className="mt-1 text-text-dim text-[0.75rem]">
                港美股按近实时汇率折算，非银行最终结算价。
              </div>
            )}
          </div>
        }>
          <span className="text-[0.7857rem] text-text-muted cursor-help">总资产</span>
        </Tooltip>
        <span className="text-[1rem] font-mono font-semibold text-text-bright">
          ¥{fmtMoney(totalValue)}
        </span>
        {foreignExposure.length > 0 && (
          <span className="text-[0.6786rem] text-text-muted">人民币口径</span>
        )}
        {foreignExposure.map(e => (
          <Tooltip key={e.currency} content={
            <div className="leading-relaxed">
              <div className="text-text-bright font-semibold mb-1">{e.currency} 持仓原币市值</div>
              <div>{formatCurrencyMoney(e.currency, e.originalMarketValue)} → ¥{fmtMoney(e.marketValue)}</div>
              <div className="text-text-dim text-[0.75rem] mt-1">
                {e.currency}/CNY {Number(e.fxRate || 1).toFixed(4)} · {fxSourceLabel(e.fxSource)} · 5分钟缓存
                {e.fxTime ? ` · ${e.fxTime}` : ''}
              </div>
            </div>
          }>
            <span className="text-[0.6786rem] font-mono text-text-muted cursor-help hidden sm:inline">
              {formatCurrencyMoney(e.currency, e.originalMarketValue)}
            </span>
          </Tooltip>
        ))}
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-[0.7857rem] text-text-muted">总盈亏</span>
        <span className={`text-[0.9286rem] font-mono font-medium ${priceColor(totalPnl)}`}>
          {totalPnl >= 0 ? '+' : ''}{fmtMoney(totalPnl)}
        </span>
        <span className={`text-[0.7857rem] font-mono ${priceColor(totalPnlPct)}`}>
          ({totalPnl >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%)
        </span>
      </div>
      <div className="w-px h-4 bg-border shrink-0" />
      <div className="flex items-center gap-1.5 shrink-0">
        {realizedTotal !== 0 ? (
          <Tooltip content={
            <div className="leading-relaxed">
              <div className="text-text-bright font-semibold mb-1">已实现盈亏拆分</div>
              <div className="font-mono text-[0.7857rem] space-y-0.5">
                {realized.stock !== 0 && (
                  <div>· 股票(已平仓段+分红) <span className={priceColor(realized.stock)}>{realized.stock >= 0 ? '+' : ''}¥{fmtMoney(realized.stock)}</span></div>
                )}
              </div>
            </div>
          }>
            <span className="text-[0.7857rem] text-text-muted cursor-help underline decoration-dotted decoration-text-muted/50 underline-offset-2">已实现盈亏</span>
          </Tooltip>
        ) : (
          <span className="text-[0.7857rem] text-text-muted">已实现盈亏</span>
        )}
        <span className={`text-[0.8571rem] font-mono ${priceColor(realizedTotal)}`}>
          {realizedTotal >= 0 ? '+' : ''}{fmtMoney(realizedTotal)}
        </span>
      </div>
      <div className="w-px h-4 bg-border shrink-0" />
      <div className="flex items-center gap-1.5 shrink-0">
        <span className="text-[0.7857rem] text-text-muted">浮动盈亏</span>
        <span className={`text-[0.8571rem] font-mono ${priceColor(unrealizedPnl)}`}>
          {unrealizedPnl >= 0 ? '+' : ''}{fmtMoney(unrealizedPnl)}
        </span>
      </div>

      {(aValue > 0 || (todayPnl != null && todayPnl !== 0)) && (
        <>
          <div className="w-px h-4 bg-border shrink-0" />
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[0.7857rem] text-text-muted">
              {srvOk ? '今日盈亏' : '今日浮动'}
            </span>
            <span className={`text-[0.9286rem] font-mono font-medium ${priceColor(todayPnl)}`}>
              {todayPnl == null ? '—' : `${todayPnl >= 0 ? '+' : ''}${fmtMoney(todayPnl)}`}
            </span>
          </div>
        </>
      )}


    </div>
  )
}
