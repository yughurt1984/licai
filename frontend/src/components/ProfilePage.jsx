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

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(v * 100 < 1 ? 3 : 2)}%`)
const money = (v) => (v == null ? '—' : `¥${v}`)

/**
 * 个人中心。本期只有「券商费率」两块: 费率档案 CRUD + 费率试算。
 * 资料卡(头像/改密)预留位置, 后续往 children 里加 Section 即可。
 */
export default function ProfilePage() {
  return (
    <div className="space-y-4">
      <BrokerSection />
      <FeeTrialSection />
    </div>
  )
}

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
          <h3 className="text-[13px] font-semibold text-text-bright m-0">券商费率</h3>
          <p className="text-[11px] text-text-dim m-0 mt-0.5">
            用于持仓成本的手续费估算与添加持仓时的税费自动带出; 每个账号各一份, 互不影响。
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
                  <th className="text-right py-1.5 px-3 font-medium">股票费率</th>
                  <th className="text-right py-1.5 px-3 font-medium">最低佣金</th>
                  <th className="text-right py-1.5 px-3 font-medium">ETF费率</th>
                  <th className="text-right py-1.5 px-3 font-medium">最低佣金</th>
                  <th className="text-center py-1.5 px-3 font-medium">默认</th>
                  <th className="text-right py-1.5 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {brokers.map(b => (
                  <tr key={b.id} className="border-b border-border-subtle">
                    <td className="py-1.5 pr-3 text-text">{b.name}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{pct(b.stock_rate)}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.stock_min)}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{pct(b.etf_rate)}</td>
                    <td className="py-1.5 px-3 text-right font-mono">{money(b.etf_min)}</td>
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
  const [stockRate, setStockRate] = useState(broker ? broker.stock_rate * 100 : '')
  const [stockMin, setStockMin] = useState(broker ? broker.stock_min : '')
  const [etfRate, setEtfRate] = useState(broker ? broker.etf_rate * 100 : '')
  const [etfMin, setEtfMin] = useState(broker ? broker.etf_min : '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    if (!name.trim()) return setErr('券商名称必填')
    const body = {
      name: name.trim(),
      stock_rate: parseFloat(stockRate) / 100,
      stock_min: parseFloat(stockMin),
      etf_rate: parseFloat(etfRate) / 100,
      etf_min: parseFloat(etfMin),
    }
    if (!(body.stock_rate >= 0) || !(body.stock_min >= 0) || !(body.etf_rate >= 0) || !(body.etf_min >= 0)) {
      return setErr('费率/最低佣金需为非负数字')
    }
    setBusy(true); setErr('')
    try {
      if (isNew) await fetchJSON('/api/brokers', { method: 'POST', body: JSON.stringify(body) })
      else await fetchJSON(`/api/brokers/${broker.id}`, { method: 'PUT', body: JSON.stringify(body) })
      onDone(isNew ? '已新增' : '已保存')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent w-28'
  const field = (label, value, set, placeholder, step = '0.0001') => (
    <div className="flex flex-col gap-1">
      <label className="text-[10.5px] text-text-muted">{label}</label>
      <input type="number" step={step} min="0" className={inp} placeholder={placeholder}
        value={value} onChange={e => set(e.target.value)} />
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
        {field('股票费率 %', stockRate, setStockRate, '0.025')}
        {field('最低佣金 ¥', stockMin, setStockMin, '5', '0.01')}
        {field('ETF费率 %', etfRate, setEtfRate, '0.01')}
        {field('ETF最低 ¥', etfMin, setEtfMin, '0.1', '0.01')}
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
        费率填百分数(万 2.5 = 0.025); 最低佣金为单笔下限。ETF 走独立费率档(多数券商 ETF 无印花税, 费率更低)。
      </p>
    </div>
  )
}

/* ── 费率试算 ─────────────────────────────────────────────── */

function FeeTrialSection() {
  const [brokers, setBrokers] = useState([])
  const [broker, setBroker] = useState('')
  const [kind, setKind] = useState('stock')      // stock | etf
  const [amount, setAmount] = useState('100000')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchJSON('/api/brokers').then(setBrokers).catch(() => {})
  }, [])

  const run = async () => {
    const amt = parseFloat(amount)
    if (!(amt > 0)) return
    setBusy(true)
    try {
      // 单价×股数拆成后端认可的形态: price=金额, shares=1 (费用对 amount 线性, 拆法不影响结果)
      const q = await fetchJSON(
        `/api/portfolio/fee-estimate?stock_code=${kind === 'etf' ? 'sh510300' : 'sh600000'}` +
        `&price=${amt}&shares=1&broker=${encodeURIComponent(broker)}&action=BUY&kind=${kind}`)
      setResult(q?.fee != null ? q.fee : null)
    } catch { setResult(null) } finally { setBusy(false) }
  }

  const sel = 'bg-bg border border-border rounded px-2 py-1.5 text-[12px] text-text outline-none focus:border-accent'
  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden">
      <div className="px-3 md:px-5 py-3 border-b border-border"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <h3 className="text-[13px] font-semibold text-text-bright m-0">费率试算</h3>
        <p className="text-[11px] text-text-dim m-0 mt-0.5">
          按所选券商的费率档案估算一笔买入的手续费(A股含佣金+过户费+经手费/证管费; ETF 走独立费率档)。
        </p>
      </div>
      <div className="p-3 md:p-4 flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">券商</label>
          <select className={`${sel} w-36`} value={broker} onChange={e => setBroker(e.target.value)}>
            <option value="">默认券商</option>
            {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">品种</label>
          <select className={`${sel} w-24`} value={kind} onChange={e => setKind(e.target.value)}>
            <option value="stock">股票</option>
            <option value="etf">ETF</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">成交金额 ¥</label>
          <input type="number" min="0" className={`${sel} w-36 font-mono`} value={amount}
            onChange={e => setAmount(e.target.value)} />
        </div>
        <button onClick={run} disabled={busy}
          className="px-4 py-1.5 rounded-md bg-accent text-bg text-[12px] font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {busy ? '计算中…' : '试算'}
        </button>
        {result != null && (
          <div className="text-[12px] pb-0.5">
            预估费用 <span className="font-mono text-text-bright">¥{result.toFixed(2)}</span>
            <span className="text-text-dim ml-2">({(result / parseFloat(amount) * 10000).toFixed(2)}‱)</span>
          </div>
        )}
      </div>
    </section>
  )
}
