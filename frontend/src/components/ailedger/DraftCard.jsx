import { useState } from 'react'
import { TYPE_COLOR, TYPE_META } from '../portfolio/constants'
import { saveDraft } from './save'

// AI 识别结果的 editable 卡片: 一条交易一张卡, 字段集随资产类型切换 ——
// 字段与持仓页对应表单(AddAShareForm/AddAssetForm/AddFuturesForm)保持同款语义,
// 但做成紧凑单卡, 方便一次核对 LLM 吐出的多笔。
const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12.5px] text-text font-mono outline-none focus:border-accent w-full'

function Field({ label, title, children }) {
  return (
    <label className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[10px] text-text-muted whitespace-nowrap" title={title}>{label}</span>
      {children}
    </label>
  )
}

function NumField({ label, title, value, onChange, step = '0.0001', placeholder }) {
  return (
    <Field label={label} title={title}>
      <input type="number" step={step} min="0" className={inp} placeholder={placeholder}
        value={value ?? ''} onChange={e => onChange(e.target.value)} />
    </Field>
  )
}

// 买/卖(或开/平) 二选一 chips, 颜色口径同持仓页: 买/开多绿? —— 与 AddFuturesForm 一致:
// 多/买 用 bull, 空/卖 用 bear。
function SideChips({ value, onChange, buyLabel, sellLabel }) {
  return (
    <div className="flex rounded border border-border overflow-hidden h-[26px] self-end">
      {[[buyLabel, 'text-bull'], [sellLabel, 'text-bear']].map(([lbl, tone], i) => {
        const on = (i === 0 ? value === 'BUY' || value === 'OPEN' : value === 'SELL' || value === 'CLOSE')
        return (
          <button key={lbl} type="button" onClick={() => onChange(i === 0)}
            className={`px-2.5 text-[12px] cursor-pointer transition-colors font-medium
              ${on ? `${tone} bg-surface-3 font-semibold` : 'text-text-dim hover:text-text'}`}>
            {lbl}
          </button>
        )
      })}
    </div>
  )
}

