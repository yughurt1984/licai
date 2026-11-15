import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fetchJSON } from '../../../hooks/useApi'
import { brokersOfType, usTodayDate } from '../../../helpers'
import { STOCK_MARKETS } from '../constants'
import { currencySymbol, stockCodeForMarket } from '../helpers'
import { AssetClassPicker, subMarket, useAssetClass } from './AssetClassPicker'

// 美股日期按美东交易日(券商成交记录上的那天), 其余市场按本地日期(A 股原逻辑不变)
const defaultDate = (market) => market === 'US' ? usTodayDate() : new Date().toISOString().slice(0, 10)
const US_DATE_HINT = '盈立成交记录上的日期; 北京时间凌晨成交算前一天'

export function AddAShareForm({ sub, initialMarket = 'A', onDone, onCancel, brokers = [] }) {
  // 分类状态在 hook 里: main/currency 派生有效组合, 币种选项只给当前 main 下有的
  const { rows, err, mainOptions, currencyOptions,
    main: hookMain, currency: hookCurrency, matched: hookMatched,
    setMain, setCurrency } =
    useAssetClass('stock', {
      preferMain: 'common_share',
      preferCurrency: initialMarket === 'HK' ? 'HKD' : initialMarket === 'US' ? 'USD' : 'CNY',
    })
  // 录入入口已选具体小类(sub)时: 按该小类锁定大类/中类/币种/市场, 不再二次选择(全部来自 assets_classify)
  const subRow = sub ? rows.find(r => r.sub_assets_type === sub) : null
  const main = subRow ? subRow.main_assets_type : hookMain
  const currency = subRow ? subRow.currency : hookCurrency
  const matched = subRow || hookMatched
  const market = subMarket(matched?.sub_assets_type)
  const curSymbol = currencySymbol(currency || 'CNY')

  const [form, setForm] = useState(() => ({
    code: '', name: '', shares: '', cost: '', fee: '',
    tradeDate: defaultDate(initialMarket === 'US' ? 'US' : 'A'),
  }))
  const [submitting, setSubmitting] = useState(false)
  const [nameLooking, setNameLooking] = useState(false)
  const [broker, setBroker] = useState('')
  // 方向(参考期货模式): 做多 = 普通股票行(买入语义); 做空 = 空头行 —— 后端记
  // direction=SHORT + OPEN 流水(卖出开仓), 盈亏按方向镜像。做多不落 direction(存量多头无方向列)。
  const [direction, setDirection] = useState('LONG')
  const [feeTouched, setFeeTouched] = useState(false)   // 用户手改过税费就不再自动覆盖
  const feeTouchedRef = useRef(false)
  useEffect(() => { feeTouchedRef.current = feeTouched }, [feeTouched])
  // 券商下拉与市场类别适配: A股只出 A股券商, 港股→港股, 美股→美股; 组合变了后已选券商不在类别里则重置
  const brokerOptions = brokersOfType(brokers, market)
  useEffect(() => {
    if (broker && !brokerOptions.some(b => b.name === broker)) setBroker('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market])

  // 名称查询, 两级: ① PG 底稿(symbol_dict, 本地零联网) ② 底稿未命中 → instruments 源
  // (assets_classify 指派, 命中后端自动登记底稿, 下次起纯本地)。quote 不在这条链里 ——
  // 行情在「确认」添加完成后才发起(见 submit), 输入过程永不打行情接口。
  // 触发时机: 输入**停顿 600ms** 后才查(输入完整代码; 敲 "7230" 的中途不会查 "723")。
  const LOOKUP_DEBOUNCE = 600
  const lookupSeq = useRef(0)
  const lookupTimer = useRef(null)
  useEffect(() => () => clearTimeout(lookupTimer.current), [])
  const nameBare = (fullCode) => /\.(HK|US|JP)$/.test(fullCode) ? fullCode.split('.')[0]
    : (fullCode.startsWith('HK.') || fullCode.startsWith('US.')) ? fullCode.split('.')[1] : fullCode
  const lookup = useCallback((nextCode, nextMarket = market) => {
    clearTimeout(lookupTimer.current)
    const seq = ++lookupSeq.current
    if (!nextCode) { setNameLooking(false); return }
    const fullCode = stockCodeForMarket(nextMarket, nextCode)
    lookupTimer.current = setTimeout(async () => {
      setNameLooking(true)
      try {
        const { name } = await fetchJSON(`/api/market/symbol-name?code=${encodeURIComponent(nameBare(fullCode))}` +
          `&sub=${encodeURIComponent(matched?.sub_assets_type || '')}`)
        if (seq === lookupSeq.current && name) {
          setForm(f => ({ ...f, name }))
          setNameLooking(false)   // 命中也要复位, 不然「查询中...」placeholder 常亮
          return
        }
      } catch { /* 底稿/instruments 都没有, 留空可手填 */ }
      if (seq === lookupSeq.current) setNameLooking(false)
    }, LOOKUP_DEBOUNCE)
  }, [market, matched?.sub_assets_type])   // sub 进依赖: 名称查询把所选小类传给 instruments 路由
  // 提交时的名称兜底: 输入过程中没查到的(未登记/负缓存), 点确认再走一次同一两级链
  const fetchName = async (nextCode, nextMarket = market) => {
    if (!nextCode) return ''
    const fullCode = stockCodeForMarket(nextMarket, nextCode)
    try {
      const { name } = await fetchJSON(`/api/market/symbol-name?code=${encodeURIComponent(nameBare(fullCode))}` +
        `&sub=${encodeURIComponent(matched?.sub_assets_type || '')}`)
      return name || ''
    } catch { return '' }
  }

  // 换「资产类型×币种」组合 = 换市场: 清空待录入内容(与老版市场下拉同款行为)
  const lastSubRef = useRef(matched?.sub_assets_type)
  useEffect(() => {
    const sub = matched?.sub_assets_type
    if (sub === undefined || sub === lastSubRef.current) return
    lastSubRef.current = sub
    lookup('')
    setForm({ code: '', name: '', shares: '', cost: '', fee: '',
      tradeDate: defaultDate(market) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matched?.sub_assets_type])

  // 税费默认值: 按 config 费率与券商收费方案由后端实时估算(数量/成本/券商/市场任一变化就重算);
  // 用户手改过(feeTouched)则以手填为准, 换代码/市场时重置为自动。
  useEffect(() => {
    setFeeTouched(false)
  }, [market, form.code])
  useEffect(() => {
    const stockCode = stockCodeForMarket(market, form.code)
    const price = parseFloat(form.cost), sh = parseFloat(form.shares)
    if (!stockCode || !(price > 0) || !(sh > 0)) return
    const t = setTimeout(async () => {
      try {
        // 费用按真实交易方向估: 做空的开仓是卖出(印花税在卖出腿)
        const q = await fetchJSON(`/api/portfolio/fee-estimate?stock_code=${encodeURIComponent(stockCode)}` +
          `&price=${price}&shares=${sh}&broker=${encodeURIComponent(broker || '')}` +
          `&action=${direction === 'SHORT' ? 'SELL' : 'BUY'}` +
          `&trade_date=${encodeURIComponent(form.tradeDate || '')}`)
        if (q?.fee != null && !feeTouchedRef.current) setForm(f => ({ ...f, fee: String(q.fee) }))
      } catch { /* 估算失败就留空(后端按费率自动算兜底) */ }
    }, 300)
    return () => clearTimeout(t)
  }, [market, form.code, form.cost, form.shares, broker, form.tradeDate, direction])

  const submit = async () => {
    const meta = STOCK_MARKETS[market]
    if (!market || !meta) return alert('该「资产类型 × 币种」组合没有匹配的资产小类, 换个组合')
    const stockCode = stockCodeForMarket(market, form.code)
    if (!form.code) return alert('请输入股票代码')
    if (market === 'A' && !/^\d{6}$/.test(stockCode)) return alert('请输入6位A股代码')
    if (market === 'HK' && !/^\d{5}\.HK$/.test(stockCode)) return alert('请输入港股5位代码')
    if (market === 'US' && !/^[A-Z0-9.]{1,8}\.US$/.test(stockCode)) return alert('请输入美股Ticker')
    if (!form.shares || parseFloat(form.shares) < meta.minShares) return alert(`持仓数量至少${meta.minShares}`)
    if (!form.cost || parseFloat(form.cost) <= 0) return alert(`请输入${direction === 'SHORT' ? '开仓' : '成本'}价`)
    setSubmitting(true)
    try {
      // 名称兜底: 输入过程中没查到的(未登记/instruments 负缓存), 提交时再走一次底稿+instruments。
      // quote 不在这查 —— 行情在添加完成后才发起(见下方冷启动), 后端建仓时也会按名自动兜底。
      let name = form.name
      if (!name) {
        name = await fetchName(form.code, market)
        if (name) setForm(f => ({ ...f, name }))
      }
      const res = await api.addHolding({
        stock_code: stockCode, stock_name: name,
        sub_assets_type: matched?.sub_assets_type || (market === 'HK' ? 'hk_comm_stock' : market === 'US' ? 'us_comm_stock' : 'a_comm_stock'),
        shares: parseFloat(form.shares), cost_price: parseFloat(form.cost),
        direction,  // 'LONG' / 'SHORT' 都下发, 后端按方向落库(多头=LONG/1, 空头=SHORT/0)
        fee: form.fee !== '' && !isNaN(parseFloat(form.fee)) ? parseFloat(form.fee) : undefined,
        trade_date: form.tradeDate || undefined,
        broker: broker || null,
      })
      if (res.message) {
        onDone?.()
        // 添加完成后再发起行情查询: 冷启动该标的报价 + write-through 落库, 持仓页刷新即有价
        fetchJSON(`/api/market/quote/${encodeURIComponent(stockCode)}`).catch(() => {})
      } else alert(res.detail || '添加失败')
    } catch (e) {
      // 后端校验不通过(持仓已存在/估值类型不对等)必须弹出来 —— 之前吞掉异常, 表现为"点了没反应"
      alert(e?.message || '添加失败')
    } finally {
      setSubmitting(false)
    }
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent'
  const metaOr = (key) => (STOCK_MARKETS[market] || {})[key]

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end">
      {/* 资产类型 / 币种 / 市场(自动匹配): 选项全部来自 PG assets_classify; 大类由上层入口点选。
          录入入口已锁小类时显示静态徽标, 避免二次选择; 否则用下拉级联。 */}
      {subRow ? (
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">分类</label>
          <div className={`${inp} w-40 !bg-surface-3/40 text-text-bright flex items-center`}>
            {subRow.sub_assets_type_name || sub}
          </div>
        </div>
      ) : (
        <AssetClassPicker rows={rows} err={err} mainOptions={mainOptions} currencyOptions={currencyOptions}
          matched={matched} main={main} currency={currency}
          onMain={setMain} onCurrency={setCurrency} inp={inp} />
      )}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">代码</label>
        <input className={`${inp} w-28 font-mono`} placeholder={metaOr('placeholder')}
          value={form.code}
          onChange={e => {
            const v = e.target.value.toUpperCase()
            setForm({ ...form, code: v })
            // 输入停顿 600ms 后才查名(底稿 → instruments): 代码敲完才查, 半截码不查。
            // 门槛: A股 6 位 / 港股 4 位起 / 日股 4 位起 / 美股 2 位起(单字符 ticker 交给确认时兜底)。
            if ((market === 'A' && v.length === 6) || (market === 'HK' && v.length >= 4)
                || (market === 'US' && v.length >= 2) || (market === 'JP' && v.length >= 4)) {
              lookup(v, market)
            } else {
              lookup('')
            }
          }} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">名称</label>
        <input className={`${inp} w-28`} placeholder={nameLooking ? '查询中...' : '可留空'}
          value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
      </div>
      {/* 方向: 多红/空绿(A股习惯涨=红), 与期货开仓表单同款交互 */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">方向</label>
        <div className="flex gap-1">
          {[['LONG', '多'], ['SHORT', '空']].map(([d, label]) => (
            <button key={d} onClick={() => setDirection(d)}
              className={`px-2.5 py-1.5 rounded-md text-[12px] font-semibold border transition-colors cursor-pointer
                ${direction === d
                  ? d === 'LONG' ? 'bg-bull/20 text-bull border-bull/60' : 'bg-bear/20 text-bear border-bear/60'
                  : 'border-border text-text-dim hover:text-text'}`}>
              {label}{d === 'LONG' ? ' (买涨)' : ' (卖跌)'}
            </button>
          ))}
        </div>
      </div>
      {/* 字段顺序与加仓/减仓表单一致: 先价格后数量 */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">
          {direction === 'SHORT' ? '开仓价' : '成本价'} <span className="text-text-muted text-[10px]">{curSymbol}</span>
        </label>
        <input type="number" className={`${inp} w-28 font-mono`}
          placeholder="12.7401" step={0.0001} value={form.cost}
          title={direction === 'SHORT' ? '卖出开仓价(做空从卖出开始, 盈亏 = 开仓价 − 现价)' : undefined}
          onChange={e => setForm({ ...form, cost: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">数量</label>
        <input type="number" className={`${inp} w-24 font-mono`}
          placeholder={market === 'US' ? '10' : '300'}
          min={metaOr('minShares')} step={metaOr('step')} value={form.shares}
          onChange={e => setForm({ ...form, shares: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">税费 <span className="text-text-muted text-[10px]">自动算·可改</span></label>
        <input type="number" className={`${inp} w-24 font-mono`}
          placeholder="0" step={0.01} min={0} value={form.fee}
          title="手续费(人民币): 默认按费率配置自动估算, 可手动覆盖; 留空由后端按费率自动算"
          onChange={e => { setFeeTouched(true); setForm({ ...form, fee: e.target.value }) }} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim" title={market === 'US' ? US_DATE_HINT : undefined}>
          {market === 'US' ? '美东交易日' : direction === 'SHORT' ? '开仓日期' : '买入日期'}
        </label>
        <input type="date" className={`${inp} w-36 font-mono`}
          value={form.tradeDate || ''}
          title={market === 'US' ? US_DATE_HINT : undefined}
          onChange={e => setForm({ ...form, tradeDate: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">券商</label>
        <select className={`${inp} w-28`} value={broker} onChange={e => setBroker(e.target.value)}>
          <option value="">无</option>
          {brokerOptions.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
        </select>
      </div>
      <button onClick={submit} disabled={submitting}
        className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 transition-opacity disabled:opacity-50 cursor-pointer">
        {submitting ? '...' : direction === 'SHORT' ? '开仓' : '确认'}
      </button>
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
        取消
      </button>
      <div className="text-[10px] text-text-muted pb-1">
        {market ? STOCK_MARKETS[market].hint : '代码格式随匹配到的市场而定'}
        {' · '}{direction === 'SHORT' ? '开仓价' : '成本价'}按{matched?.currency || 'CNY'}({curSymbol})录入，总资产自动折人民币
        {market === 'US' && <> · 日期按美东填({US_DATE_HINT})</>}
        {direction === 'SHORT' && <> · 做空: 卖出开仓、买入平仓, 价跌=赚; 流水在该行「开仓/平仓」里记</>}
      </div>
    </div>
  )
}
