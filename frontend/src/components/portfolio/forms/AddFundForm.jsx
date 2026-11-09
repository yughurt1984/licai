import { useEffect, useRef, useState } from 'react'
import { brokersOfType, estimateFee, isOnchainEtf, isOnchainFundKind } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import Tooltip from '../../Tooltip'
import { BROKER_COMMISSION_MIN, BROKER_COMMISSION_RATE, KEY_TO_ASSET_TYPE } from '../constants'
import { currencySymbol } from '../helpers'
import { AssetClassPicker, useAssetClass } from './AssetClassPicker'

// ============================================================
// AddFundForm — 基金添加表单(场内 ETF/LOF / 场外开放式 / REITs), 仅处理 F。
// 原名 AddAssetForm, 因基金功能为主更名; CRYPTO/BOT/WEALTH/CASH 不在此处理
// (调用条件已收窄为 F, 加密后续单独写 AddCryptoForm)。
//
// 基金(open 场外)申购口径(外扣法):
//   净申购金额 = 申购金额 / (1 + 申购费率)
//   申购手续费 = 申购金额 − 净申购金额
//   确认份额   = 净申购金额 / T日基金份额净值
// 净申购/手续费 由 申购金额+费率 自动算(灰显只读); 确认份额 还需申购净值,
// 净值没填(当日申购 T+1 未公布)时份额留空 → 后端记 pending 待结算。
// 场外基金代码存档带 .OF 后缀(019144 → 019144.OF), 报价层按该代码请求 ths。
//
// 基金类型(记账口径)不再手选: 资产类型×币种 → assets_classify 唯一小类,
// 小类推导记账口径 —— *etf_fund / *lof_fund → etf(场内市价), *reits_fund → reits,
// 其余(mm/otc/private) → open(场外申购)。
// ============================================================

function fundKindOfSub(sub) {
  const s = String(sub || '')
  if (/reits_fund$/.test(s)) return 'reits'
  if (/mm_fund$/.test(s)) return 'mm'           // 场内货币基金: 场内交易资产, 股票式记账(份额×单价+券商)
  if (/etf_fund$|lof_fund$/.test(s)) return 'etf'
  return 'open'
}

