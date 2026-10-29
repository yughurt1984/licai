import { useState } from 'react'
import { fetchJSON } from '../../../hooks/useApi'

// 期货/期权行的 加仓(同向 OPEN) / 平仓(CLOSE) 内联表单。
// 复用持仓流水端点 POST /api/portfolio/{code}/actions (OPEN/CLOSE + direction + 乘数)。
export function FuturesLotRow({ holding, mode, onDone, onCancel }) {
  const isClose = mode === 'close'
  const deriv = holding.asset_kind === 'opt' ? 'opt' : 'fut'
  const direction = holding.direction || 'LONG'
  const [price, setPrice] = useState('')
  const [lots, setLots] = useState(isClose ? String(holding.shares || '') : '1')
  const [fee, setFee] = useState('')
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [tradeTime, setTradeTime] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    const px = parseFloat(price)
    if (!(px > 0)) return setErr('请填价格')
    const sh = parseFloat(lots)
    if (!(sh > 0)) return setErr('手数必须大于 0')
    if (isClose && holding.shares && sh > holding.shares) return setErr(`手数不能超过持仓 ${holding.shares} 手`)
    setBusy(true); setErr('')
    try {
      const body = {
        action_type: isClose ? 'CLOSE' : 'OPEN',
        price: px,
        shares: sh,
        trade_date: tradeDate,
        direction,
        contract_multiplier: holding.contract_multiplier || 1,
      }
      if (tradeTime.trim()) body.trade_time = tradeTime.trim()
      if (fee !== '' && parseFloat(fee) >= 0) body.fee = parseFloat(fee)
      await fetchJSON(`/api/portfolio/${encodeURIComponent(holding.stock_code)}/actions`, {
        method: 'POST', body: JSON.stringify(body),
      })
      onDone(isClose ? '平仓成功' : '加仓成功')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[12.5px] text-text font-mono outline-none focus:border-accent w-24'
  const field = (label, value, set, placeholder, step = '0.01', type = 'number') => (
    <div className="flex flex-col gap-1">
      <label className="text-[10.5px] text-text-muted">{label}</label>
      <input type={type} step={step} min="0" className={inp} placeholder={placeholder}
        value={value} onChange={e => set(e.target.value)} />
    </div>
  )

  return (
    <div className="rounded-lg border border-border-med bg-surface-2/60 p-3 fade-up">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">操作</label>
          <span className={`text-[12.5px] font-semibold ${direction === 'LONG' ? 'text-bull' : 'text-bear'}`}>
            {isClose ? '平仓' : '加仓'} · {holding.stock_name || holding.stock_code}
            <span className="text-text-dim font-normal"> ({direction === 'LONG' ? '多' : '空'} · 现持 {holding.shares} 手)</span>
          </span>
        </div>
        {field(isClose ? '平仓价 ¥' : '加仓价 ¥', price, setPrice, '现价')}
        {field('手数', lots, setLots, '1', '1')}
        {field('手续费 ¥', fee, setFee, '自动', '0.01')}
        {field('日期', tradeDate, setTradeDate, '', undefined, 'date')}
        {field('时刻', tradeTime, setTradeTime, 'HH:MM', undefined, 'time')}
        <div className="flex gap-2 pb-0.5">
          <button onClick={submit} disabled={busy}
            className="px-4 py-1.5 rounded-md bg-accent text-bg text-[12.5px] font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '提交中…' : '提交'}
          </button>
          <button onClick={onCancel}
            className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12.5px] hover:text-text cursor-pointer">取消</button>
        </div>
      </div>
      {err && <p className="text-[12px] text-bear m-0">{err}</p>}
    </div>
  )
}