export function DraftCard({ draft, ctx, onChange, onRemove }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const d = draft
  const set = (patch) => onChange(d.id, patch)
  const isTradable = ['A', 'F', 'C'].includes(d.type)
  const isDeriv = d.type === 'T' || d.type === 'O'
  const tint = TYPE_COLOR[d.type]

  const save = async () => {
    setBusy(true); setErr('')
    try {
      const msg = await saveDraft(d, ctx)
      onChange(d.id, { state: 'saved', msg })
    } catch (e) {
      onChange(d.id, { state: 'failed' })
      setErr(e.message)
    } finally { setBusy(false) }
  }

  // 已入账: 折叠成一行结果, 留个"撤销状态"入口没必要 —— 持仓页自己能删
  if (d.state === 'saved') {
    return (
      <div className="px-3 py-2 border-b border-border-subtle flex items-center gap-2 text-[12.5px]"
        style={{ background: 'rgba(95,168,108,0.06)' }}>
        <span className="text-bull font-semibold">✓ 已入账</span>
        <span className="text-text">{d.msg}</span>
        <span className="text-text-muted text-[11px] truncate">{d.name || d.code}</span>
        <button onClick={() => onRemove(d.id)}
          className="ml-auto text-[11px] text-text-muted hover:text-text cursor-pointer shrink-0">关闭</button>
      </div>
    )
  }

  const conf = d.confidence != null ? Math.round(d.confidence * 100) : null
  const lowConf = conf != null && conf < 60

  return (
    <div className="px-3 md:px-6 py-2.5 border-b border-border-subtle space-y-2">
      {/* 卡头: 类型徽章 + 名称/代码 + 置信度 + 依据 + 操作 */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="px-1.5 py-0.5 rounded text-[10.5px] font-semibold shrink-0"
          style={{ background: `${tint}1f`, color: tint, border: `1px solid ${tint}55` }}>
          {TYPE_META[d.type]?.label || d.type}{d.type === 'A' ? `·${d.market}` : ''}
        </span>
        <input className="bg-transparent border-b border-transparent hover:border-border-med focus:border-accent
          text-[13px] font-medium text-text-bright outline-none w-40 min-w-0"
          value={d.name} placeholder="名称"
          onChange={e => set({ name: e.target.value })} />
        <span className="text-[10.5px] text-text-dim">核对无误后入账</span>
        {conf != null && (
          <span className={`text-[10.5px] font-mono px-1.5 rounded ${lowConf ? 'text-warn' : 'text-text-muted'}`}
            title="AI 对这条解析的自报把握, <60% 建议逐字段核对">把握 {conf}%</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button onClick={save} disabled={busy}
            className="px-3 py-1 rounded bg-accent text-bg text-[12px] font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '入账中…' : '入账'}
          </button>
          <button onClick={() => onRemove(d.id)}
            className="text-[11px] text-text-muted hover:text-text cursor-pointer">忽略</button>
        </div>
      </div>

      {/* 字段区: 按类型给对应的录入界面 */}
      <div className="flex flex-wrap gap-2 items-end">
        {d.type === 'A' && <>
          <Field label="市场">
            <select className={`${inp} w-20`} value={d.market} onChange={e => set({ market: e.target.value })}>
              <option value="A">A股</option><option value="HK">港股</option><option value="US">美股</option>
            </select>
          </Field>
          <SideChips value={d.side} onChange={buy => set({ side: buy ? 'BUY' : 'SELL' })} buyLabel="买入" sellLabel="卖出" />
          <Field label="代码">
            <input className={`${inp} w-24`} value={d.code} onChange={e => set({ code: e.target.value })} placeholder="600362" />
          </Field>
          <NumField label={d.market === 'US' ? '价格 $' : d.market === 'HK' ? '价格 HK$' : '价格 ¥'} value={d.price} onChange={v => set({ price: v })} />
          <NumField label="数量" value={d.shares} onChange={v => set({ shares: v })} step="0.0001" />
          <NumField label="手续费 ¥" value={d.fee} onChange={v => set({ fee: v })} step="0.01" placeholder="自动" />
        </>}

        {d.type === 'F' && <>
          <Field label="基金类型">
            <select className={`${inp} w-28`} value={d.fundKind} onChange={e => set({ fundKind: e.target.value })}>
              <option value="open">开放式基金</option><option value="etf">ETF(场内)</option>
            </select>
          </Field>
          <SideChips value={d.side} onChange={buy => set({ side: buy ? 'BUY' : 'SELL' })} buyLabel="申购" sellLabel="赎回" />
          <Field label="代码">
            <input className={`${inp} w-24`} value={d.code} onChange={e => set({ code: e.target.value })} placeholder="161226" />
          </Field>
          <NumField label="净值/单价" value={d.price} onChange={v => set({ price: v })} />
          <NumField label="份额" value={d.shares} onChange={v => set({ shares: v })} />
          <NumField label="金额 ¥" title="没识别出份额时按金额入账(场外 T+1 待确认)" value={d.amount} onChange={v => set({ amount: v })} step="0.01" />
          <NumField label="手续费 ¥" value={d.fee} onChange={v => set({ fee: v })} step="0.01" />
        </>}

        {d.type === 'C' && <>
          <SideChips value={d.side} onChange={buy => set({ side: buy ? 'BUY' : 'SELL' })} buyLabel="买入" sellLabel="卖出" />
          <Field label="币对">
            <input className={`${inp} w-32`} value={d.code} onChange={e => set({ code: e.target.value })} placeholder="BTC-USDT" />
          </Field>
          <NumField label="单价 USDT" value={d.price} onChange={v => set({ price: v })} />
          <NumField label="数量" value={d.shares} onChange={v => set({ shares: v })} />
          <NumField label="金额 USDT" value={d.amount} onChange={v => set({ amount: v })} step="0.01" />
          <NumField label="手续费" value={d.fee} onChange={v => set({ fee: v })} step="0.01" />
        </>}

        {(d.type === 'W' || d.type === 'M') && <>
          <Field label={d.type === 'W' ? '产品名称' : '账户名称'}>
            <input className={`${inp} w-40`} value={d.name} onChange={e => set({ name: e.target.value })} placeholder={d.type === 'W' ? '招商月添利' : '余额宝'} />
          </Field>
          <Field label="平台/银行">
            <input className={`${inp} w-28`} value={d.broker} onChange={e => set({ broker: e.target.value })} placeholder="招商银行" />
          </Field>
          <NumField label={d.type === 'W' ? '投入本金 ¥' : '当前余额 ¥'} value={d.amount} onChange={v => set({ amount: v })} step="0.01" />
          <NumField label="年化 %" value={d.annualYieldPct} onChange={v => set({ annualYieldPct: v })} step="0.001" />
        </>}

        {d.type === 'R' && <>
          <Field label="策略名称">
            <input className={`${inp} w-40`} value={d.name} onChange={e => set({ name: e.target.value })} placeholder="OKX BTC 现货马丁" />
          </Field>
          <Field label="平台">
            <input className={`${inp} w-24`} value={d.broker} onChange={e => set({ broker: e.target.value })} placeholder="OKX" />
          </Field>
          <NumField label="投入本金 ¥" value={d.amount} onChange={v => set({ amount: v })} step="0.01" />
          <NumField label="当前资产 ¥" value={d.currentValue} onChange={v => set({ currentValue: v })} step="0.01" />
        </>}

        {isDeriv && <>
          <Field label="品种代码">
            <input className={`${inp} w-20`} value={d.code} onChange={e => set({ code: e.target.value.toUpperCase() })} placeholder="CU" />
          </Field>
          <Field label="方向" title="多=买涨, 空=卖跌">
            <select className={`${inp} w-16`} value={d.direction} onChange={e => set({ direction: e.target.value })}>
              <option value="LONG">多</option><option value="SHORT">空</option>
            </select>
          </Field>
          <SideChips value={d.side} onChange={open => set({ side: open ? 'OPEN' : 'CLOSE' })} buyLabel="开仓" sellLabel="平仓" />
          <Field label="合约月份">
            <input className={`${inp} w-20`} value={d.month} onChange={e => set({ month: e.target.value })} placeholder="2601" />
          </Field>
          <NumField label="手数" value={d.shares} onChange={v => set({ shares: v })} step="1" />
          <NumField label="每手乘数" value={d.multiplier} onChange={v => set({ multiplier: v })} step="1" />
          <NumField label="价格 ¥" value={d.price} onChange={v => set({ price: v })} />
          <NumField label="手续费 ¥" value={d.fee} onChange={v => set({ fee: v })} step="0.01" placeholder="自动" />
        </>}

        {/* 公共尾字段: 券商 + 日期 + 时刻 */}
        {!['W', 'M', 'R'].includes(d.type) && (
          <Field label={isDeriv ? '交易商' : '券商'}>
            <input className={`${inp} w-24`} value={d.broker} onChange={e => set({ broker: e.target.value })} placeholder="可空" />
          </Field>
        )}
        <Field label="日期">
          <input type="date" className={`${inp} w-36`} value={d.tradeDate || ''} onChange={e => set({ tradeDate: e.target.value })} />
        </Field>
        {isTradable && (
          <Field label="时刻">
            <input type="time" className={`${inp} w-28 text-text-dim`} value={d.tradeTime || ''} onChange={e => set({ tradeTime: e.target.value })} />
          </Field>
        )}
      </div>

      {/* 依据 + 错误 */}
      {(d.evidence || err || d.state === 'failed') && (
        <div className="flex flex-col gap-0.5">
          {d.evidence && (
            <div className="text-[10.5px] text-text-muted font-mono truncate" title={d.evidence}>
              依据: {d.evidence}
            </div>
          )}
          {err && <div className="text-[11.5px] text-bear">✗ {err}</div>}
        </div>
      )}
    </div>
  )
}
