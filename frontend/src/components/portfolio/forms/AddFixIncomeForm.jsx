import { useEffect, useRef, useState } from 'react'
import { brokersOfType } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import Tooltip from '../../Tooltip'
import { currencySymbol } from '../helpers'
import { AssetClassPicker, useAssetClass } from './AssetClassPicker'

// ============================================================
// AddFixIncomeForm — 固定收益添加表单 (理财 / 债券全谱系), 仅处理 W 桶。
//
// 数据源: assets_classify 大类 fix-income (wealth 理财 / gov_bond 国债 /
// corp_bond 企业债 / trading_bond 交易所债 / tran_bond 可转债…),
// 中类×币种 → 唯一小类自动匹配, 全部目录驱动, 不硬编码资产种类。
//
// 双模式按 matched.valuation_type 路由入库(与 AddFundForm 的 quote/nav 双模式同一先例):
//   yield  → 固定收益账本 fix_income_assets (POST /api/fix-income)
//            本金 + 年化 + 期限模型, 无份额无市价, 单利计提估值
//   quote  → 持仓账本 holdings/position_actions (POST /api/portfolio)
//            市价交易型债券, **口径完全同股票录入**(AddAShareForm):
//            代码查名 → 价格 → 数量(张) → 税费(后端 fee-estimate 实时估算) → 日期/券商。
//            价格口径按中类分两种(A股市场规则):
//              可转债 tran_bond: 全价直接报价 → 单个「买入价格」字段
//              交易所债 trading_bond: 净价报价 → 净价 + 应计利息 = 全价(自动), 成本落全价
// 后端双端校验兜底(portfolio 拒 yield / fix-income 拒 quote), 表单路由错不了。
// 展示层两类资产自动同归「固定收益」分组(typeFromClassify 按大类派生, 不看估值类型)。
// ============================================================

const INTEREST_TYPES = [
  { v: 'bullet', label: '到期一次付息' },
  { v: 'monthly', label: '按月付息' },
  { v: 'quarterly', label: '按季付息' },
  { v: 'semiannual', label: '半年付息' },
  { v: 'annual', label: '每年付息' },
]

