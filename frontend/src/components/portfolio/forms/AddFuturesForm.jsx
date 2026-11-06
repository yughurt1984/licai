import { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '../../../hooks/useApi'
import { brokersOfType } from '../../../helpers'
import { AssetClassPicker, useAssetClass } from './AssetClassPicker'

// 期货/期权开仓表单(品种 + 月份拼接模式), 代码/名称逻辑与股票表单一致:
// 第一行: 资产类型/币种/市场(来自 assets_classify; main 由期货|期权入口锁定,
//         币种切换 → 匹配唯一小类 cn_futures/hk_futures/us_futures..., 品种搜索按小类过滤)
//         + 品种代码(输入框) + 品种名称(输入框, 防抖查 symbol_dict 自动带出, 可手改) + 方向(多红/空绿)
// 第二行: 合约月份(下拉, 未来 12 个月) + 合约代码(自动拼接 CU2601.SHF, 交易所后缀取品种行)
//         + 保证金(按品种 margin_rate 估算, 仅供参考) + 手数 + 每手乘数(默认品种 trade_amount)
//         + 开仓价 + 手续费(默认按分类比例费率×名义金额, 可覆盖) + 券商 + 日期
// 品种搜索只在 symbol_dict 品种行里做(不联网、不弹下拉); 提交走 POST /api/portfolio
// (asset_kind=fut|opt + direction), 后端 seed OPEN 流水并重算综合成本。
export function AddFuturesForm({ sub, brokers, onDone, onCancel }) {
  // 资产分类(PG assets_classify): 衍生品大类 + 中类(futures/options/warrant) × 币种 → 唯一小类。
  // 中类由用户在表单里选(不再靠 kind 枚举锁定) —— 期货/期权同属 derivatives, 一个入口覆盖。
  const { rows, err: clsErr, mainOptions, currencyOptions,
    matched: hookMatched, main: hookMain,
    setMain, setCurrency } =
    useAssetClass('derivatives', {})
  // 录入入口已选具体小类(sub)时锁定中类/币种; 否则用户在中类下拉里选 futures/options
  const subRow = sub ? rows.find(r => r.sub_assets_type === sub) : null
  const main = subRow ? subRow.main_assets_type : hookMain
  const matched = subRow || hookMatched
  const isOpt = /opt|warrant/.test(main)   // 期货/期权由选中的中类派生, 不靠 kind 硬编码

  const [query, setQuery] = useState('')
  const [name, setName] = useState('')                   // 品种名称: 自动带出, 可手改
  const [searching, setSearching] = useState(false)
  const [product, setProduct] = useState(null)        // 品种行 {code,name,exchange,trade_amount,margin_rate,open_fee_rate,...}
  const [month, setMonth] = useState('')
  const [direction, setDirection] = useState('LONG')  // LONG(多,红) | SHORT(空,绿)
  const [lots, setLots] = useState('1')
  const [multiplier, setMultiplier] = useState('')
  const [price, setPrice] = useState('')
  const [fee, setFee] = useState('')                  // 留空 = 按分类比例费率自动(回退券商档案), 后端同口径
  const [broker, setBroker] = useState('')
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const debounceRef = useRef(null)
  const lookupSeq = useRef(0)
  const subType = matched?.sub_assets_type || ''          // 匹配到的小类: 品种搜索按它过滤

  const brokerOptions = brokersOfType(brokers, 'FUT')

  // 合约月份: 从当前月起未来 12 个月 (YYMM, 如 2601)
  const months = (() => {
    const arr = []
    const now = new Date()
    for (let i = 0; i < 12; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() + i, 1)
      arr.push(String(d.getFullYear()).slice(2) + String(d.getMonth() + 1).padStart(2, '0'))
    }
    return arr
  })()
  useEffect(() => { if (!month) setMonth(months[0]) /* eslint-disable-line react-hooks/exhaustive-deps */ }, [])

  // 换市场小类(币种) = 换品种池: 清掉已选品种, 避免上一市场的品种行拼错合约
  useEffect(() => {
    setProduct(null)
  }, [subType])

  // 品种名称自动带出(仿股票表单): 防抖 350ms, 只搜 symbol_dict 品种行(按匹配小类过滤), 不弹下拉 ——
  // 命中(精确代码优先, 否则首个)自动把名称填进品种名称输入框, 手数乘数默认带品种乘数;
  // 查不到则留空(可手填名称, 但合约代码需要品种行的交易所后缀, 提交前须先登记品种)。
  useEffect(() => {
    const q = query.trim()
    if (q.length < 1) { setProduct(null); setSearching(false); return undefined }
    const seq = ++lookupSeq.current
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        const d = await fetchJSON(
          `/api/portfolio/products/search?q=${encodeURIComponent(q)}&kind=${isOpt ? 'opt' : 'fut'}` +
          (subType ? `&sub=${encodeURIComponent(subType)}` : ''))
        if (seq !== lookupSeq.current) return
        const items = d?.items || []
        const pick = items.find(p => (p.code || '').toUpperCase() === q.toUpperCase()) || items[0] || null
        setProduct(pick)
        if (pick) {
          setName(pick.name || '')
          if (pick.trade_amount) setMultiplier(String(pick.trade_amount))
        }
      } catch { if (seq === lookupSeq.current) setProduct(null) }
      finally { if (seq === lookupSeq.current) setSearching(false) }
    }, 350)
    return () => clearTimeout(debounceRef.current)
  }, [query, isOpt, sub])

  // 合约代码自动拼接: 品种 + 月份 + 交易所后缀 (CU2601.SHF); 期权同(IO2601.CFE 按品种行交易所)
  const thscode = product && month
    ? `${product.code}${month}${product.exchange ? '.' + product.exchange : ''}`.toUpperCase()
    : ''
  // 保证金估算(仅供参考): 开仓价 × 每手乘数 × 手数 × 保证金率 —— 期货公司在交易所基础上加收
  // (保证金率来自 assets_classify 品种分类行, 经 /products/search JOIN 带出)
  const marginEst = product?.margin_rate && parseFloat(multiplier) > 0 && parseFloat(lots) > 0 && parseFloat(price) > 0
    ? parseFloat(price) * parseFloat(multiplier) * parseFloat(lots) * product.margin_rate
    : null
  // 手续费自动口径(与后端一致): 分类行比例费率 × 名义金额(价×乘数×手数); 分类行没配费率时
  // 后端回退券商档案(元/手), 前端就不预显数字
  const feeAuto = product?.open_fee_rate != null && parseFloat(lots) > 0 && parseFloat(price) > 0 && parseFloat(multiplier) > 0
    ? parseFloat(price) * parseFloat(multiplier) * parseFloat(lots) * product.open_fee_rate
    : null

  const submit = async () => {
    if (!product) return setErr('请先选择品种')
    if (!thscode) return setErr('合约代码拼接失败: 检查品种行的交易所后缀是否已维护')
    const sh = parseFloat(lots)
    if (!(sh > 0)) return setErr('手数必须大于 0')
    const mult = parseFloat(multiplier)
    if (!(mult > 0)) return setErr('请填每手乘数')
    const px = parseFloat(price)
    if (!(px > 0)) return setErr('请填开仓价格')
    setBusy(true); setErr('')
    try {
      const body = {
        stock_code: thscode,
        stock_name: (name.trim() || product.name || '') + month || thscode,
        shares: sh,
        cost_price: px,
        asset_kind: isOpt ? 'opt' : 'fut',
        direction,
        contract_multiplier: mult,
        trade_date: tradeDate,
        sub_assets_type: subType || undefined,   // 分类归口目录(trade=t 路由校验 + 统一命名)
      }
      if (broker) body.broker = broker
      if (fee !== '' && parseFloat(fee) >= 0) body.fee = parseFloat(fee)   // 留空 = 后端按品种费率自动
      await fetchJSON('/api/portfolio', { method: 'POST', body: JSON.stringify(body) })
      onDone('开仓成功')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[12.5px] text-text font-mono outline-none focus:border-accent'
  const field = (label, value, set, placeholder, step = '0.01', type = 'number') => (
    <div className="flex flex-col gap-1">
      <label className="text-[10.5px] text-text-muted">{label}</label>
      <input type={type} step={step} min="0" className={`${inp} w-24`} placeholder={placeholder}
        value={value} onChange={e => set(e.target.value)} />
    </div>
  )

  return (
    <div className="rounded-lg border border-border-med bg-surface-2/60 p-3 space-y-3 fade-up">
      {/* 第一行: 资产类型(中类)/币种/市场(自动匹配) + 品种代码 + 品种名称 + 方向 */}
      <div className="flex flex-wrap items-end gap-2">
        {subRow ? (
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] text-text-muted">分类</label>
            <div className={`${inp} w-28 !bg-surface-3/40 text-text-bright flex items-center`}>
              {subRow.sub_assets_type_name || sub}
            </div>
          </div>
        ) : (
          <AssetClassPicker rows={rows} err={clsErr}
            mainOptions={mainOptions}
            currencyOptions={currencyOptions}
            matched={matched} main={main} inp={inp}
            onMain={setMain} onCurrency={setCurrency} />
        )}
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">{isOpt ? '期权品种代码' : '期货代码'}</label>
          <input className={`${inp} w-28 font-sans`} autoFocus placeholder="CU / 沪铜"
            value={query} onChange={e => setQuery(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">期货品种</label>
          <input className={`${inp} w-28 font-sans`} placeholder={searching ? '查询中…' : '输入代码自动带出'}
            value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10.5px] text-text-muted">方向</label>
          <div className="flex gap-1">
            {[['LONG', '多'], ['SHORT', '空']].map(([d, label]) => (
              <button key={d} onClick={() => setDirection(d)}
                className={`px-4 py-1.5 rounded-md text-[12.5px] font-semibold border transition-colors cursor-pointer
                  ${direction === d
                    ? d === 'LONG' ? 'bg-bull/20 text-bull border-bull/60' : 'bg-bear/20 text-bear border-bear/60'
                    : 'border-border text-text-dim hover:text-text'}`}>
                {label}{d === 'LONG' ? ' (买涨)' : ' (卖跌)'}
              </button>
            ))}
          </div>
        </div>
      {/* 合约月份 + 合约代码 + 保证金 + 手数/乘数/价格/费用/券商/日期 (同排 flex-wrap) */}
      {!product && query.trim() && !searching && (
        <p className="text-[11px] text-warn m-0 w-full">
          {subType
            ? `当前市场分类「${matched?.sub_assets_type_name || subType}」下没有匹配的品种行(需维护: code=品种代码如 CU, name=品种名称, exchange=交易所后缀如 SHF, sub_assets_type=${subType})`
            : 'symbol_dict 里没有匹配的品种行(需维护品种行并确认币种组合能匹配到小类)'}
        </p>
      )}
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] text-text-muted">合约月份</label>
            <select className={`${inp} w-24 font-mono`} value={month} onChange={e => setMonth(e.target.value)}>
              {months.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] text-text-muted">合约代码</label>
            <span className={`${inp} w-40 font-mono !bg-surface-3/60 text-text-bright select-all`}>{thscode || '—'}</span>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] text-text-muted"
              title="开仓价 × 每手乘数 × 手数 × 保证金率。交易所口径估算, 期货公司会在其基础上加收 —— 实际以保证金监控为准">
              保证金(参考)
            </label>
            <span className="font-mono text-[12.5px] text-warn pb-1.5">
              {marginEst != null ? `¥${marginEst.toFixed(0)} ≈` : '填价后自动'}
            </span>
          </div>
          {field('手数', lots, setLots, '1', '1')}
          {field('每手乘数', multiplier, setMultiplier, product?.trade_amount ? String(product.trade_amount) : '5', '1')}
          {field(`开仓价 ¥`, price, setPrice, '现价')}
          {field(`手续费 ¥`, fee, setFee, feeAuto != null ? `自动 ${feeAuto.toFixed(1)}` : '自动', '0.01')}
          <div className="flex flex-col gap-1">
            <label className="text-[10.5px] text-text-muted">交易商</label>
            <select className={`${inp} w-28 font-sans`} value={broker} onChange={e => setBroker(e.target.value)}>
              <option value="">无</option>
              {brokerOptions.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </div>
          {field('日期', tradeDate, setTradeDate, '', undefined, 'date')}
          <div className="flex gap-2 pb-0.5">
            <button onClick={submit} disabled={busy || !product}
              className={busy || !product ? 'px-4 py-1.5 rounded-md bg-accent text-bg text-[12.5px] font-medium opacity-50 cursor-not-allowed'
                : 'px-4 py-1.5 rounded-md bg-accent text-bg text-[12.5px] font-medium hover:opacity-90 cursor-pointer'}>
              {busy ? '开仓中…' : '开仓'}
            </button>
            <button onClick={onCancel}
              className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12.5px] hover:text-text cursor-pointer">取消</button>
          </div>
        </div>
      {err && <p className="text-[12px] text-bear m-0">{err}</p>}
      <p className="text-[10.5px] text-text-muted m-0">
        品种在 symbol_dict 维护(code=CU, exchange=SHF, trade_amount=每手乘数), 费率/保证金率在
        assets_classify 按资产小类维护(sub_assets_type 关联, 比例费率: 费率×价×乘数×手数);
        手续费留空按分类费率自动(没配则回退券商档案), 填了以手填为准。
        同一合约多空各占一行, 再次开仓同合约同方向请在该行点「加仓」。
      </p>
    </div>
  )
}
