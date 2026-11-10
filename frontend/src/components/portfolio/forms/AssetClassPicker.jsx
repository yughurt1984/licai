/* eslint-disable react-refresh/only-export-components */
import { useEffect, useMemo, useState } from 'react'
import { fetchJSON } from '../../../hooks/useApi'
import { currencySymbol } from '../helpers'

// 资产分类选择器(数据源: assets_classify, 经 /api/market/asset-classes)。
// 四件套: 大类徽章(assets_class_name) + 资产类型下拉(main_assets_type) +
// 币种下拉(currency) + 市场自动匹配(main × currency → 唯一 sub_assets_type)。
// 股票/基金/理财/现金/加密/期货 各表单共用; 机器人不在分类目录, 不接。

// 币种选项只展示**当前 main_assets_type 下有的**货币(如 优先股 只有 USD 就只给 USD);
// 人民币置顶(本位币), 其余按字母序。
function currenciesOf(rows, main) {
  const set = new Set(
    rows.filter(r => !main || r.main_assets_type === main).map(r => r.currency).filter(Boolean))
  return [...set].sort((a, b) => (a === 'CNY' ? -1 : b === 'CNY' ? 1 : a.localeCompare(b)))
}

// 小类 → 录入口径市场(代码格式/费率方案/交易日历的键)。
// 小类本身是 PG 数据, 这个映射只管表单 UX 常量, 不决定"是什么资产"。
export function subMarket(sub) {
  const s = String(sub || '')
  return s.startsWith('a_') ? 'A'
    : s.startsWith('hk_') ? 'HK'
    : s.startsWith('us_') ? 'US' : ''
}

const _cache = new Map()      // assetsClass → Promise<rows>(模块级, 同类多表单共享一次请求)
const _allCache = { p: null }  // 全量目录 Promise(录入入口一次性取全部大类/中类/小类)

// 录入入口用: 一次取全量 assets_classify, 派生「大类(assets_class)」标签列表 ——
// 期货/期权同属 derivatives, 自然归到一个「衍生品」标签, 不硬编码任何资产种类。
export function useAllAssetClasses() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  useEffect(() => {
    let dead = false
    if (!_allCache.p) {
      _allCache.p = fetchJSON('/api/market/asset-classes')
        .then(d => d.items || [])
        .catch(e => { _allCache.p = null; throw e })
    }
    _allCache.p
      .then(items => { if (!dead) { setRows(items); setErr('') } })
      .catch(() => { if (!dead) setErr('分类目录加载失败') })
    return () => { dead = true }
  }, [])
  // 大类按目录首次出现顺序(不写死顺序), 名称取 assets_class_name
  const classOptions = useMemo(() => {
    const seen = new Map()
    for (const r of rows)
      if (r.assets_class && !seen.has(r.assets_class))
        seen.set(r.assets_class, r.assets_class_name || r.assets_class)
    return [...seen.entries()]
  }, [rows])
  return { rows, classOptions, err }
}

/**
 * 分类状态 hook: 持有 main/currency 选择, 并在渲染期派生**有效组合** ——
 *   · main 不在目录里 → preferMain(在目录) → 第一个 main
 *   · 币种不在当前 main 下 → preferCurrency(在) → CNY(在) → 该 main 下第一个币种
 * 这样换 main 后币种自动吸附到合法值(优先股只有 USD → 自动落 USD), 用户永远选不出
 * 无效组合; picked* 保留原始选择, 切回别的 main 时原币种若重新合法会自动恢复。
 */
