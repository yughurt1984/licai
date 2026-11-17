import { useState } from 'react'
import { fmtMoney, fmtPct, priceColor } from '../../helpers'
import Tooltip from '../Tooltip'
import { FxHint } from './atoms'

// ============================================================
// Summary strip
// ============================================================
export function SummaryStrip({ agg, realized, todayPnl, scope = 'ALL' }) {
  const fxExposure = agg.fxExposure || []
  const realizedTotal = realized?.stock || 0
  const grandPnl = (agg.totalPnl || 0) + realizedTotal
  const items = [
    {
      label: '总资产',
      val: `¥${fmtMoney(agg.totalMv)}`,
      big: true,
      color: 'text-text-bright',
    },
    {
      label: '总盈亏',
      val: `${grandPnl >= 0 ? '+' : ''}${fmtMoney(grandPnl)}`,
      color: priceColor(grandPnl),
      sub: agg.totalCost > 0 ? `(${fmtPct(grandPnl / agg.totalCost * 100)})` : '',
    },
    {
      label: '已实现盈亏',
      val: `${realizedTotal >= 0 ? '+' : ''}${fmtMoney(realizedTotal)}`,
      color: priceColor(realizedTotal),
      tooltip: realizedTotal !== 0 ? (
        <div className="leading-relaxed">
          <div className="text-text-bright font-semibold mb-1">已实现盈亏拆分</div>
          <div className="font-mono text-[0.7857rem] space-y-0.5">
            {realized?.stock !== 0 && (
              <div>· 股票(已平仓段+分红) <span className={priceColor(realized.stock)}>{realized.stock >= 0 ? '+' : ''}¥{fmtMoney(realized.stock)}</span></div>
            )}
          </div>
        </div>
      ) : null,
    },
    {
      label: '浮动盈亏',
      val: `${(agg.totalPnl || 0) >= 0 ? '+' : ''}${fmtMoney(agg.totalPnl || 0)}`,
      color: priceColor(agg.totalPnl),
    },
    (() => {
      // 当日盈亏优先用后端券商口径(含今日清仓的已实现 + 今日新建仓以买入价为基准);
      // 端点不可用时退回前端的纯市值变动口径, 并把标签退回「今日浮动」以免口径不符。
      // scope 非 ALL(如期货 tab)时不用后端全组合 total —— 用前端分组口径, 显示该类自己的今日盈亏。
      const useSrv = scope === 'ALL' && todayPnl && typeof todayPnl.total === 'number'
      const v = useSrv ? todayPnl.total : agg.totalToday
      const closedN = useSrv ? (todayPnl.items || []).filter(i => i.closed_today).length : 0
      const openedN = useSrv ? (todayPnl.items || []).filter(i => i.opened_today).length : 0
      const est = useSrv ? (todayPnl.estimated_part || 0) : 0
      const unk = useSrv ? (todayPnl.unknown || []) : []
      return {
        label: useSrv ? '今日盈亏' : '今日浮动',
        val: `${v >= 0 ? '+' : ''}${fmtMoney(v)}`,
        color: priceColor(v),
        note: useSrv && (closedN || openedN)
          ? [closedN ? `含今日清仓 ${closedN} 笔` : '', openedN ? `新建 ${openedN} 笔` : '']
              .filter(Boolean).join(' · ')
          : null,
        tooltip: useSrv ? (
          <div className="space-y-1.5">
            <div>当日盈亏 = 现市值 + 今日卖出所得 − 昨收市值 − 今日买入成本</div>
            <div className="text-text-dim">
              今日清仓的已实现算在内; 今天新建的仓以买入价(而非昨收)为基准。金额含手续费。
            </div>
            {(todayPnl.items || []).filter(i => i.today_pnl != null && Math.abs(i.today_pnl) >= 1)
              .slice(0, 8).map(i => (
              <div key={i.code} className="flex gap-2 justify-between font-mono text-[0.8214rem]">
                <span className="text-text-dim">
                  {i.name?.slice(0, 14) || i.code}
                  {i.closed_today ? ' 清仓' : i.opened_today ? ' 新建' : ''}
                  {i.estimated ? ' 估' : ''}
                </span>
                <span className={priceColor(i.today_pnl)}>
                  {i.today_pnl >= 0 ? '+' : ''}{fmtMoney(i.today_pnl)}
                </span>
              </div>
            ))}
            {est !== 0 && (
              <div className="text-text-muted text-[0.7857rem]">
                其中 {fmtMoney(est)} 来自净值 T+1 的场外基金, 按底层代理估, 净值公布后修正
              </div>
            )}
            {unk.length > 0 && (
              <div className="text-text-muted text-[0.7857rem]">
                {unk.length} 只净值滞后且无代理标的, 未计入(不冒充)
              </div>
            )}
          </div>
        ) : null,
      }
    })(),
  ]
  return (
    <div className="flex gap-4 md:gap-7 items-baseline flex-wrap">
      {items.map((it, i) => {
        const valueSpan = (
          <span className="inline-flex items-baseline gap-1 md:gap-1.5 flex-wrap">
            <span className={`font-mono font-bold tabular-nums ${it.color} ${it.big ? 'text-[1.3571rem] md:text-[1.6429rem]' : 'text-[1.0714rem] md:text-[1.1429rem]'} ${it.tooltip ? 'cursor-help underline decoration-dotted decoration-text-muted/60 underline-offset-4' : ''}`}
              style={{ letterSpacing: '-.01em' }}>{it.val}</span>
            {it.sub && <span className={`font-mono text-[0.8214rem] md:text-[0.8571rem] opacity-80 ${it.color}`}>{it.sub}</span>}
          </span>
        )
        return (
        <div key={i} className="flex flex-col gap-0.5">
          <span className="text-[0.8214rem] text-text-dim tracking-wide">{it.label}</span>
          {it.tooltip ? <Tooltip content={it.tooltip}>{valueSpan}</Tooltip> : valueSpan}
          {/* 总资产下方: 口径说明 + 折算所用币种汇率收进同一行(原币金额→折算额的 pill 太长,
              且折算额和上面大数字重复)。悬停看汇率来源/时点, 同 FxHint。 */}
          {i === 0 && (
            <span className="flex items-center flex-wrap gap-x-1.5 text-[0.75rem] text-text-muted">
              <span>人民币口径</span>
              {fxExposure.map(e => (
                <FxHint key={e.currency} extra={{ currency: e.currency, fxRate: e.fxRate, fxTime: e.fxTime, fxSource: e.fxSource }}>
                  <span className="font-mono cursor-help underline decoration-dotted decoration-text-muted/50 underline-offset-2">
                    {e.currency} {Number(e.fxRate || 1).toFixed(4)}
                  </span>
                </FxHint>
              ))}
            </span>
          )}
          {i !== 0 && it.note && <span className="text-[0.75rem] text-text-muted">{it.note}</span>}
        </div>
        )
      })}
    </div>
  )
}

