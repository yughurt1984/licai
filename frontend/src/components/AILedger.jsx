import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fetchJSON } from '../hooks/useApi'
import { loadBrokers } from '../helpers'
import { TYPE_META } from './portfolio/constants'
import { AddAShareForm } from './portfolio/forms/AddAShareForm'
import { AddFundForm } from './portfolio/forms/AddFundForm'
import { AddFuturesForm } from './portfolio/forms/AddFuturesForm'
import { DraftCard } from './ailedger/DraftCard'
import { saveDraft } from './ailedger/save'

// ============================================================
// AI记账 — 多品种统一记账入口
// 上半部: 交易记录/成交截图/交易短信 → LLM 解析 → 可编辑草稿 → 入账
//         (入账全部走既有端点, 见 ailedger/save.js)
// 下半部: 下拉选择资产类型, 复用持仓页现成的分品种录入表单
// ============================================================

// LLM 解析的资产提示 + 手动录入下拉共用这份清单(auto 只给解析用)
const TYPE_OPTIONS = [
  ['A', '股票'], ['F', '基金'], ['W', '理财'], ['M', '现金'],
  ['C', '加密'], ['R', '机器人'], ['T', '期货'], ['O', '期权'],
]

// 截图压到长边 1600 的 JPEG: 截图动辄 2-8MB, base64 再涨 1/3, LLM 和请求体都吃不消。
// GIF 压了会丢动画帧, 小图没必要压, 原样传。
async function fileToDataUrl(file) {
  const dataUrl = await new Promise((res, rej) => {
    const r = new FileReader()
    r.onload = () => res(r.result)
    r.onerror = () => rej(new Error('读取图片失败'))
    r.readAsDataURL(file)
  })
  if (file.type === 'image/gif' || file.size < 300 * 1024) return dataUrl
  try {
    const img = await new Promise((res, rej) => {
      const im = new Image()
      im.onload = () => res(im)
      im.onerror = () => rej(new Error('decode failed'))
      im.src = dataUrl
    })
    const scale = Math.min(1, 1600 / (img.naturalWidth || 1600))
    if (scale >= 1) return dataUrl
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(img.naturalWidth * scale)
    canvas.height = Math.round(img.naturalHeight * scale)
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', 0.85)
  } catch { return dataUrl }
}