export function AddFixIncomeForm({ sub, onDone, onCancel, brokers = [] }) {
  // 资产分类(PG assets_classify): 大类固定 fix-income, 中类下拉自动同步(全部中类,
  // 含 quote 型交易所债/可转债 —— 模式由小类 valuation_type 决定)。
  // 币种选项只给当前中类下有的(hook 里吸附, 选不出无效组合)。
  const { rows, err, mainOptions, currencyOptions,
    main: hookMain, currency: hookCurrency, matched: hookMatched,
    setMain, setCurrency } =
    useAssetClass('fix-income', { preferMain: 'wealth' })
  // 录入入口已选具体小类(sub)时: 按该小类锁定中类/币种, 不再二次选择(全部来自 assets_classify)
  const subRow = sub ? rows.find(r => r.sub_assets_type === sub) : null
  const main = subRow ? subRow.main_assets_type : hookMain
  const currency = subRow ? subRow.currency : hookCurrency
  const matched = subRow || hookMatched
  const curSymbol = currencySymbol(currency || 'CNY')

  // 模式由目录小类的估值类型决定(数据驱动): quote = 市价交易型债券 → 持仓账本;
  // 其余(yield) = 固定收益计提型 → 固收账本。目录没配 valuation_type 时按 yield 兜底。
  const vtype = String(matched?.valuation_type || '').trim().toLowerCase()
  const isQuote = vtype === 'quote'
  const isYield = !isQuote
  const isBondMain = ['bond', 'gov_bond', 'corp_bond', 'trading_bond', 'tran_bond'].includes(main)

  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [nameLooking, setNameLooking] = useState(false)
  const [platform, setPlatform] = useState('')
  const [principal, setPrincipal] = useState('')
  const [yieldPct, setYieldPct] = useState('')       // 年化收益率/票面利率 %
  const [termDays, setTermDays] = useState('')       // 期限(天); 可空 = 开放式
  const [valueDate, setValueDate] = useState(new Date().toISOString().slice(0, 10))
  const [maturityDate, setMaturityDate] = useState('')  // 到期日; 可空
  const [interestType, setInterestType] = useState('bullet')
  const [riskLevel, setRiskLevel] = useState('')
  const [note, setNote] = useState('')
  // quote 模式(市价债券)字段 —— 口径同 AddAShareForm, 字段顺序也一致(先价格后数量)
  const [unitPrice, setUnitPrice] = useState('')     // 买入净价(每张; 可转债全价报价则=全价, 应计填 0)
  const [accrued, setAccrued] = useState('')         // 应计利息(每张); 净价交易债券必填项之外的可选项
  const [shares, setShares] = useState('')           // 份额(张), 10 张 = 1 手
  const [fee, setFee] = useState('')                 // 税费 ¥ (后端 fee-estimate 实时估算, 可覆盖)
  const [feeTouched, setFeeTouched] = useState(false)
  const [selectedBroker, setSelectedBroker] = useState('')
  // 期限 ↔ 到期日互推: 手填过哪头就不再动哪头(程序回填不碰 touched 标记)
  const termTouched = useRef(false)
  const maturityTouched = useRef(false)

  // 期限 → 到期日 (到期日没手填过; 仅 yield 模式)
  useEffect(() => {
    if (isQuote) return
    const t = parseInt(termDays)
    if (maturityTouched.current || !t || t <= 0 || !valueDate) return
    const d = new Date(`${valueDate}T00:00:00`)
    d.setDate(d.getDate() + t)
    setMaturityDate(d.toISOString().slice(0, 10))
  }, [termDays, valueDate, isQuote])

  // 到期日 → 期限 (期限没手填过; 仅 yield 模式)
  useEffect(() => {
    if (isQuote) return
    if (termTouched.current || !maturityDate || !valueDate) return
    const diff = Math.round((new Date(`${maturityDate}T00:00:00`) - new Date(`${valueDate}T00:00:00`)) / 86400000)
    if (Number.isFinite(diff) && diff > 0) setTermDays(String(diff))
  }, [maturityDate, valueDate, isQuote])

  // ── quote 模式: 债券数值派生 ──
  // 价格口径按中类(A股市场规则): 交易所债 trading_bond 净价报价 → 全价 = 净价 + 应计;
  // 其余(可转债 tran_bond 等)全价直接报价 → 单个买入价, 无净价/应计拆分。
  const isNetPrice = isQuote && main === 'trading_bond'
  const accruedNum = isNetPrice ? (parseFloat(accrued) || 0) : 0
  const netNum = parseFloat(unitPrice) || 0
  const dirtyPrice = netNum > 0 ? netNum + accruedNum : null   // 每张全价
  const quotePrice = dirtyPrice                                // 提交的每张成本(全价口径)
  const sNum = parseFloat(shares) || 0
  const feeNum = parseFloat(fee) || 0
  const quoteGross = (quotePrice != null && sNum > 0) ? quotePrice * sNum : null
  const quoteTotal = quoteGross != null ? quoteGross + feeNum : null

  // 税费: 按后端费率配置实时估算(同 AddAShareForm /api/portfolio/fee-estimate),
  // 数量/价格/应计/券商/代码任一变化就重算; 用户手改过(feeTouched)则以手填为准。
  const feeTouchedRef = useRef(false)
  useEffect(() => { feeTouchedRef.current = feeTouched }, [feeTouched])
  useEffect(() => {
    // 换代码/小类 → 重置为自动估算
    setFeeTouched(false)
  }, [code, matched?.sub_assets_type])
  useEffect(() => {
    if (!isQuote) return
    const fullCode = code.trim().toUpperCase()
    if (!fullCode || quotePrice == null || !(sNum > 0)) return
    const t = setTimeout(async () => {
      try {
        const q = await fetchJSON(`/api/portfolio/fee-estimate?stock_code=${encodeURIComponent(fullCode)}` +
          `&price=${quotePrice}&shares=${sNum}&broker=${encodeURIComponent(selectedBroker || '')}` +
          `&action=BUY&trade_date=${encodeURIComponent(valueDate || '')}`)
        if (q?.fee != null && !feeTouchedRef.current) setFee(String(q.fee))
      } catch { /* 估算失败就留空(后端按费率自动算兜底) */ }
    }, 300)
    return () => clearTimeout(t)
  }, [isQuote, code, quotePrice, sNum, selectedBroker, valueDate])

  // ── quote 模式: 债券代码自动查名(防抖 + 只认最后一次, 同 AddAShareForm) ──
  // 两级: ① PG 搜股底表(symbol_dict, 本地零联网) ② 底表没有 → instruments 源(命中后端登记底稿)。
  // quote 不在这条链里 —— 行情在「确认」添加完成后才发起(见 submit), 输入过程不打行情接口
  // (此前这里兜底打 /api/market/quote/<裸码>, 半截码会误判市场还白耗源配额)。
  const lookupSeq = useRef(0)
  const lookupTimer = useRef(null)
  useEffect(() => () => clearTimeout(lookupTimer.current), [])
  useEffect(() => {
    if (!isQuote) return
    const c = code.trim().split('.')[0]      // 兼容带后缀的输入, 查询一律用裸码
    clearTimeout(lookupTimer.current)
    if (!/^\d{5,6}$/.test(c)) { setNameLooking(false); return }
    const seq = ++lookupSeq.current
    lookupTimer.current = setTimeout(async () => {
      setNameLooking(true)
      try {
        const { name } = await fetchJSON(`/api/market/symbol-name?code=${encodeURIComponent(c)}` +
          `&sub=${encodeURIComponent(matched?.sub_assets_type || '')}`)
        if (seq === lookupSeq.current && name) { setName(name); setNameLooking(false); return }
      } catch { /* 底稿/instruments 都没有, 留空可手填 */ }
      if (seq === lookupSeq.current) setNameLooking(false)
    }, 350)
  }, [code, isQuote, matched?.sub_assets_type])

  // ── yield 模式预览: 单利计提, 与后端 _accrued_estimate 同式 ──
  const p = parseFloat(principal) || 0
  const r = parseFloat(yieldPct) / 100
  const t = parseInt(termDays) || 0
  const hasRate = Number.isFinite(r) && r > 0
  const expectedMaturity = (p > 0 && hasRate && t > 0) ? p * (1 + r * t / 365) : null
  const dailyInterest = (p > 0 && hasRate) ? p * r / 365 : null

  const submit = async () => {
    if (!matched) return alert('请选择资产类型与币种(自动匹配小类)')
    if (isQuote) {
      // 市价债券 → 持仓账本(holdings): 口径同股票 —— 成本价 = 每张支付价(全价口径), 真实每张支付
      if (!code.trim()) return alert('请输入债券代码')
      if (quotePrice == null || !(quotePrice > 0)) return alert(isNetPrice ? '请输入买入净价' : '请输入买入价格')
      if (!(sNum > 0)) return alert('请输入数量(张)')
      try {
        await fetchJSON('/api/portfolio', {
          method: 'POST',
          body: JSON.stringify({
            stock_code: code.trim().toUpperCase(),
            stock_name: name.trim(),
            shares: sNum,
            cost_price: dirtyPrice,               // 全价落库: 持仓成本 = 实际支付
            fee: feeNum > 0 ? feeNum : undefined,
            trade_date: valueDate || undefined,
            broker: selectedBroker || null,
            sub_assets_type: matched.sub_assets_type,
          }),
        })
      } catch (e) { return alert(e.message || '保存失败') }
      onDone?.()
      // 添加完成后再发起行情查询: 冷启动该标的报价 + write-through 落库, 持仓页刷新即有价
      fetchJSON(`/api/market/quote/${encodeURIComponent(code.trim().toUpperCase())}`).catch(() => {})
      return
    }
    // yield 模式 → 固定收益账本(fix_income_assets)
    if (!name.trim()) return alert('请输入名称')
    if (!(p > 0)) return alert('请输入买入本金')
    if (!hasRate) return alert('请输入年化收益率(理财=业绩比较基准 / 债券=票面利率)')
    try {
      await fetchJSON('/api/fix-income', {
        method: 'POST',
        body: JSON.stringify({
          code: code.trim() || name.trim(),
          name: name.trim(),
          principal: p,
          sub_assets_type: matched.sub_assets_type,
          platform: platform.trim() || '',
          currency: currency || 'CNY',
          annual_yield_rate: r,
          rate_type: 'fixed',
          value_date: valueDate || undefined,
          maturity_date: maturityDate || undefined,
          term_days: t > 0 ? t : undefined,
          interest_type: interestType,
          risk_level: riskLevel || undefined,
          note: note.trim() || '',
        }),
      })
    } catch (e) { return alert(e.message || '保存失败') }
    onDone?.()
  }

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-[13px] text-text outline-none focus:border-accent'

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border flex flex-wrap gap-2 items-end text-[13px]">
      {/* 资产类型(中类) / 币种 / 市场自动匹配: 选项全部来自 PG assets_classify
          fix-income 大类, 中类加行即生效, 前端零改动。已锁小类时显示静态徽标。 */}
      {subRow ? (
        <div className="flex flex-col gap-1">
          <label className="text-[11px] text-text-dim">分类</label>
          <div className={`${inp} w-40 !bg-surface-3/40 text-text-bright flex items-center`}>
            {subRow.sub_assets_type_name || sub}
          </div>
        </div>
      ) : (
        <AssetClassPicker rows={rows} err={err} mainOptions={mainOptions} currencyOptions={currencyOptions}
          matched={matched} main={main} currency={currency} inp={inp}
          onMain={v => setMain(v)} onCurrency={v => setCurrency(v)} />
      )}

      {/* 代码 / 名称: quote 模式防抖查名(底表优先→行情兜底), yield 模式手填 */}
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">{isQuote || isBondMain ? '债券代码' : '产品代码'}</label>
        <input className={`${inp} w-28 font-mono`} placeholder={isQuote ? '113050' : isBondMain ? '019547.SH' : '可空'}
          value={code} onChange={e => setCode(e.target.value.toUpperCase())} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] text-text-dim">{isQuote || isBondMain ? '债券名称' : '产品名称'}{!isQuote && ' *'}</label>
        <input className={`${inp} w-28`} placeholder={nameLooking ? '查询中...' : (isQuote ? '可留空' : isBondMain ? '24国债09' : '招银季季宝')}
          value={name} onChange={e => setName(e.target.value)} />
      </div>

      {/* ── yield 模式: 本金 → 年化 → 期限 (固定收益三要素) ── */}
      {isYield && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              买入本金 <span className="text-text-muted text-[10px]">{curSymbol}</span>
            </label>
            <input type="number" step="0.01" value={principal}
              onChange={e => setPrincipal(e.target.value)}
              className={`${inp} w-28 font-mono`} placeholder="50000" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {isBondMain ? '票面利率 %' : '年化收益率 %'} *
              <Tooltip content={
                <div className="leading-relaxed">
                  <div className="text-text-bright font-semibold mb-0.5">{isBondMain ? '票面利率' : '年化收益率'}</div>
                  <div className="text-text-dim text-[10.5px]">
                    {isBondMain
                      ? '债券票面利率(单利, 按持有天数计提应计利息)'
                      : '理财填业绩比较基准/预期年化(单利计提估值); 到期兑付按它估算最后一笔利息'}
                  </div>
                </div>
              }>
                <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
              </Tooltip>
            </label>
            <input type="number" step="0.001" value={yieldPct} onChange={e => setYieldPct(e.target.value)}
              className={`${inp} w-20 font-mono`} placeholder="2.65" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">期限 (天)</label>
            <input type="number" step="1" value={termDays}
              onChange={e => { termTouched.current = true; setTermDays(e.target.value) }}
              className={`${inp} w-20 font-mono`} placeholder="93"
              title="封闭期/存续期天数; 填了自动推算到期日, 开放式可空" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">起息日</label>
            <input type="date" value={valueDate} onChange={e => setValueDate(e.target.value)}
              className={`${inp} w-36 font-mono`} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">到期日</label>
            <input type="date" value={maturityDate}
              onChange={e => { maturityTouched.current = true; setMaturityDate(e.target.value) }}
              className={`${inp} w-36 font-mono`}
              title="填了自动反算期限(天); 开放式理财可空" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">付息方式</label>
            <select value={interestType} onChange={e => setInterestType(e.target.value)}
              className={`${inp} w-32`}>
              {INTEREST_TYPES.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">风险等级</label>
            <select value={riskLevel} onChange={e => setRiskLevel(e.target.value)}
              className={`${inp} w-20`} title="可空">
              <option value="">无</option>
              {['R1', 'R2', 'R3', 'R4', 'R5'].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">平台/发行机构</label>
            <input value={platform} onChange={e => setPlatform(e.target.value)} className={`${inp} w-28`}
              placeholder="招商银行 / 支付宝" />
          </div>
        </>
      )}

      {/* ── quote 模式: 市价债券, 口径完全同股票录入(AddAShareForm) ──
          字段顺序与股票表单一致: 价格(成本价位) → 数量 → 税费 → 日期 → 券商。
          价格口径按中类(A股规则): 交易所债=净价+应计→全价(自动); 可转债=全价直接报价。 */}
      {isQuote && (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              {isNetPrice ? '买入净价' : '买入价格'} <span className="text-text-muted text-[10px]">{curSymbol}</span>
            </label>
            <input type="number" className={`${inp} w-24 font-mono`}
              placeholder={isNetPrice ? '101.50' : '113.50'} step={0.0001} value={unitPrice}
              title={isNetPrice ? '每张净价(交易所债券净价报价)' : '可转债按全价直接报价, 填买入价即可(每张)'}
              onChange={e => setUnitPrice(e.target.value)} />
          </div>
          {isNetPrice && (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] text-text-dim">
                  应计利息
                  <Tooltip content={
                    <div className="leading-relaxed">
                      <div className="text-text-bright font-semibold mb-0.5">应计利息 (每张)</div>
                      <div className="text-text-dim text-[10.5px]">全价 = 净价 + 应计利息</div>
                      <div className="text-text-dim text-[10.5px] mt-0.5">
                        应计利息 ≈ 面值 100 × 票面利率 × 距上次付息天数 ÷ 365
                      </div>
                      <div className="text-text-dim text-[10.5px] mt-1">
                        从行情软件当日报价页抄应计利息; 留空按 0 计
                      </div>
                    </div>
                  }>
                    <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
                  </Tooltip>
                </label>
                <input type="number" className={`${inp} w-20 font-mono`}
                  placeholder="0.00" step={0.0001} value={accrued}
                  title="应计利息(每张); 全价 = 净价 + 应计"
                  onChange={e => setAccrued(e.target.value)} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[11px] text-text-dim">全价 <span className="text-text-muted text-[10px]">自动</span></label>
                <input type="number" readOnly tabIndex={-1}
                  className={`${inp} w-24 font-mono text-text-dim bg-surface-3/40`}
                  value={dirtyPrice != null ? dirtyPrice.toFixed(4) : ''} placeholder="自动"
                  title="全价 = 净价 + 应计利息; 成本价按全价落库(真实每张支付)" />
              </div>
            </>
          )}
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">
              数量 <span className="text-text-muted text-[10px]">张</span>
            </label>
            <input type="number" className={`${inp} w-20 font-mono`}
              placeholder="100" min={10} step={10} value={shares}
              title="债券按张交易(1张=100元面值), 10张 = 1手"
              onChange={e => setShares(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">税费 <span className="text-text-muted text-[10px]">自动算·可改</span></label>
            <input type="number" className={`${inp} w-20 font-mono`}
              placeholder="0" step={0.01} min={0} value={fee}
              title="手续费(人民币): 默认按费率配置自动估算, 可手动覆盖; 留空由后端按费率自动算"
              onChange={e => { setFeeTouched(true); setFee(e.target.value) }} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">买入日期</label>
            <input type="date" className={`${inp} w-36 font-mono`}
              value={valueDate || ''} onChange={e => setValueDate(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-text-dim">券商</label>
            <select className={`${inp} w-28`} value={selectedBroker} onChange={e => setSelectedBroker(e.target.value)}>
              <option value="">无</option>
              {brokersOfType(brokers, 'FUND').map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </div>
        </>
      )}

      <button onClick={submit} className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 cursor-pointer">
        保存
      </button>
      <button onClick={onCancel}
        className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text cursor-pointer">
        取消
      </button>

      {/* ── yield 模式第二行: 备注 ── */}
      {isYield && (
        <div className="flex flex-col gap-1 w-full">
          <label className="text-[11px] text-text-dim">备注</label>
          <input value={note} onChange={e => setNote(e.target.value)} className={`${inp} w-full max-w-[480px]`}
            placeholder="锁定期 / 申赎规则 / 其他说明 (可空)" />
        </div>
      )}

      {/* 单利计提预览 (yield 模式, 与后端 _accrued_estimate 同式): 到期本息 + 日利息 */}
      {isYield && (expectedMaturity != null || dailyInterest != null) && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 w-full text-[10.5px] font-mono text-bull">
          {expectedMaturity != null && (
            <span>✓ 预计到期本息 ¥{expectedMaturity.toFixed(2)}
              <span className="text-text-dim ml-1.5">
                (本金 ¥{p.toFixed(2)} + 利息 ¥{(expectedMaturity - p).toFixed(2)} · {t}天单利)
              </span>
            </span>
          )}
          {dailyInterest != null && (
            <span className="text-text-dim">日息 ≈ ¥{dailyInterest.toFixed(2)}</span>
          )}
        </div>
      )}

      {/* 按股买预览 (quote 模式, 价格+数量齐了) */}
      {isQuote && quoteTotal != null && (() => {
        const avg = sNum > 0 ? quoteTotal / sNum : 0
        return (
          <div className="w-full text-[10.5px] font-mono text-bull">
            ✓ {sNum} 张 × {isNetPrice ? '全价' : ''}¥{quotePrice.toFixed(4)}
            {isNetPrice && accruedNum > 0 && ` (净价 ¥${netNum.toFixed(4)} + 应计 ¥${accruedNum.toFixed(4)})`}
            = ¥{quoteGross.toFixed(2)}
            {feeNum > 0 && ` + 税费 ¥${feeNum.toFixed(2)} = ¥${quoteTotal.toFixed(2)}`}
            <span className="ml-2 text-text-dim">含费成本 ¥{avg.toFixed(4)}/张</span>
          </div>
        )
      })()}

      <div className="w-full text-[10px] text-text-muted pb-1">
        {isQuote
          ? (isNetPrice
            ? '交易所债净价报价: 全价 = 净价 + 应计利息, 成本按全价落库(真实每张支付) · 记入持仓账本(holdings/position_actions), 与理财同属固定收益分组'
            : '可转债按全价直接报价, 成本 = 买入价 × 张数 + 税费 · 记入持仓账本(holdings/position_actions), 与理财同属固定收益分组')
          : '固定收益按「本金 + 年化 + 期限」记账: 估值 = 本金 × (1 + 年化 × 持有天数/365) 单利计提; App 显示余额可直接填手动市值覆盖估算 · 估值类型 yield → 计入固定收益账本(fix_income_assets)'}
        {matched && ` · 小类 ${matched.sub_assets_type} (${matched.valuation_type || '未配'})`}
      </div>
    </div>
  )
}
