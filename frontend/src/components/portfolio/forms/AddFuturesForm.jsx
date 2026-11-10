import { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '../../../hooks/useApi'
import { brokersOfType } from '../../../helpers'
import { AssetClassPicker, useAssetClass } from './AssetClassPicker'

// 衍生品开仓表单(品种 + 月份拼接模式): 顶部标签(大类名)来自 assets_classify, 资产类型
// 默认期货/币种默认 CNY; 全部字段压缩在**一行**内(窄屏自动折行兜底):
// 大类标签 | 资产类型/币种/市场(assets_classify) | 品种代码 | 名称(防抖查 symbol_dict) |
// 方向(多红/空绿) | 月份 | 合约代码(自动拼接 CU2601.SHF) | 手数/乘数/开仓价/手续费 |
// 保证金(参考) | 券商 | 日期 | 开仓/取消。
// 品种搜索只在 symbol_dict 品种行里做(不联网、不弹下拉); 提交走 POST /api/portfolio
// (main_assets_type=futures|options + direction), 后端 seed OPEN 流水并重算综合成本。
export function AddFuturesForm({ sub, brokers, onDone, onCancel }) {
  // 资产分类(PG assets_classify): 衍生品大类 + 中类(futures/options/warrant) × 币种 → 唯一小类。
  // 中类由用户在表单里选(不再靠 kind 枚举锁定) —— 期货/期权同属 derivatives, 一个入口覆盖。
  const { rows, err: clsErr, mainOptions, currencyOptions,
    matched: hookMatched, main: hookMain,
    setMain, setCurrency } =
    useAssetClass('derivatives', { preferMain: 'futures' })   // 默认期货; 币种 hook 默认 CNY
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
  const [strikePrice, setStrikePrice] = useState('')
  const [optionType, setOptionType] = useState('CALL')  // 期权类型 CALL/PUT(合约属性, 与 direction 正交)
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
  // 品种名称自动带出: 防抖 350ms, 只查 symbol_dict 品种行(用户人工维护, 不查同花顺 API)
  // 命中(精确代码优先, 否则首个)自动把名称填进输入框; 每手乘数默认带品种乘数。
  useEffect(() => {
    const q = query.trim()
    if (q.length < 1) { setProduct(null); setSearching(false); return undefined }
    const seq = ++lookupSeq.current
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        // 期货/期权品种由用户维护在 symbol_dict, 只查数据库, 不走 ths API
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

  // 合约代码拼接: 品种(symbol_dict.code, 如 IF/CU) + 月份(YYMM) + 交易所后缀
  // symbol_dict.suffix 由你直接维护为同花顺后缀(SHF/CFE...), 原样拼接; 期货品种只查库, 报价那一步才用 ticker 取价
  // 期权拼码: 品种+年月-C/P-行权价.后缀 (如 IO2701-C-4000.CFE)
  // C/P 由独立 optionType 决定, 与 direction(权利方/义务方) 正交: 四态都支持(买看涨/卖看涨/买看跌/卖看跌)
  const cp = optionType === 'PUT' ? 'P' : 'C'
  const strikeNum = parseFloat(strikePrice)
  const thscode = product && month && (!isOpt || strikeNum > 0)
    ? (isOpt
        ? `${product.code}${month}-${cp}-${strikeNum}.${product.suffix || ''}`.toUpperCase()
        : `${product.code}${month}${product.suffix ? '.' + product.suffix : ''}`.toUpperCase())
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
    // 期权页隐藏乘数输入 → 每手乘数改从品种行 trade_amount 自动取(需先在 symbol_dict 维护)
    const mult = parseFloat(multiplier) || (isOpt && product?.trade_amount ? parseFloat(product.trade_amount) : 0)
    if (!(mult > 0)) return setErr(isOpt ? '期权品种未配置每手乘数: 请在 symbol_dict 维护 trade_amount' : '请填每手乘数')
    const px = parseFloat(price)
    if (!(px > 0)) return setErr('请填开仓价格')
    if (isOpt && !(strikeNum > 0)) return setErr('请填行权价')
    setBusy(true); setErr('')
    try {
      const body = {
        stock_code: thscode,
        stock_name: (name.trim() || product.name || '') + month || thscode,
        shares: sh,
        cost_price: px,
        main_assets_type: isOpt ? 'options' : 'futures',
        ...(isOpt ? { strike_price: strikeNum, option_type: optionType } : {}),
        direction,
        contract_multiplier: mult,
        trade_date: tradeDate,
        sub_assets_type: subType || undefined,   // 分类归口目录(valuation_type=quote 路由校验 + 统一命名)
      }
      if (broker) body.broker = broker
      if (fee !== '' && parseFloat(fee) >= 0) body.fee = parseFloat(fee)   // 留空 = 后端按品种费率自动
      await fetchJSON('/api/portfolio', { method: 'POST', body: JSON.stringify(body) })
      onDone('开仓成功')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }


  const inp = 'bg-bg border border-border rounded px-1.5 py-1.5 text-[13px] text-text font-mono outline-none focus:border-accent'
  const field = (label, value, set, placeholder, step = '0.01', type = 'number', w = 'w-[62px]', grow = false) => (
    <div className={`flex flex-col gap-0.5 min-w-0${grow ? ' flex-1' : ''}`}>
      <label className="text-[11px] text-text-muted truncate" title={label}>{label}</label>
      <input type={type} step={step} min="0" className={`${inp} ${grow ? 'w-full min-w-[70px]' : w} text-right`} placeholder={placeholder}
        value={value} onChange={e => set(e.target.value)} />
    </div>
  )

  return (
    <div className="rounded-lg border border-border-med bg-surface-2/60 p-2 fade-up">
      {/* 一行布局: 顶部标签(大类, 来自数据库) + 全部字段同排; 窄屏自动折行兜底 */}
      <div className="flex flex-wrap items-end gap-1.5">
        {subRow ? (
          <div className="flex flex-col gap-0.5">
            <label className="text-[11px] text-text-muted">分类</label>
            <div className={`${inp} w-[76px] !bg-surface-3/40 text-text-bright flex items-center justify-center text-center`}
              title={subRow.sub_assets_type_name || sub}>
              {(subRow.sub_assets_type_name || sub).slice(0, 6)}
            </div>
          </div>
        ) : (
          <AssetClassPicker rows={rows} err={clsErr}
            mainOptions={mainOptions}
            currencyOptions={currencyOptions}
            matched={matched} main={main} inp={inp} compact
            onMain={setMain} onCurrency={setCurrency} />
        )}
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-text-muted">{isOpt ? '期权品种' : '期货品种'}</label>
          <input className={`${inp} w-[56px] font-sans`} autoFocus placeholder="CU / 沪铜"
            value={query} onChange={e => setQuery(e.target.value)} />
        </div>
        <div className="flex flex-col gap-0.5 flex-1 min-w-[90px] max-w-[108px]">
          <label className="text-[11px] text-text-muted truncate">名称</label>
          <input className={`${inp} w-full font-sans`} placeholder={searching ? '查询中…' : '自动带出'}
            value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-text-muted">{isOpt ? '方向(权利方/义务方)' : '方向'}</label>
          <div className="flex gap-0.5">
            {(isOpt
              ? [['LONG', '权利方', '买期权(权利方): 付权利金, 最大亏损=权利金'],
                 ['SHORT', '义务方', '卖期权(义务方/写权): 收权利金, 有履约义务(卖方保证金由券商另行计)']]
              : [['LONG', '多', '买涨: 价格涨=盈利'], ['SHORT', '空', '卖跌: 价格跌=盈利']]
            ).map(([d, label, tip]) => (
              <button key={d} onClick={() => setDirection(d)} title={tip}
                className={`px-3 py-1.5 rounded text-[14px] font-semibold border transition-colors cursor-pointer
                  ${direction === d
                    ? d === 'LONG' ? 'bg-bull/20 text-bull border-bull/60' : 'bg-bear/20 text-bear border-bear/60'
                    : 'border-border text-text-dim hover:text-text'}`}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {isOpt && (
          <div className="flex flex-col gap-0.5">
            <label className="text-[11px] text-text-muted">类型(C/P)</label>
            <div className="flex gap-0.5">
              {[['CALL', 'C 认购'], ['PUT', 'P 认沽']].map(([t, label]) => (
                <button key={t} onClick={() => setOptionType(t)} title={t === 'CALL' ? '认购期权(CALL)' : '认沽期权(PUT)'}
                  className={`px-2.5 py-1.5 rounded text-[13px] font-semibold border transition-colors cursor-pointer
                    ${optionType === t
                      ? 'bg-accent/20 text-accent border-accent/60'
                      : 'border-border text-text-dim hover:text-text'}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-text-muted">月份</label>
          <select className={`${inp} w-[64px]`} value={month} onChange={e => setMonth(e.target.value)}>
            {months.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        {isOpt && field('行权价', strikePrice, setStrikePrice, '合约行权价', '0.001', 'number', 'w-[72px]')}
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-text-muted">合约代码</label>
          <span className={`${inp} w-[136px] !bg-surface-3/60 text-text-bright select-all text-center`}
            title={thscode || '选品种+月份后自动拼接'}>{thscode || '—'}</span>
        </div>
        {field('手数', lots, setLots, '1', '1')}
        {!isOpt && field('乘数', multiplier, setMultiplier, product?.trade_amount ? String(product.trade_amount) : '5', '1')}
        {field('开仓价', price, setPrice, '现价', '0.01', 'number', 'w-[78px]')}
        {field('手续费', fee, setFee, feeAuto != null ? feeAuto.toFixed(1) : '自动', '0.01')}
        {!isOpt && (
          <div className="flex flex-col gap-0.5">
            <label className="text-[11px] text-text-muted truncate" title="开仓价 × 乘数 × 手数 × 保证金率(交易所口径)">保证金</label>
            <span className={`${inp} w-[92px] !bg-surface-3/40 text-warn text-right select-all`}
              title="开仓价 × 每手乘数 × 手数 × 保证金率(交易所口径, 期货公司加收)">
              {marginEst != null ? `¥${marginEst.toFixed(0)}` : '—'}
            </span>
          </div>
        )}
        <div className="flex flex-col gap-0.5">
          <label className="text-[11px] text-text-muted">券商</label>
          <select className={`${inp} w-[96px] font-sans`} value={broker} onChange={e => setBroker(e.target.value)}>
            <option value="">无</option>
            {brokerOptions.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
          </select>
        </div>
        {field('日期', tradeDate, setTradeDate, '', undefined, 'date', 'w-[112px]')}
        <div className="flex gap-1 pb-0.5">
          <button onClick={submit} disabled={busy || !product}
            className={`px-3 py-1 rounded-md bg-accent text-bg text-[13px] font-medium ${busy || !product ? 'opacity-50 cursor-not-allowed' : 'hover:opacity-90 cursor-pointer'}`}>
            {busy ? '开仓中…' : '开仓'}
          </button>
          <button onClick={onCancel}
            className="px-2 py-1 rounded-md border border-border text-text-dim text-[13px] hover:text-text cursor-pointer">取消</button>
        </div>
      </div>
      {/* 条件提示与说明: 单行截断, 悬停看全文, 不占默认布局 */}
      {!product && query.trim() && !searching && (
        <p className="text-[11.5px] text-warn m-0 mt-1 truncate"
          title={`当前分类「${matched?.sub_assets_type_name || subType}」下没有品种行: 需在 symbol_dict 维护 code(如 CU)/name/exchange(如 SHF), 并确认 sub_assets_type=${subType || '(未匹配)'}`}>
          {`「${matched?.sub_assets_type_name || subType || '未匹配'}」下没有品种行 — 需在 symbol_dict 维护 code/exchange`}
        </p>
      )}
      {err && <p className="text-[12.5px] text-bear m-0 mt-1 truncate" title={err}>{err}</p>}
      <p className="text-[11px] text-text-muted m-0 mt-1 truncate"
        title="品种在 symbol_dict 维护(code=CU, exchange=SHF, trade_amount=每手乘数); 费率/保证金率在 assets_classify 按资产小类维护; 手续费留空按分类比例费率自动(没配则回退券商档案); 同一合约多空各占一行, 再次开仓同合约同方向请在该行点「加仓」">
        同一合约多空各占一行 · 再次开仓同合约同方向在该行「加仓」 · 手续费留空按分类费率自动
      </p>
    </div>
  )
}
