import { useState } from 'react'
import { useAllAssetClasses, useAssetClass } from './AssetClassPicker'

const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[0.8929rem] text-text outline-none focus:border-accent'

// 录入资产入口(全部来自 assets_classify, 不硬编码任何资产种类/中类/小类):
//  ① 顶层标签 = 大类(assets_class); 期货/期权同属 derivatives → 自然归到一个「衍生品」标签
//  ② 选中大类 → 中类(main_assets_type)下拉
//  ③ 中类 × 币种 → 唯一小类(sub_assets_type)自动匹配
// 选中小类后「录入」打开对应表单(表单按小类锁定分类, 不再二次选择)。
export function AssetEntryMenu({ onPick, onCancel }) {
  const { classOptions, err } = useAllAssetClasses()
  const [cls, setCls] = useState('')
  const cat = useAssetClass(cls || null)   // 大类未选时返回空, 不请求

  return (
    <div className="px-3 md:px-6 py-3 border-b border-border bg-surface-2/60 space-y-2">
      {/* ① 资产大类标签: 全部来自 assets_classify, 不硬编码 */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[0.7857rem] text-text-dim mr-1">资产大类</span>
        {classOptions.length === 0 && !err && (
          <span className="text-[0.7857rem] text-text-muted">加载中…</span>
        )}
        {classOptions.map(([c, label]) => (
          <button key={c} type="button" onClick={() => setCls(c)}
            className={cls === c
              ? 'px-3 py-1 rounded border border-accent text-[0.8571rem] text-accent'
              : 'px-3 py-1 rounded border border-border-med text-[0.8571rem] text-text hover:border-accent hover:text-accent transition-colors cursor-pointer'}>
            {label}
          </button>
        ))}
        <button type="button" onClick={onCancel}
          className="ml-auto text-[0.7857rem] text-text-dim hover:text-text cursor-pointer">取消</button>
      </div>

      {/* ② 中类 + ③ 币种 → 小类 级联(数据驱动, 无任何硬编码) */}
      {cls && (
        <div className="flex flex-wrap gap-2 items-end">
          <div className="flex flex-col gap-1">
            <label className="text-[0.7857rem] text-text-dim">资产类型(中类)</label>
            <select className={inp} value={cat.main} onChange={e => cat.setMain(e.target.value)}>
              {cat.mainOptions.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[0.7857rem] text-text-dim">币种</label>
            <select className={inp} value={cat.currency} onChange={e => cat.setCurrency(e.target.value)}>
              {cat.currencyOptions.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[0.7857rem] text-text-dim"
              title={cat.matched ? `sub_assets_type: ${cat.matched.sub_assets_type}` : undefined}>
              小类 <span className="text-text-muted text-[0.7143rem]">自动匹配</span>
            </label>
            <div className={`${inp} w-28 !bg-surface-3/40 text-text-bright flex items-center`}>
              {cat.matched?.sub_assets_type_name || '—'}
            </div>
          </div>
          <button type="button" onClick={() => cat.matched && onPick?.(cls, cat.matched.sub_assets_type)}
            disabled={!cat.matched}
            className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[0.9286rem] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            录入
          </button>
        </div>
      )}
      {err && <div className="text-[0.75rem] text-warn w-full">{err}。</div>}
    </div>
  )
}
