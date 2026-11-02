import { useEffect, useRef, useState } from 'react'
import { brokersOfType, estimateFee, isOnchainEtf, isOnchainFundKind } from '../../../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import Tooltip from '../../Tooltip'
import { BROKER_COMMISSION_MIN, BROKER_COMMISSION_RATE, KEY_TO_ASSET_TYPE } from '../constants'

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
// ============================================================
export function AddFundForm({ typeKey, onDone, onCancel, brokers = [] }) {
  const assetType = KEY_TO_ASSET_TYPE[typeKey] || 'FUND'
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
  const [tradeTime, setTradeTime] = useState('')     // 成交时刻 HH:MM (场内基金, 可选)
  const [feeRatePct, setFeeRatePct] = useState('')   // FUND 申购费率 %, C 类填 0
  const [hint, setHint] = useState('')
  // 场内基金的买入日期 / 场外的申购日期, 种子流水 start_date 用它
  const [tradeDate, setTradeDate] = useState(new Date().toISOString().slice(0, 10))
  // FUND 细分类型: etf(场内, 股票式市价记账) / open(场外开放式, NAV T+1) / reits(场内, tickflow行情)。
  // 默认按代码段位猜, 可下拉手改。
  const [fundKind, setFundKind] = useState('open')
  const [fundKindTouched, setFundKindTouched] = useState(false)

  useEffect(() => {
    if (fundKindTouched) return
    setFundKind(isOnchainEtf(code) ? 'etf' : 'open')
  }, [code, fundKindTouched])

  const isInlineFund = isOnchainFundKind(fundKind)   // 场内基金(ETF/LOF/REITs)按股票口径
  const isOtcFund = fundKind === 'open'              // 场外开放式(申购口径)

  // 申购口径自动计算(外扣法): 净申购金额/手续费 由 申购金额+费率; 确认份额 还需申购净值
  const amt = parseFloat(cost) || 0
  const ratePct = feeRatePct === '' || isNaN(parseFloat(feeRatePct)) ? null : parseFloat(feeRatePct)
  const netAmount = (isOtcFund && amt > 0 && ratePct != null && ratePct >= 0) ? amt / (1 + ratePct / 100) : null
  const fundFee = netAmount != null ? amt - netAmount : null
  const navPx = parseFloat(unitPrice) || 0
  const confShares = (netAmount != null && navPx > 0) ? netAmount / navPx : null

  // 场内基金按股买: 自动估算手续费 (券商费率; 场外的手续费走申购公式, 不用费率估算)
  useEffect(() => {
    if (!isInlineFund || feeTouched) return
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
        const { name } = await fetchJSON(`/api/market/symbol-name?code=${encodeURIComponent(c)}`)
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
    if (!code || !name) return alert('代码/名称必填')

    // 场外基金代码存档带 .OF 后缀(019144 → 019144.OF); 场内原样
    const storedCode = isOtcFund && /^\d{6}$/.test(code.trim()) ? `${code.trim()}.OF` : code.trim()
    await fetchJSON('/api/assets', {
      method: 'POST',
      body: JSON.stringify({
        asset_type: assetType, code: storedCode, name: name.trim(), platform: platform.trim(),
        cost_amount: parseFloat(effCost),
        // 净值没填 → 份额待确认(pending); 场内按手填份额
        shares: isOtcFund
          ? (confShares ? parseFloat(confShares.toFixed(4)) : null)
          : shares ? parseFloat(shares) : null,
        // 场内买入日/场外申购日当种子流水 start_date (口径同股票)
        start_date: tradeDate || null,
        trade_time: isInlineFund && tradeTime ? tradeTime : null,
        purchase_fee_rate: isOtcFund && ratePct != null ? ratePct / 100 : null,
        broker: selectedBroker || null,
        // 手续费单独透传 (cost 已含它), 后端单存到初始流水的 fee 字段, 避免在流水里"消失"
        // 场外: 申购手续费 = 申购金额 − 净申购金额; 场内: 按券商费率估算/手填
        fee: isOtcFund
          ? (fundFee != null && fundFee > 0.005 ? parseFloat(fundFee.toFixed(2)) : null)
          : isInlineFund && parseFloat(fee) > 0 ? parseFloat(fee) : null,
        fund_kind: fundKind,
      }),
    })
    onDone?.()
  }

  const inp = 'bg-bg border border-border rounded px-1.5 py-1.5 text-[12.5px] text-text outline-none focus:border-accent'
  const autoInp = `${inp} font-mono text-text-dim bg-surface-3/40`   // 自动计算灰显只读

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border space-y-2.5 text-[13px]">
      {/* 单行自适应: 各控件 flex-1 均分一行宽度(宽屏自动拉伸/窄屏收缩到 min-w 后折行兜底) */}
      <div className="flex flex-wrap gap-1.5 items-end">
        <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">基金类型</label>
          <select value={fundKind}
            onChange={e => { setFundKind(e.target.value); setFundKindTouched(true) }}
            className={`${inp} w-full`}>
            <option value="etf">场内基金</option>
            <option value="open">场外基金</option>
            <option value="reits">Reits</option>
          </select>
        </div>
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
            {isInlineFund ? '买入单价 ¥' : '申购净值 ¥'}
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
              <label className="text-[12px] text-text-dim whitespace-nowrap">申购金额 ¥</label>
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
              累计投入 ¥
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
            <label className="text-[12px] text-text-dim whitespace-nowrap">成交时刻 <span className="text-text-muted text-[10px]">可空</span></label>
            <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
              className={`${inp} w-full font-mono text-text-dim`} title="今日建仓的成交时刻(可选), 留空用录入时间, 供分时图打点" />
          </div>
        )}
        {isInlineFund && (
          <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
            <label className="text-[12px] text-text-dim whitespace-nowrap">券商</label>
            <select className={`${inp} w-full font-mono`} value={selectedBroker} onChange={e => setSelectedBroker(e.target.value)}>
              <option value="">默认</option>
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
          {fundKind === 'etf'
            ? '场内基金按市价成交, 口径同股票: 成本 = 份额 × 单价 + 手续费; 行情走 ths 每日价格(ths fund/market/snapshot), 实时估值'
            : fundKind === 'reits'
              ? 'REITs 场内成交, 口径同股票; 行情走 TickFlow 实时行情(需在设置页或 TICKFLOW_API_KEY 配置 key)'
              : '场外开放式基金按申购口径: 净申购金额=申购金额÷(1+费率), 手续费=申购金额−净申购金额, 确认份额=净申购金额÷T日净值; 净值走 ths(ths fund/performance/nav); 净值没填先记待确认; 代码存档带 .OF 后缀'}
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