export default function AILedger({ onMutated }) {
  // ---- AI 解析区状态 ----
  const [hint, setHint] = useState('')             // 目标资产提示(auto=空)
  const [text, setText] = useState('')
  const [images, setImages] = useState([])          // {id, dataUrl, name}
  const [parsing, setParsing] = useState(false)
  const [parseErr, setParseErr] = useState('')
  const [notes, setNotes] = useState('')
  const [drafts, setDrafts] = useState([])
  // ---- 手动录入区状态 ----
  const [entryType, setEntryType] = useState('A')
  const [brokers, setBrokers] = useState([])
  const [formKey, setFormKey] = useState(0)        // 保存/取消后整表单重置(表单状态在自己内部)
  const [toast, setToast] = useState('')
  const toastTimer = useRef(null)

  // 草稿入账要知道"账上已有什么": 决定买入走建仓还是加仓、卖出能不能平
  const [holdings, setHoldings] = useState([])
  const [assets, setAssets] = useState([])
  const refresh = useCallback(async () => {
    try { setHoldings(await api.getPortfolio()) } catch {}
    try { setAssets((await fetchJSON('/api/assets')).assets || []) } catch {}
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { loadBrokers().then(setBrokers).catch(() => {}) }, [])

  const showToast = useCallback((msg) => {
    setToast(msg)
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(''), 4000)
  }, [])
  useEffect(() => () => clearTimeout(toastTimer.current), [])

  const addImages = useCallback(async (files) => {
    const imgs = [...files].filter(f => f.type.startsWith('image/'))
    if (!imgs.length) return
    const loaded = await Promise.all(imgs.map(async f => ({
      id: `img${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      dataUrl: await fileToDataUrl(f), name: f.name || '截图',
    })))
    setImages(prev => [...prev, ...loaded].slice(0, 5))
  }, [])

  // 截图随时 Ctrl+V: 监听页面级 paste, 有图片文件就直接收进候补(不用先点进输入框)
  useEffect(() => {
    const onPaste = (e) => {
      const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'))
      if (files.length) { e.preventDefault(); addImages(files) }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [addImages])

  const [dragOver, setDragOver] = useState(false)

  const handleParse = async () => {
    if (parsing) return
    if (!text.trim() && images.length === 0) {
      setParseErr('先贴上交易记录/短信原文, 或附一张成交截图')
      return
    }
    setParsing(true); setParseErr(''); setNotes('')
    try {
      const d = await fetchJSON('/api/ledger/parse', {
        method: 'POST',
        body: JSON.stringify({
          text, images: images.map(i => i.dataUrl),
          asset_type: hint || null,
        }),
      })
      const txs = d.transactions || []
      setDrafts(prev => [...prev, ...txs.map((tx, i) => ({
        id: `d${Date.now()}_${i}`,
        // LLM 字段 → 草稿字段(命名对齐卡片编辑器); state: draft→saved/failed
        type: tx.type, market: tx.market || 'A', fundKind: tx.fund_kind || '',
        side: tx.side || 'BUY', direction: tx.direction || 'LONG',
        code: tx.code || '', name: tx.name || '',
        price: tx.price ?? '', shares: tx.shares ?? '', amount: tx.amount ?? '',
        fee: tx.fee ?? '', month: tx.month || '', multiplier: tx.multiplier ?? '',
        annualYieldPct: tx.annual_yield_pct ?? '', startDate: tx.start_date || '',
        currentValue: tx.current_value ?? '',
        broker: tx.broker || '', platform: tx.platform || '',
        tradeDate: tx.trade_date || new Date().toISOString().slice(0, 10),
        tradeTime: tx.trade_time || '', note: tx.note || '',
        confidence: tx.confidence, evidence: tx.evidence || '',
        state: 'draft', msg: '',
      }))])
      setNotes(d.notes || '')
      if (!txs.length) setParseErr(d.notes || '没有识别到可入账的交易')
    } catch (e) {
      setParseErr(e.message)
    } finally { setParsing(false) }
  }

  const patchDraft = (id, patch) => {
    setDrafts(prev => prev.map(x => (x.id === id ? { ...x, ...patch } : x)))
  }
  const removeDraft = (id) => setDrafts(prev => prev.filter(x => x.id !== id))

  // 全部入账: 串行跑, 每张卡的成功/失败都落在自己的 state 上(后一张不因前一张失败而跳过)
  const [savingAll, setSavingAll] = useState(false)
  const saveAll = async () => {
    setSavingAll(true)
    let ok = 0, fail = 0
    for (const d of drafts.filter(x => x.state === 'draft')) {
      try {
        const msg = await saveDraft(d, { holdings, assets })
        patchDraft(d.id, { state: 'saved', msg })
        ok++
      } catch (e) {
        patchDraft(d.id, { state: 'failed', msg: e.message })
        fail++
      }
      await refresh()
    }
    setSavingAll(false)
    if (ok) onMutated?.()
    showToast(`入账完成: ${ok} 笔成功${fail ? `, ${fail} 笔失败(见卡片提示)` : ''}`)
  }

  const pendingDrafts = drafts.filter(d => d.state !== 'saved')

  const formProps = {
    brokers,
    onDone: (msg) => { showToast(typeof msg === 'string' ? msg : '已入账'); setFormKey(k => k + 1); onMutated?.(); refresh() },
    onCancel: () => setFormKey(k => k + 1),
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      {/* Header: 与持仓页同款卡头 */}
      <div className="px-3 md:px-6 py-3 md:py-4 border-b border-border flex flex-col gap-1"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-3">
          <h2 className="text-[15px] font-semibold text-text-bright tracking-wide m-0">AI 记账</h2>
          <span className="text-[12px] text-text-dim">成交截图 · 交易短信 · 手打记录 → 大模型解析入账</span>
        </div>
        <span className="text-[11px] text-text-muted">
          AI 只做识别不入账 —— 解析结果先列成草稿, 你核对/改错后点「入账」, 写入的还是持仓页那套账本
        </span>
      </div>

      {/* ---- AI 识别区 ---- */}
      <div className={`px-3 md:px-6 py-3 border-b border-border space-y-2.5 ${dragOver ? 'bg-accent/5' : ''}`}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => { e.preventDefault(); setDragOver(false); addImages(e.dataTransfer.files) }}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[12px] text-text-dim shrink-0">目标资产</span>
          <select value={hint} onChange={e => setHint(e.target.value)}
            className="bg-bg border border-border rounded px-2 py-1 text-[12.5px] text-text outline-none focus:border-accent"
            title="给 AI 的提示; 内容本身更可信, 判断不了就选自动">
            <option value="">自动判断</option>
            {TYPE_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <span className="text-[10.5px] text-text-muted">
            支持文本 + 最多 5 张截图; 截图可 Ctrl+V 直接粘贴或拖进本区域
          </span>
        </div>

        <textarea
          value={text} onChange={e => setText(e.target.value)}
          rows={3}
          placeholder={'粘贴交易记录 / 交易短信原文, 例如:\n【XX证券】您于10月28日14:32成交买入 600519 贵州茅台 100股, 成交价1412.50元, 发生金额141250.00元。'}
          className="w-full bg-bg border border-border rounded px-2.5 py-2 text-[13px] text-text outline-none focus:border-accent resize-y leading-relaxed" />

        {images.length > 0 && (
          <div className="flex gap-2 flex-wrap">
            {images.map(img => (
              <div key={img.id} className="relative group">
                <img src={img.dataUrl} alt={img.name}
                  className="h-16 w-24 object-cover rounded border border-border" />
                <button onClick={() => setImages(prev => prev.filter(x => x.id !== img.id))}
                  title="移除"
                  className="absolute -top-1.5 -right-1.5 w-[18px] h-[18px] rounded-full bg-bear text-bg text-[11px] leading-none
                    opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer">×</button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 flex-wrap">
          <label className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12.5px] hover:text-text hover:border-border-med transition-colors cursor-pointer">
            + 上传截图
            <input type="file" accept="image/*" multiple className="hidden"
              onChange={e => { addImages(e.target.files); e.target.value = '' }} />
          </label>
          <button onClick={handleParse} disabled={parsing}
            className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 transition-opacity disabled:opacity-50 cursor-pointer">
            {parsing ? 'AI 识别中…' : `AI 识别记账${images.length ? ` (${text.trim() ? '文本+' : ''}${images.length}图)` : ''}`}
          </button>
          {(text || images.length > 0) && (
            <button onClick={() => { setText(''); setImages([]); setParseErr(''); setNotes('') }}
              className="px-3 py-1.5 text-[12.5px] text-text-dim hover:text-text cursor-pointer">清空输入</button>
          )}
          {parsing && <span className="text-[11.5px] text-text-muted">大模型在读你的截图和文字, 一般几秒到十几秒</span>}
        </div>

        {parseErr && (
          <div className="text-[12px] text-bear">✗ {parseErr}</div>
        )}
      </div>

      {/* ---- 识别结果: 草稿卡片 ---- */}
      {drafts.length > 0 && (
        <div className="border-b border-border">
          <div className="px-3 md:px-6 py-2 flex items-center gap-2 border-b border-border-subtle"
            style={{ background: 'var(--color-surface-2)' }}>
            <span className="text-[12px] font-semibold text-text">识别结果</span>
            <span className="text-[11.5px] text-text-dim">{drafts.length} 笔, 待入账 {pendingDrafts.length}</span>
            {notes && <span className="text-[11px] text-text-muted truncate" title={notes}>{notes}</span>}
            <div className="ml-auto flex items-center gap-2">
              {pendingDrafts.length > 0 && (
                <button onClick={saveAll} disabled={savingAll}
                  className="px-3 py-1 rounded border border-accent/50 bg-accent/10 text-accent text-[12px] hover:bg-accent/20 disabled:opacity-50 cursor-pointer">
                  {savingAll ? '入账中…' : `全部入账 (${pendingDrafts.length})`}
                </button>
              )}
              <button onClick={() => setDrafts([])}
                className="text-[11px] text-text-muted hover:text-text cursor-pointer">清空草稿</button>
            </div>
          </div>
          {drafts.map(d => (
            <DraftCard key={d.id} draft={d} ctx={{ holdings, assets }}
              onChange={patchDraft} onRemove={removeDraft} />
          ))}
        </div>
      )}

      {/* ---- 手动录入: 下拉选资产 → 复用持仓页现成表单 ---- */}
      <div className="px-3 md:px-6 py-2 border-b border-border flex items-center gap-2.5 flex-wrap"
        style={{ background: 'var(--color-surface-2)' }}>
        <span className="text-[12px] font-semibold text-text">手动记账</span>
        <select value={entryType} onChange={e => setEntryType(e.target.value)}
          className="bg-bg border border-border rounded px-2 py-1 text-[12.5px] text-text outline-none focus:border-accent">
          {/* 全类型可选; 加密/机器人/理财/现金暂显示待开发占位(表单后续单独实现) */}
          {TYPE_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <span className="text-[10.5px] text-text-muted">
          {TYPE_META[entryType]?.desc || ''} · 字段与持仓页「添加」表单一致, 走同一套入账接口
        </span>
      </div>

      <div className="px-3 md:px-6 py-3">
        {/* key 撞在表单上: 保存/取消后整体重挂载, 表单内部 state 自然回到空白 */}
        {entryType === 'A' && <AddAShareForm key={`a${formKey}`} initialMarket="A" {...formProps} />}
        {/* AddFundForm 仅处理基金(F); CRYPTO/机器人/理财/现金不在此表单 */}
        {entryType === 'F' && (
          <AddFundForm key={`f${formKey}`} typeKey={entryType} {...formProps} />
        )}
        {['T', 'O'].includes(entryType) && (
          <AddFuturesForm key={`t${formKey}`} kind={entryType} {...formProps} />
        )}
        {/* 加密/机器人/理财/现金入口保留, 表单待单独开发 */}
        {['C', 'R', 'W', 'M'].includes(entryType) && (
          <div className="text-[12.5px] text-text-muted py-1">
            「{TYPE_META[entryType]?.label || entryType}」添加表单待单独开发。
          </div>
        )}
      </div>

      {toast && (
        <div className="fixed bottom-5 right-5 z-[300] px-4 py-2.5 rounded-lg border border-border-med bg-surface-2
          shadow-lg text-[12.5px] text-text" style={{ animation: 'fade-up 0.3s ease-out' }}>
          {toast}
        </div>
      )}
    </section>
  )
}