// 已清仓股票区块 — 列出持仓已删但还有交易历史的标的, 看到累计已实现盈亏 + 翻历史
export function ClosedPositionsBlock({ items, onHistory, onKline }) {
  const [open, setOpen] = useState(false)
  if (!items || items.length === 0) return null
  const total = items.reduce((s, it) => s + (it.realized_pnl || 0), 0)
  return (
    <div className="px-3 md:px-6 py-2 border-t border-border-subtle bg-surface/40">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between text-[0.8929rem] text-text-dim hover:text-text cursor-pointer transition-colors">
        <span className="flex items-baseline gap-2">
          <span>已清仓 {items.length} 笔</span>
          <span className="text-text-muted text-[0.8214rem]">流水保留, 不在主列表显示</span>
        </span>
        <span className="flex items-baseline gap-2">
          <span className={priceColor(total)}>
            累计 {total >= 0 ? '+' : ''}¥{fmtMoney(total)}
          </span>
          <span className="text-text-muted text-[0.7857rem]">{open ? '▾' : '▸'}</span>
        </span>
      </button>
      {open && (
        <div className="mt-2 pt-2 border-t border-border-subtle space-y-1">
          {items.map(it => (
            <div key={it.stock_code}
              className="flex items-baseline justify-between text-[0.8929rem] py-1 px-1 rounded hover:bg-surface-2 transition-colors">
              <span className="flex items-baseline gap-2 min-w-0">
                <span className="text-text-bright truncate">{it.stock_name || '--'}</span>
                <span className="font-mono text-[0.7857rem] text-text-muted">{it.stock_code}</span>
              </span>
              <span className="flex items-baseline gap-3 shrink-0">
                <span className={`font-mono ${priceColor(it.realized_pnl)}`}>
                  {it.realized_pnl >= 0 ? '+' : ''}¥{fmtMoney(it.realized_pnl)}
                </span>
                {/* A股清仓 / 场内ETF清仓: 有K线; BS标记按保留的流水照常标出买卖点 */}
                <button onClick={() => onKline?.({
                  stock_code: it.code || it.stock_code, stock_name: it.stock_name,
                })}
                  className="text-[0.8214rem] text-text-dim hover:text-accent cursor-pointer">
                  K线
                </button>
                <button onClick={() => onHistory?.({ stock_code: it.stock_code, stock_name: it.stock_name })}
                  className="text-[0.8214rem] text-text-dim hover:text-accent cursor-pointer">
                  流水
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
