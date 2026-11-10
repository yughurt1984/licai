import React, { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '../../../hooks/useApi'

// FixIncomeEditRow — 固定收益资产行内编辑 (UnifiedPortfolio 行「改」入口)。
// 本金不在这里改(由流水重算); 手动市值清空 = 恢复年化计提估算。
// 期限 ↔ 到期日互推与 AddFixIncomeForm 同一约定: 手填过哪头就不动哪头。

const INTEREST_TYPES = [
  { v: 'bullet', label: '到期一次付息' },
  { v: 'monthly', label: '按月付息' },
  { v: 'quarterly', label: '按季付息' },
  { v: 'semiannual', label: '半年付息' },
  { v: 'annual', label: '每年付息' },
]
const STATUSES = [
  { v: 'holding', label: '在持' },
  { v: 'matured', label: '已到期兑付' },
  { v: 'closed', label: '已结清' },
]

export function FixIncomeEditRow({ asset, onDone, onCancel }) {
  const q = asset.quote || {}
  const [code, setCode] = useState(asset.code || '')
  const [name, setName] = useState(asset.name || '')
  const [platform, setPlatform] = useState(asset.platform || '')
  // 手动市值: DB 有值就显示, 清空提交 null = 恢复年化计提
  const [manualValue, setManualValue] = useState(asset.manual_value != null ? String(asset.manual_value) : '')
  const [yieldPct, setYieldPct] = useState(
    asset.annual_yield_rate != null ? String(+(asset.annual_yield_rate * 100).toFixed(4)) : '')
  const [valueDate, setValueDate] = useState(asset.value_date || '')
  const [maturityDate, setMaturityDate] = useState(asset.maturity_date || '')
  const [termDays, setTermDays] = useState(asset.term_days != null ? String(asset.term_days) : '')
  const [interestType, setInterestType] = useState(asset.interest_type || 'bullet')
  const [riskLevel, setRiskLevel] = useState(asset.risk_level || '')
  const [status, setStatus] = useState(asset.status || 'holding')
  const [note, setNote] = useState(asset.note || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const termTouched = useRef(false)
  const maturityTouched = useRef(false)
  const rootRef = React.useRef(null)
  useEffect(() => { rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }, [])

  // 期限 → 到期日 (到期日没手填过)
  useEffect(() => {
    const t = parseInt(termDays)
    if (maturityTouched.current || !t || t <= 0 || !valueDate) return
    const d = new Date(`${valueDate}T00:00:00`)
    d.setDate(d.getDate() + t)
    setMaturityDate(d.toISOString().slice(0, 10))
  }, [termDays, valueDate])
  // 到期日 → 期限 (期限没手填过)
  useEffect(() => {
    if (termTouched.current || !maturityDate || !valueDate) return
    const diff = Math.round((new Date(`${maturityDate}T00:00:00`) - new Date(`${valueDate}T00:00:00`)) / 86400000)
    if (Number.isFinite(diff) && diff > 0) setTermDays(String(diff))
  }, [maturityDate, valueDate])

  const save = async () => {
    if (!String(name).trim()) { setErr('名称必填'); return }
    setBusy(true); setErr('')
    try {
      await fetchJSON(`/api/fix-income/${asset.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          code: String(code).trim() || String(name).trim(),
          name: String(name).trim(),
          platform: String(platform).trim(),
          manual_value: manualValue === '' ? null : parseFloat(manualValue),
          annual_yield_rate: yieldPct === '' ? null : parseFloat(yieldPct) / 100,
          value_date: valueDate || null,
          maturity_date: maturityDate || null,
          term_days: termDays === '' ? null : parseInt(termDays),
          interest_type: interestType,
          risk_level: riskLevel || null,
          status,
          note: String(note).trim(),
        }),
      })
      onDone?.()
    } catch (e) {
      setErr(String(e?.message || e))
    } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1 text-[12px] text-text font-mono outline-none focus:border-accent'
  const lbl = 'text-[12px] text-text-dim whitespace-nowrap'
  const col = 'flex flex-col gap-1'
  return (
    <div ref={rootRef}
      className="px-6 py-3 border-b-2 border-accent bg-accent/5 flex flex-wrap gap-3 items-end text-[12.5px]"
      style={{ animation: 'fade-up 0.2s ease-out' }}>
      <span className="text-[11px] text-accent font-semibold basis-full">
        ✎ 编辑固定收益 <span className="text-text-bright">{asset.name}</span>
        <span className="text-text-dim font-normal ml-2">
          本金 ¥{(asset.principal || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })} (由流水重算, 不在此改)
        </span>
      </span>

      <div className={col}>
        <label className={lbl}>产品/债券名称</label>
        <input value={name} onChange={e => setName(e.target.value)} className={`${inp} w-40`} />
      </div>
      <div className={col}>
        <label className={lbl}>代码</label>
        <input value={code} onChange={e => setCode(e.target.value)} className={`${inp} w-28`} />
      </div>
      <div className={col}>
        <label className={lbl}>平台/发行机构</label>
        <input value={platform} onChange={e => setPlatform(e.target.value)} className={`${inp} w-28`} />
      </div>
      <div className={col}>
        <label className={lbl}>年化收益率 %</label>
        <input type="number" step="0.001" value={yieldPct} onChange={e => setYieldPct(e.target.value)}
          className={`${inp} w-20`} placeholder="2.65" />
      </div>
      <div className={col}>
        <label className={lbl}>
          手动市值 ¥
          <span className="ml-1 text-[10px] text-text-muted">清空=恢复估算</span>
        </label>
        <input type="number" step="0.01" value={manualValue} onChange={e => setManualValue(e.target.value)}
          className={`${inp} w-28`} placeholder={q.value_source === 'yield' ? '当前按年化计提' : '可空'} />
      </div>
      <div className={col}>
        <label className={lbl}>起息日</label>
        <input type="date" value={valueDate} onChange={e => setValueDate(e.target.value)}
          className={`${inp} w-[130px]`} />
      </div>
      <div className={col}>
        <label className={lbl}>期限 (天)</label>
        <input type="number" step="1" value={termDays}
          onChange={e => { termTouched.current = true; setTermDays(e.target.value) }}
          className={`${inp} w-16`} />
      </div>
      <div className={col}>
        <label className={lbl}>到期日</label>
        <input type="date" value={maturityDate}
          onChange={e => { maturityTouched.current = true; setMaturityDate(e.target.value) }}
          className={`${inp} w-[130px]`} />
      </div>
      <div className={col}>
        <label className={lbl}>付息方式</label>
        <select value={interestType} onChange={e => setInterestType(e.target.value)} className={`${inp} w-28`}>
          {INTEREST_TYPES.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
        </select>
      </div>
      <div className={col}>
        <label className={lbl}>风险等级</label>
        <select value={riskLevel} onChange={e => setRiskLevel(e.target.value)} className={`${inp} w-16`}>
          <option value="">无</option>
          {['R1', 'R2', 'R3', 'R4', 'R5'].map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>
      <div className={col}>
        <label className={lbl}>状态</label>
        <select value={status} onChange={e => setStatus(e.target.value)} className={`${inp} w-28`}>
          {STATUSES.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
        </select>
      </div>
      <div className={`${col} flex-1 min-w-[160px]`}>
        <label className={lbl}>备注</label>
        <input value={note} onChange={e => setNote(e.target.value)} className={`${inp} w-full`} />
      </div>

      <div className="flex gap-2">
        <button onClick={save} disabled={busy}
          className="text-[12px] px-3.5 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
          {busy ? '...' : '保存'}
        </button>
        <button onClick={onCancel} className="text-[12px] px-3 py-1.5 rounded-lg text-text-dim hover:text-text border border-border">
          取消
        </button>
      </div>
      {err && <span className="text-[11px] text-bear-bright basis-full">{err}</span>}
    </div>
  )
}