export function AddFundForm({ sub, typeKey, onDone, onCancel, brokers = [] }) {
  const assetType = KEY_TO_ASSET_TYPE[typeKey] || 'FUND'
  // 资产分类(PG assets_classify): 大类显示 + 资产类型×币种 → 唯一小类(市场)。
  // 币种选项只给当前资产类型下有的(hook 里吸附, 选不出无效组合)。
  const { rows, err, mainOptions, currencyOptions,
    main: hookMain, currency: hookCurrency, matched: hookMatched,
    setMain, setCurrency } =
    useAssetClass('fund', { preferMain: 'etf_fund' })
  // 录入入口已选具体小类(sub)时: 按该小类锁定中类/币种, 不再二次选择(全部来自 assets_classify)
  const subRow = sub ? rows.find(r => r.sub_assets_type === sub) : null
  const main = subRow ? subRow.main_assets_type : hookMain
  const currency = subRow ? subRow.currency : hookCurrency
  const matched = subRow || hookMatched
  const curSymbol = currencySymbol(currency || 'CNY')

  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('')
  const [shares, setShares] = useState('')
  const [cost, setCost] = useState('')
  const [costTouched, setCostTouched] = useState(false)
  const [selectedBroker, setSelectedBroker] = useState('')
  const [unitPrice, setUnitPrice] = useState('')
  const [fee, setFee] = useState('')
  const [feeTouched, setFeeTouched] = useState(false)
  const [feeRatePct, setFeeRatePct] = useState('')   // FUND 申购费率 %, C 类填 0
  const [hint, setHint] = useState('')
  // 场内基金的买入日期 / 场外的申购日期, 种子流水 start_date 用它
  const [tradeDate, setTradeDate] = useState(new Date().toISOString().slice(0, 10))
  // FUND 细分类型(记账口径)由分类小类推导: etf(场内, 市价) / open(场外, NAV T+1) / reits(场内, tickflow)。
  // 小类没匹配到(目录在加载)时按代码段位兜底, 与老逻辑一致。
  const fundKind = matched ? fundKindOfSub(matched.sub_assets_type)
    : (isOnchainEtf(code) ? 'etf' : 'open')

  // 表单模式与账本路由统一以 assets_classify.valuation_type 为准(数据驱动):
  //   quote = 市价估值 → 份额模式(单价×份额), 持仓账本 holdings/position_actions(POST /api/portfolio)
  //   nav   = 净值估值 → 金额模式(申购金额外扣法倒推份额), 同走 holdings(净值=价格, 申购=买入)
  //   yield/manual = 固定收益/手动估值 → 场外账本(本表单不处理, 后端会拒绝)
  // 目录未匹配(只填代码没选小类)或小类还没配 valuation_type 时, 退化为基金种类兜底
  // (场内 etf/reits=份额模式, 场外 open=金额模式)。
  const vtype = String(matched?.valuation_type || '').trim().toLowerCase()
  const isOtcFund = vtype
    ? vtype === 'nav'
    : (matched ? fundKindOfSub(matched.sub_assets_type) === 'open' : isOnchainFundKind(fundKind))
  const isInlineFund = !isOtcFund
  const toHoldings = true   // 全部基金统一走 holdings(净值=价格, 申购=买入)

  // 申购口径自动计算(外扣法): 净申购金额/手续费 由 申购金额+费率; 确认份额 还需申购净值
  const amt = parseFloat(cost) || 0
  const ratePct = feeRatePct === '' || isNaN(parseFloat(feeRatePct)) ? null : parseFloat(feeRatePct)
  const netAmount = (isOtcFund && amt > 0 && ratePct != null && ratePct >= 0) ? amt / (1 + ratePct / 100) : null
  const fundFee = netAmount != null ? amt - netAmount : null
  const navPx = parseFloat(unitPrice) || 0
  const confShares = (netAmount != null && navPx > 0) ? netAmount / navPx : null

  // 场内基金按股买: 自动估算手续费 (券商费率; 场外的手续费走申购公式, 不用费率估算)
  useEffect(() => {
    if (!isInlineFund || fundKind === 'mm' || feeTouched) return   // 场内货基无交易佣金, 不自动估算手续费
    const s = parseFloat(shares); const u = parseFloat(unitPrice)
    if (s > 0 && u > 0) {
      const est = estimateFee(s * u, brokers, selectedBroker, 'etf', assetType)
      setFee(est.toFixed(2))
    }
  }, [shares, unitPrice, feeTouched, isInlineFund, brokers, selectedBroker])

  // 场内基金按股买: 累计投入 = 单价 × 份额 + 手续费 (用户没手填时自动; 场外的申购金额是用户直接输入)
  useEffect(() => {
    if (!isInlineFund || costTouched) return
    const s = parseFloat(shares); const u = parseFloat(unitPrice); const f = parseFloat(fee) || 0
    if (s > 0 && u > 0) {
      setCost((s * u + f).toFixed(2))
    }
  }, [shares, unitPrice, fee, costTouched])

  // 基金代码自动查名: 防抖后先查 ledger.symbol_dict 底表(纯本地零联网, /api/market/symbol-name
  // 按 ticker/裸码 → code 顺序匹配), 底表没有再走行情接口兜底(顺带带出净值提示)。
  const autoSeq = useRef(0)
  const autoTimer = useRef(null)
  useEffect(() => () => clearTimeout(autoTimer.current), [])
  useEffect(() => {
    const c = code.trim().split('.')[0]      // 兼容带 .OF 后缀的输入, 查询一律用裸码
    clearTimeout(autoTimer.current)
    if (!/^\d{6}$/.test(c)) return
    const seq = ++autoSeq.current
    autoTimer.current = setTimeout(async () => {
      try {
        const { name } = await fetchJSON(`/api/market/symbol-name?code=${encodeURIComponent(c)}` +
          `&sub=${encodeURIComponent(matched?.sub_assets_type || '')}`)
        if (seq === autoSeq.current && name) { setName(name); return }
      } catch { /* 底表没有, 走行情接口兜底 */ }
      try {
        const q = await fetchJSON(`/api/assets/quote/fund/${c}`)
        if (seq === autoSeq.current && q?.name) {
          setName(q.name)
          setHint(`✓ ${q.realtime ? '估值' : '昨净值'} ${q.est_nav || q.nav}`)
        }
      } catch { /* 查不到就留手填 */ }
    }, 350)
  }, [code])

  const submit = async () => {
    // 场内基金按股票口径: 累计投入 = 份额×单价+手续费 全自动, 不要求用户填
    const effCost = (!cost && isInlineFund && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0)
      ? (parseFloat(shares) * parseFloat(unitPrice) + (parseFloat(fee) || 0)).toFixed(2)
      : cost
    if (!effCost) return alert(isInlineFund ? '请输入买入单价和份额' : '请输入申购金额')
    if (isInlineFund && !(parseFloat(unitPrice) > 0)) return alert('请输入买入单价')
    if (isInlineFund && !(parseFloat(shares) > 0)) return alert('请输入份额')
    if (isOtcFund && !(amt > 0)) return alert('请输入申购金额')
    if (isOtcFund && !confShares) return alert('请输入申购净值(确认份额=净申购金额÷净值)')
    if (!code || !name) return alert('代码/名称必填')

    // ── 全部基金 → 持仓账本(holdings/position_actions): 口径同股票 ──
    // 成本价 = 买入单价(场内)或申购净值(场外, 同在 unitPrice 字段), 后端按综合成本法重算; 小类透传
    const _shares = isOtcFund ? (confShares || 0) : parseFloat(shares || 0)
    const _costPrice = parseFloat(unitPrice || 0)
    const _fee = isOtcFund
      ? (fundFee != null && fundFee > 0.005 ? parseFloat(fundFee.toFixed(2)) : undefined)
      : isInlineFund && parseFloat(fee) > 0 ? parseFloat(fee) : undefined
    // 场外基金代码存档带 .OF 后缀(019144 → 019144.OF); 场内原样
    const storedCode = isOtcFund && /^\d{6}$/.test(code.trim()) ? `${code.trim()}.OF` : code.trim()
    try {
      await fetchJSON('/api/portfolio', {
        method: 'POST',
        body: JSON.stringify({
          stock_code: storedCode,
          stock_name: name.trim(),
          shares: _shares,
          cost_price: _costPrice,
          fee: _fee,
          trade_date: tradeDate || undefined,
          broker: selectedBroker || null,
          sub_assets_type: matched?.sub_assets_type,
        }),
      })
    } catch (e) { return alert(e.message || '保存失败') }   // 后端拒绝(路由校验/查重)要给用户看到
    onDone?.()
  }

  const inp = 'bg-bg border border-border rounded px-1.5 py-1.5 text-[12.5px] text-text outline-none focus:border-accent'
  const autoInp = `${inp} font-mono text-text-dim bg-surface-3/40`   // 自动计算灰显只读

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border space-y-2.5 text-[13px]">
      {/* 单行自适应: 各控件 flex-1 均分一行宽度(宽屏自动拉伸/窄屏收缩到 min-w 后折行兜底) */}
      <div className="flex flex-wrap gap-1.5 items-end">
        {/* 资产类型 / 币种 / 市场自动匹配: 选项全部来自 PG assets_classify; 大类由上层入口点选。
            录入入口已锁小类时显示静态徽标, 避免二次选择; 否则用下拉级联。 */}
        {subRow ? (
          <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">分类</label>
            <div className={`${inp} w-full !bg-surface-3/40 text-text-bright flex items-center`}>
              {subRow.sub_assets_type_name || sub}
            </div>
          </div>
        ) : (
          <AssetClassPicker rows={rows} err={err} mainOptions={mainOptions} currencyOptions={currencyOptions}
            matched={matched} main={main} currency={currency} inp={inp}
            onMain={v => setMain(v)} onCurrency={v => setCurrency(v)} />
        )}
        <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">基金代码</label>
          <input value={code} onChange={e => setCode(e.target.value)}
            className={`${inp} w-full font-mono`} placeholder="161226" />
        </div>
        <div className="flex flex-col gap-1 flex-[1.3] min-w-[96px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">基金名称</label>
          <input value={name} onChange={e => setName(e.target.value)} className={`${inp} w-full`}
            placeholder="自动带出" />
        </div>

        {/* 申购净值(场外) / 买入单价(场内): 先价格后数量; 场外的份额走自动确认份额(见下) */}
        <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">
            {isInlineFund ? `买入单价 ${curSymbol}` : `申购净值 ${curSymbol}`}
          </label>
          <input type="number" step="0.0001" value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
            className={`${inp} w-full font-mono`} placeholder="3.4915" />
        </div>
        {isInlineFund && (
          <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">份额</label>
            <input type="number" step="0.0001" value={shares} onChange={e => setShares(e.target.value)}
              className={`${inp} w-full font-mono`} placeholder="1500.23" />
          </div>
        )}

        {/* 场外: 申购金额 + 净申购金额/确认份额/手续费 自动计算 */}
        {isOtcFund && (
          <>
            <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">申购金额 {curSymbol}</label>
              <input type="number" step="0.01" value={cost}
                onChange={e => { setCost(e.target.value); setCostTouched(true) }}
                className={`${inp} w-full font-mono`} placeholder="10000" />
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">申购费率 %
                <Tooltip content={
                  <div className="leading-relaxed">
                    <div className="text-text-bright font-semibold mb-0.5">申购费率 (外扣法)</div>
                    <div className="text-text-dim text-[10.5px]">
                      净申购金额 = 申购金额 ÷ (1 + 费率)<br/>
                      手续费 = 申购金额 − 净申购金额<br/>
                      确认份额 = 净申购金额 ÷ T日净值<br/>
                      A类填折后实际费率(如 0.15), C类/无费填 0 或留空
                    </div>
                  </div>
                }>
                  <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
                </Tooltip>
              </label>
              <input type="number" step="0.01" value={feeRatePct} onChange={e => setFeeRatePct(e.target.value)}
                className={`${inp} w-full font-mono`} placeholder="0.15"
                title="净申购金额 = 申购金额 ÷ (1+费率); A类填折后实际费率(如0.15), C类/无申购费填0或留空" />
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">净申购金额 ¥</label>
              <input type="number" step="0.0001" readOnly tabIndex={-1}
                value={netAmount != null ? netAmount.toFixed(4) : ''}
                className={`${autoInp} w-full`} placeholder="自动计算"
                title="净申购金额 = 申购金额 ÷ (1 + 申购费率)" />
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">确认份额</label>
              <input type="number" step="0.0001" readOnly tabIndex={-1}
                value={confShares != null ? confShares.toFixed(4) : ''}
                className={`${autoInp} w-full`} placeholder="自动计算"
                title="确认份额 = 净申购金额 ÷ T日申购净值; 净值没填(当日申购)则留空 → 记 T+1 待确认" />
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">手续费 ¥</label>
              <input type="number" step="0.01" readOnly tabIndex={-1}
                value={fundFee != null ? fundFee.toFixed(2) : ''}
                className={`${autoInp} w-full`} placeholder="自动计算"
                title="申购手续费 = 申购金额 − 净申购金额" />
            </div>
            {/* 购买渠道: 场外显示(移到日期前); 场内按股票口径不显示 */}
            <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">购买渠道</label>
              <input value={platform} onChange={e => setPlatform(e.target.value)} className={`${inp} w-full`}
                placeholder="银行/天天基金" />
            </div>
            <div className="flex flex-col gap-1 flex-1 min-w-[104px]">
              <label className="text-[12px] text-text-dim whitespace-nowrap">日期</label>
              <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)}
                className={`${inp} w-full font-mono`} />
            </div>
          </>
        )}

        {/* 场内: 累计投入 = 份额×单价+手续费 自动, 可覆盖 */}
        {isInlineFund && (
          <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">
              累计投入 {curSymbol}
              {!costTouched && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0 && (
                <span className="text-[9.5px] text-accent ml-1">自动算</span>
              )}
            </label>
            <input type="number" step="0.01" value={cost}
              onChange={e => { setCost(e.target.value); setCostTouched(true) }}
              className={`${inp} w-full font-mono`} placeholder="5000" />
          </div>
        )}
        {/* 手续费(场内): 按券商费率估算, 可覆盖; 场外走申购公式(上面只读展示) */}
        {isInlineFund && (
          <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">
              手续费 ¥
              <Tooltip content={
                <div className="leading-relaxed">
                  <div className="text-text-bright font-semibold mb-0.5">手续费 (单笔)</div>
                  <div className="text-text-dim text-[10.5px]">
                    场内基金: 默认按 config.commission_rate (万 {(BROKER_COMMISSION_RATE * 10000).toFixed(2)}) + 最低 ¥{BROKER_COMMISSION_MIN}
                  </div>
                </div>
              }>
                <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
              </Tooltip>
            </label>
            <input type="number" step="0.01" value={fee}
              onChange={e => { setFee(e.target.value); setFeeTouched(true) }}
              className={`${inp} w-full font-mono`} placeholder="5.00" />
          </div>
        )}
        {isInlineFund && (
          <div className="flex flex-col gap-1 flex-1 min-w-[104px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">买入日期</label>
            <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)}
              className={`${inp} w-full font-mono`} />
          </div>
        )}
        {isInlineFund && (
          <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">券商</label>
            <select className={`${inp} w-full font-mono`} value={selectedBroker} onChange={e => setSelectedBroker(e.target.value)}>
              <option value="">无</option>
              {brokersOfType(brokers, assetType).map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </div>
        )}
        <button onClick={submit} className="shrink-0 px-3 py-1.5 rounded-md bg-accent text-bg font-semibold text-[13px] hover:opacity-90 cursor-pointer">
          保存
        </button>
        <button onClick={onCancel} className="shrink-0 px-2.5 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text cursor-pointer">
          取消
        </button>
      </div>

      {hint && <div className={`text-[10px] font-mono ${hint.startsWith('✓') ? 'text-bull' : 'text-bear'}`}>{hint}</div>}

      {/* Pending hint: 场外基金填了申购金额但净值没填(当日申购 T+1) → 先记待确认 */}
      {isOtcFund && amt > 0 && !confShares && (
        <div className="text-[10.5px] font-mono text-accent">
          💡 申购净值没填(当日申购 T+1 未公布) → 确认份额暂缺，¥{amt.toFixed(2)} 先记到 pending，净值公布后回来编辑补上自动结算。
        </div>
      )}

      {assetType === 'FUND' && (
        <div className="text-[10.5px] text-text-muted">
          {fundKind === 'mm'
            ? '场内货币基金(交易资产): 按份额 × 单价 + 券商记账(口径同股票), 行情走 ths 每日价格; 无申购费/交易佣金'
            : fundKind === 'etf'
              ? '场内基金按市价成交, 口径同股票: 成本 = 份额 × 单价 + 手续费; 行情走 ths 每日价格(ths fund/market/snapshot), 实时估值'
              : fundKind === 'reits'
                ? 'REITs 场内成交, 口径同股票; 行情走 TickFlow 实时行情(需在设置页或 TICKFLOW_API_KEY 配置 key)'
                : '场外开放式基金按申购口径: 净申购金额=申购金额÷(1+费率), 手续费=申购金额−净申购金额, 确认份额=净申购金额÷T日净值; 净值走 ths(ths fund/performance/nav); 净值没填先记待确认; 代码存档带 .OF 后缀'}
          {toHoldings
            ? ` · 估值类型 ${vtype || '(未配)'} → 计入持仓账本(holdings)`
            : matched ? ` · 估值类型 ${vtype || '(未配)'} → 计入场外账本` : ''}
        </div>
      )}

      {/* 按股买预览 (场内基金 同时填了份额 + 单价) */}
      {isInlineFund && parseFloat(shares) > 0 && parseFloat(unitPrice) > 0 && (() => {
        const s = parseFloat(shares); const u = parseFloat(unitPrice); const f = parseFloat(fee) || 0
        const gross = s * u
        const total = gross + f
        const avg = total / s
        return (
          <div className="text-[10.5px] font-mono text-bull">
            ✓ 按股买: {s.toFixed(4)} × ¥{u.toFixed(4)} = ¥{gross.toFixed(2)}
            {f > 0 && ` + 手续费 ¥${f.toFixed(2)} = ¥${total.toFixed(2)}`}
            <span className="ml-2 text-text-dim">持有成本 ¥{avg.toFixed(4)}/份</span>
          </div>
        )
      })()}
    </div>
  )
}