export function useAssetClass(assetsClass, { preferMain = '', preferCurrency = 'CNY', lockedMain = '', requireValuation = '' } = {}) {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [pickedMain, setPickedMain] = useState(lockedMain || preferMain)
  const [pickedCurrency, setPickedCurrency] = useState('')

  useEffect(() => {
    if (!assetsClass) return undefined
    let dead = false
    if (!_cache.has(assetsClass)) {
      _cache.set(assetsClass, fetchJSON(
        `/api/market/asset-classes?assets_class=${encodeURIComponent(assetsClass)}`)
        .then(d => d.items || [])
        .catch(e => { _cache.delete(assetsClass); throw e }))
    }
    _cache.get(assetsClass)
      .then(items => { if (!dead) { setRows(items); setErr('') } })
      .catch(() => { if (!dead) setErr('分类目录加载失败') })
    return () => { dead = true }
  }, [assetsClass])

  // requireValuation: 只保留指定估值类型的小类(如固定收益表单只要 yield —— 同一大类下
  // quote 型小类(交易所债/可转债)属持仓账本, 不该出现在本金+年化表单里), 数据驱动不写死。
  // assetsClass 为空(不接分类的表单)时视为无目录, 不留上一个大类的残留
  const liveRows = useMemo(() => {
    if (!assetsClass) return []
    if (!requireValuation) return rows
    return rows.filter(r => String(r.valuation_type || '').trim().toLowerCase() === requireValuation)
  }, [assetsClass, rows, requireValuation])

  const effective = useMemo(() => {
    if (!liveRows.length) return { main: lockedMain || pickedMain, currency: pickedCurrency }
    const mains = [...new Set(liveRows.map(r => r.main_assets_type))]
    let m = lockedMain || pickedMain
    if (!mains.includes(m)) m = mains.includes(preferMain) ? preferMain : mains[0]
    const curs = currenciesOf(liveRows, m)
    let c = pickedCurrency
    if (!curs.includes(c)) {
      c = curs.includes(preferCurrency) ? preferCurrency : curs.includes('CNY') ? 'CNY' : (curs[0] || '')
    }
    return { main: m, currency: c }
  }, [liveRows, pickedMain, pickedCurrency, lockedMain, preferMain, preferCurrency])

  const mainOptions = useMemo(() => {
    const seen = new Map()
    for (const r of liveRows)
      if (r.main_assets_type && !seen.has(r.main_assets_type))
        seen.set(r.main_assets_type, r.assets_type_name || r.main_assets_type)
    return [...seen.entries()]
  }, [liveRows])
  const currencyOptions = useMemo(
    () => currenciesOf(liveRows, effective.main), [liveRows, effective.main])
  const matched = useMemo(
    () => liveRows.find(r => r.main_assets_type === effective.main && r.currency === effective.currency) || null,
    [liveRows, effective.main, effective.currency])
  const market = subMarket(matched?.sub_assets_type)
  const curSymbol = currencySymbol(effective.currency || 'CNY')

  return {
    rows: liveRows, err,
    mainOptions, currencyOptions,
    main: effective.main, currency: effective.currency,
    matched, market, curSymbol,
    setMain: setPickedMain, setCurrency: setPickedCurrency,
  }
}

export function AssetClassPicker({ err, mainOptions, currencyOptions, matched,
                                   main, currency, mainLocked = false, inp = '',
                                   onMain, onCurrency, compact = false }) {
  // 资产大类由上层入口(手动记账的 entryType 下拉 / 持仓页添加入口)点选决定, 此处不再重复展示。
  // compact=true: 窄输入(衍生品等一字段一行的大表单用), 标签照旧在输入框上方。
  const wMain = compact ? 'w-[76px]' : ''
  const wCur = compact ? 'w-[68px]' : ''
  const wMkt = compact ? 'w-[74px]' : 'w-24'
  return (
    <>
      <div className="flex flex-col gap-1">
        <label className="text-[12px] text-text-dim">资产类型</label>
        <select className={`${inp} ${wMain}`} value={main} disabled={mainLocked}
          onChange={e => onMain?.(e.target.value)}>
          {mainOptions.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[12px] text-text-dim">币种</label>
        <select className={`${inp} ${wCur}`} value={currency} onChange={e => onCurrency?.(e.target.value)}>
          {currencyOptions.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[12px] text-text-dim"
          title={matched ? `sub_assets_type: ${matched.sub_assets_type}` : undefined}>
          市场 <span className="text-text-muted text-[11px]">自动匹配</span>
        </label>
        <div className={`${inp} ${wMkt} !bg-surface-3/40 text-text-bright flex items-center`}>
          {matched?.sub_assets_type_name || '—'}
        </div>
      </div>
      {err && <div className="text-[10.5px] text-warn w-full pb-1">{err}。</div>}
    </>
  )
}
