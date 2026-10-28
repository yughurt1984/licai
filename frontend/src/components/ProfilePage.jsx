import { useCallback, useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { clearBrokersCache } from '../helpers'

// 券商档案写操作广播: 个人中心保存后, 持仓页/流水页(挂载时 loadBrokers 一次且带
// 模块级缓存)立即清缓存重拉, 不用等下次进入页面。
export const BROKERS_EVENT = 'licai:brokers-changed'

function notifyBrokersChanged() {
  clearBrokersCache()
  try { window.dispatchEvent(new CustomEvent(BROKERS_EVENT)) } catch {}
}

const money = (v, ccy = '¥') => (v == null ? '—' : `${ccy}${v}`)
// 类型与货币单位统一: A股/期货 ¥, 港股 HK$, 美股 $, Crypto U(USDT)
const CCY_OF = { 'A股': '¥', '港股': 'HK$', '美股': '$', '期货': '¥', 'Crypto': 'U' }
// 计价单位: 笔/股/张/%。费率原值存储, 显示直接拼接: 0.002 + % → "0.002%", 0.1 + 张 → "0.1/张"
const BROKER_UNITS = ['笔', '股', '张', '%']
const rateCell = (rate, unit) => {
  if (rate == null || rate === 0) return '—'
  return unit === '%' ? `${rate}%` : `${rate}/${unit || '笔'}`
}

/**
 * 个人中心。「券商费率」: 费率档案 CRUD(类型 + 股票/ETF/期权费率 + 平台使用费)。
 * 资料卡(头像/改密)预留位置, 后续往 children 里加 Section 即可。
 */
export default function ProfilePage() {
  return (
    <div className="space-y-4">
      <BrokerSection />
    </div>
  )
}

export const BROKER_TYPES = ['A股', '港股', '美股', '期货', 'Crypto']

/* ── 券商费率档案 ─────────────────────────────────────────── */

function BrokerSection() {
  const [brokers, setBrokers] = useState(null)
  const [editing, setEditing] = useState(null)      // null | 'new' | broker对象
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })

  // 绕过 loadBrokers 的模块缓存: 这里必须是最新档案
  const load = useCallback(() => fetchJSON('/api/brokers'), [])
  useEffect(() => {
    load().then(d => setBrokers(d)).catch(() => setBrokers([]))
  }, [load])

  const done = (text, ok = true) => {
    setStatus({ text, ok })
    setEditing(null)
    setBusy('')
    load()
    notifyBrokersChanged()
  }

  const remove = async (b) => {
    if (!confirm(`删除券商「${b.name}」？相关持仓的手续费估算将回退默认券商。`)) return
    setBusy(`del-${b.id}`)
    try {
      await fetchJSON(`/api/brokers/${b.id}`, { method: 'DELETE' })
      done('已删除')
    } catch (e) { setStatus({ text: e.message, ok: false }); setBusy('') }
  }

  const setDefault = async (b) => {
    setBusy(`def-${b.id}`)
    try {
      await fetchJSON(`/api/brokers/${b.id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      done(`已把「${b.name}」设为默认`)
    } catch (e) { setStatus({ text: e.message, ok: false }); setBusy('') }
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden">
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-center justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div>
          <h3 className="text-[13px] font-semibold text-text-bright m-0">券商费率配置</h3>
          <p className="text-[11px] text-text-dim m-0 mt-0.5">
            货币: A股/期货 ¥, 港股 HK$, 美股 $, Crypto U。
          </p>
        </div>
        {editing === null && (
          <button onClick={() => setEditing('new')}
            className="px-3 py-1 rounded-md border border-accent/40 bg-accent/10 text-accent text-[12px] hover:bg-accent/20 transition-colors cursor-pointer">
            + 新增券商
          </button>
        )}
      </div>

      <div className="p-3 md:p-4 space-y-3">
        {status.text && (
          <p className={`text-[12px] ${status.ok ? 'text-bull' : 'text-bear'}`}>{status.text}</p>
        )}

        {brokers === null ? (
          <div className="text-[12px] text-text-dim py-2">加载中…</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] border-collapse">
              <thead>
                <tr className="text-text-dim text-[11px] border-b border-border">
                  <th className="text-left py-1.5 pr-3 font-medium">券商</th>
                  <th className="text-left py-1.5 pr-3 font-medium">类型</th>
                  <th className="text-right py-1.5 px-3 font-medium">交易费率</th>
                  <th className="text-right py-1.5 px-3 font-medium">最低佣金</th>
                  <th className="text-right py-1.5 px-3 font-medium">基金费率</th>
                  <th className="text-right py-1.5 px-3 font-medium">最低佣金</th>
                  <th className="text-right py-1.5 px-3 font-medium">期权费</th>
                  <th className="text-right py-1.5 px-3 font-medium">最低期权费</th>
                  <th className="text-right py-1.5 px-3 font-medium">平台费</th>
                  <th className="text-right py-1.5 px-3 font-medium">最低平台费</th>
                  <th className="text-center py-1.5 px-3 font-medium">默认</th>
                  <th className="text-right py-1.5 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {brokers.map(b => (
                  <tr key={b.id} className="border-b border-border-subtle">
                    <td className="py-1.5 pr-3 text-text">{b.name}</td>
                    <td className="py-1.5 pr-3 text-text-dim">{b.type || 'A股'}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{rateCell(b.stock_rate, b.stock_unit)}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.stock_min, CCY_OF[b.type || 'A股'])}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{b.etf_rate == null ? '同股票' : rateCell(b.etf_rate, b.etf_unit)}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.etf_min, CCY_OF[b.type || 'A股'])}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{rateCell(b.opt_rate, b.opt_unit)}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.opt_min, CCY_OF[b.type || 'A股'])}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.platform_fee, CCY_OF[b.type || 'A股'])}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.platform_min, CCY_OF[b.type || 'A股'])}</td>
                    <td className="py-1.5 px-3 text-center">
                      {b.is_default ? <span className="text-accent">★ 默认</span> : ''}
                    </td>
                    <td className="py-1.5 text-right whitespace-nowrap">
                      <button onClick={() => setEditing(b)} disabled={busy !== ''}
                        className="text-text-dim hover:text-accent transition-colors cursor-pointer px-1.5">编辑</button>
                      {!b.is_default && (
                        <button onClick={() => setDefault(b)} disabled={busy !== ''}
                          className="text-text-dim hover:text-accent transition-colors cursor-pointer px-1.5">
                          {busy === `def-${b.id}` ? '…' : '设默认'}
                        </button>
                      )}
                      {!b.is_default && (
                        <button onClick={() => remove(b)} disabled={busy !== ''}
                          className="text-text-dim hover:text-bear transition-colors cursor-pointer px-1.5">
                          {busy === `del-${b.id}` ? '…' : '删除'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {editing !== null && (
          <BrokerEditForm
            broker={editing === 'new' ? null : editing}
            onDone={(msg) => done(msg)}
            onCancel={() => { setEditing(null); setStatus({ text: '', ok: null }) }} />
        )}
      </div>
    </section>
  )
}

function BrokerEditForm({ broker, onDone, onCancel }) {
  const isNew = !broker
  const [name, setName] = useState(broker?.name || '')
  const [btype, setBtype] = useState(broker?.type || 'A股')
  const [stockRate, setStockRate] = useState(broker ? broker.stock_rate : '')
  const [stockUnit, setStockUnit] = useState(broker?.stock_unit || '%')
  const [stockMin, setStockMin] = useState(broker ? broker.stock_min : '')
  const [etfRate, setEtfRate] = useState(broker && broker.etf_rate != null ? broker.etf_rate : '')
  const [etfUnit, setEtfUnit] = useState(broker?.etf_unit || '%')
  const [etfMin, setEtfMin] = useState(broker ? (broker.etf_min ?? '') : '')
  const [optRate, setOptRate] = useState(broker ? (broker.opt_rate || '') : '')   // 期权费金额(按 opt_unit)
  const [optUnit, setOptUnit] = useState(broker?.opt_unit || '张')
  const [optMin, setOptMin] = useState(broker ? (broker.opt_min || '') : '')      // 最低期权费
  const [platformFee, setPlatformFee] = useState(broker ? (broker.platform_fee || '') : '')
  const [platformMin, setPlatformMin] = useState(broker ? (broker.platform_min || '') : '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 基金费率/单位/最低默认持续跟随股票费率/单位/最低佣金(输入股票值即自动复制);
  // 用户手改过对应基金字段后停止跟随。已有档案存过显式基金值时视为手改, 不覆盖。
  const [etfTouched, setEtfTouched] = useState(!!(broker && broker.etf_rate != null))
  const [etfUnitTouched, setEtfUnitTouched] = useState(!!(broker && broker.etf_unit))
  const [etfMinTouched, setEtfMinTouched] = useState(!!(broker && broker.etf_min))
  useEffect(() => {
    if (!etfTouched && stockRate !== '') setEtfRate(stockRate)
  }, [stockRate, etfTouched])
  useEffect(() => {
    if (!etfUnitTouched) setEtfUnit(stockUnit)
  }, [stockUnit, etfUnitTouched])
  useEffect(() => {
    if (!etfMinTouched && stockMin !== '') setEtfMin(stockMin)
  }, [stockMin, etfMinTouched])

  const submit = async () => {
    if (!name.trim()) return setErr('券商名称必填')
    const body = {
      name: name.trim(),
      type: btype,
      stock_rate: parseFloat(stockRate),
      stock_unit: stockUnit,
      stock_min: parseFloat(stockMin) || 0,
      etf_rate: etfRate === '' ? null : parseFloat(etfRate),   // 空 = 同股票费率
      etf_unit: etfUnit,
      etf_min: parseFloat(etfMin) || 0,
      opt_rate: parseFloat(optRate) || 0,                            // 期权费金额(按单位)
      opt_unit: optUnit,
      opt_min: parseFloat(optMin) || 0,
      platform_fee: parseFloat(platformFee) || 0,
      platform_min: parseFloat(platformMin) || 0,
    }
    // 股票费率/期权费必填(可填 0); 基金费率可留空(=同股票费率); 其余最低值可留空按 0 计
    if (![body.stock_rate, body.opt_rate].every(v => v >= 0)) {
      return setErr('股票费率/期权费必填(非负数字, 可为0); 基金费率留空=同股票费率; 各最低值可留空按0计')
    }
    setBusy(true); setErr('')
    try {
      if (isNew) await fetchJSON('/api/brokers', { method: 'POST', body: JSON.stringify(body) })
      else await fetchJSON(`/api/brokers/${broker.id}`, { method: 'PUT', body: JSON.stringify(body) })
      onDone(isNew ? '已新增' : '已保存')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent w-24'
  const ccy = CCY_OF[btype] || '¥'
  const field = (label, value, set, placeholder, step = '0.0001', onInput) => (
    <div className="flex flex-col gap-1">
      <label className="text-[10.5px] text-text-muted">{label}</label>
      <input type="number" step={step} min="0" className={inp} placeholder={placeholder}
        value={value} onChange={e => { set(e.target.value); onInput?.() }} />
    </div>
  )
  const unitField = (label, value, set, onInput) => (
    <div className="flex flex-col gap-1">
      <label className="text-[10.5px] text-text-muted">{label}</label>
      <select className={`${inp} font-sans`} value={value}
        onChange={e => { set(e.target.value); onInput?.() }}>
        {BROKER_UNITS.map(u => <option key={u} value={u}>{u}</option>)}
      </select>
    </div>
  )

  return (
    <div className="rounded-lg border border-border-med bg-surface-2/60 p-3 space-y-3 fade-up">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">券商名称</label>
          <input className={`${inp} w-32 font-sans`} placeholder="招商证券" value={name}
            onChange={e => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">类型</label>
          <select className={`${inp} w-24 font-sans`} value={btype} onChange={e => setBtype(e.target.value)}>
            {BROKER_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        {field(`股票费率`, stockRate, setStockRate, '0.025')}
        {unitField('单位', stockUnit, setStockUnit)}
        {field(`最低佣金 ${ccy}`, stockMin, setStockMin, '5', '0.01')}
        {field(`基金费率`, etfRate, setEtfRate, '同股票费率', '0.0001', () => setEtfTouched(true))}
        {unitField('单位', etfUnit, setEtfUnit, () => setEtfUnitTouched(true))}
        {field(`最低佣金 ${ccy}`, etfMin, setEtfMin, '0.1', '0.01', () => setEtfMinTouched(true))}
        {field(`期权费 ${ccy}`, optRate, setOptRate, '0.6', '0.01')}
        {unitField('单位', optUnit, setOptUnit)}
        {field(`最低期权费 ${ccy}`, optMin, setOptMin, '0', '0.01')}
        {field(`平台费 ${ccy}`, platformFee, setPlatformFee, '0', '0.01')}
        {field(`最低平台费 ${ccy}`, platformMin, setPlatformMin, '0', '0.01')}
        <div className="flex gap-2 pb-0.5">
          <button onClick={submit} disabled={busy}
            className="px-4 py-1.5 rounded-md bg-accent text-bg text-[12px] font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '保存中…' : (isNew ? '新增' : '保存')}
          </button>
          <button onClick={onCancel}
            className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">取消</button>
        </div>
      </div>
      {err && <p className="text-[12px] text-bear m-0">{err}</p>}
      <p className="text-[10.5px] text-text-muted m-0">
        股票费率/期权费必填; 费率值原样保存并拼接单位显示, 如 0.002 + % 显示 0.002%、0.1 + 张 显示 0.1/张;
        单位可选 笔/股/张/%; 基金费率/单位/最低佣金留空时自动跟随股票对应值, 手改后独立;
        各最低值与平台费可留空按 0 计, 金额按类型显示货币单位(A股/期货 ¥, 港股 HK$, 美股 $, Crypto U)。
      </p>
    </div>
  )
}

