import { useEffect, useRef, useState } from 'react'
import { currencySymbol } from '../helpers'
import { fetchJSON } from '../../../hooks/useApi'
import Tooltip from '../../Tooltip'
import { AssetClassPicker, useAssetClass } from './AssetClassPicker'

// ============================================================
// AddFixIncomeForm — 固定收益添加表单 (理财 / 债券), 仅处理 W 桶。
//
// 数据源: assets_classify 大类 fix-income (bank_wealth 理财 / bond 债券),
// 中类×币种 → 唯一小类自动匹配, 全部目录驱动, 不硬编码资产种类。
// 账本: fix_income_assets / fix_income_asset_actions (POST /api/fix-income),
// 估值类型 yield —— 本金 + 年化 + 期限模型, 无份额无市价。
//
// 字段设计(fix_income_assets 表驱动):
//   本金(首笔 BUY 流水) · 年化收益率/票面利率 · 起息日(计息起点) ·
//   期限(天) ↔ 到期日(二填一互相推算) · 付息方式 · 风险等级 · 平台。
// 预估到期本息 = 本金 × (1 + 年化 × 期限/365) (单利)。
// ============================================================

const INTEREST_TYPES = [
  { v: 'bullet', label: '到期一次付息' },
  { v: 'monthly', label: '按月付息' },
  { v: 'quarterly', label: '按季付息' },
  { v: 'semiannual', label: '半年付息' },
  { v: 'annual', label: '每年付息' },
]

export function AddFixIncomeForm({ sub, onDone, onCancel }) {
  // 资产分类(PG assets_classify): 大类固定 fix-income, 中类下拉自动同步(理财/国债/企业债…)。
  // requireValuation: 'yield' —— 同大类下 quote 型小类(交易所债/可转债)按市价交易属持仓账本,
  // 不进本金+年化表单, 数据驱动过滤不写死。币种选项只给当前中类下有的(hook 里吸附)。
  const { rows, err, mainOptions, currencyOptions,
    main: hookMain, currency: hookCurrency, matched: hookMatched,
    setMain, setCurrency } =
    useAssetClass('fix-income', { preferMain: 'wealth', requireValuation: 'yield' })
  // 录入入口已选具体小类(sub)时: 按该小类锁定中类/币种, 不再二次选择(全部来自 assets_classify)。
  // 小类不在 yield 集合里(如 quote 型交易所债/可转债) → rows 里找不到, 下方给提示不硬锁。
  const subRow = sub ? rows.find(r => r.sub_assets_type === sub) : null
  const subNotYield = !!sub && !subRow
  const main = subRow ? subRow.main_assets_type : hookMain
  const currency = subRow ? subRow.currency : hookCurrency
  const matched = subRow || hookMatched
  const curSymbol = currencySymbol(currency || 'CNY')
  const isBond = main === 'bond'   // 债券中类: 年化字段叫「票面利率」, 名称叫「债券名称」

  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('')
  const [principal, setPrincipal] = useState('')
  const [yieldPct, setYieldPct] = useState('')       // 年化收益率/票面利率 %
  const [termDays, setTermDays] = useState('')       // 期限(天); 可空 = 开放式
  const [valueDate, setValueDate] = useState(new Date().toISOString().slice(0, 10))
  const [maturityDate, setMaturityDate] = useState('')  // 到期日; 可空
  const [interestType, setInterestType] = useState('bullet')
  const [riskLevel, setRiskLevel] = useState('')
  const [note, setNote] = useState('')
  // 期限 ↔ 到期日互推: 手填过哪头就不再动哪头(程序回填不碰 touched 标记)
  const termTouched = useRef(false)
  const maturityTouched = useRef(false)

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

  // ── 实时预览: 单利口径, 与后端 _accrued_estimate / expected_maturity_value 同式 ──
  const p = parseFloat(principal) || 0
  const r = parseFloat(yieldPct) / 100
  const t = parseInt(termDays) || 0
  const hasRate = Number.isFinite(r) && r > 0
  const expectedMaturity = (p > 0 && hasRate && t > 0) ? p * (1 + r * t / 365) : null
  const dailyInterest = (p > 0 && hasRate) ? p * r / 365 : null

  const submit = async () => {
    if (!name.trim()) return alert('请输入产品名称')
    if (!(p > 0)) return alert('请输入买入本金')
    if (!hasRate) return alert('请输入年化收益率(理财=业绩比较基准 / 债券=票面利率)')
    if (!matched) return alert('请选择资产类型与币种(自动匹配小类)')
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

  const inp = 'bg-bg border border-border rounded px-1.5 py-1.5 text-[12.5px] text-text outline-none focus:border-accent'
  const autoInp = `${inp} font-mono text-text-dim bg-surface-3/40`   // 自动计算灰显只读

  return (
    <div className="px-6 py-3 bg-surface-2/50 border-b border-border space-y-2.5 text-[13px]">
      {/* 单行自适应: 各控件 flex-1 均分一行宽度(宽屏自动拉伸/窄屏收缩到 min-w 后折行兜底) */}
      <div className="flex flex-wrap gap-1.5 items-end">
        {/* 资产类型(中类: 理财/债券) / 币种 / 市场自动匹配: 选项全部来自 PG assets_classify
            fix-income 大类, 中类加行即生效, 前端零改动。已锁小类时显示静态徽标。 */}
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
          <label className="text-[12px] text-text-dim whitespace-nowrap">{isBond ? '债券代码' : '产品代码'}</label>
          <input value={code} onChange={e => setCode(e.target.value)}
            className={`${inp} w-full font-mono`} placeholder={isBond ? '019547.SH' : '可空'} />
        </div>
        <div className="flex flex-col gap-1 flex-[1.3] min-w-[96px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">{isBond ? '债券名称' : '产品名称'} *</label>
          <input value={name} onChange={e => setName(e.target.value)} className={`${inp} w-full`}
            placeholder={isBond ? '24国债09' : '招银季季宝'} />
        </div>

        {/* 本金 → 年化 → 期限: 固定收益三要素 */}
        <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">买入本金 {curSymbol} *</label>
          <input type="number" step="0.01" value={principal}
            onChange={e => setPrincipal(e.target.value)}
            className={`${inp} w-full font-mono`} placeholder="50000" />
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">
            {isBond ? '票面利率 %' : '年化收益率 %'} *
            <Tooltip content={
              <div className="leading-relaxed">
                <div className="text-text-bright font-semibold mb-0.5">{isBond ? '票面利率' : '年化收益率'}</div>
                <div className="text-text-dim text-[10.5px]">
                  {isBond
                    ? '债券票面利率(单利, 按持有天数计提应计利息)'
                    : '理财填业绩比较基准/预期年化(单利计提估值); 到期兑付按它估算最后一笔利息'}
                </div>
              </div>
            }>
              <span className="ml-0.5 cursor-help text-text-muted">ⓘ</span>
            </Tooltip>
          </label>
          <input type="number" step="0.001" value={yieldPct} onChange={e => setYieldPct(e.target.value)}
            className={`${inp} w-full font-mono`} placeholder="2.65" />
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[68px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">期限 (天)</label>
          <input type="number" step="1" value={termDays}
            onChange={e => { termTouched.current = true; setTermDays(e.target.value) }}
            className={`${inp} w-full font-mono`} placeholder="93"
            title="封闭期/存续期天数; 填了自动推算到期日, 开放式可空" />
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[104px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">起息日</label>
          <input type="date" value={valueDate} onChange={e => setValueDate(e.target.value)}
            className={`${inp} w-full font-mono`} />
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[104px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">到期日</label>
          <input type="date" value={maturityDate}
            onChange={e => { maturityTouched.current = true; setMaturityDate(e.target.value) }}
            className={`${inp} w-full font-mono`}
            title="填了自动反算期限(天); 开放式理财可空" />
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[110px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">付息方式</label>
          <select value={interestType} onChange={e => setInterestType(e.target.value)}
            className={`${inp} w-full`}>
            {INTEREST_TYPES.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1 flex-[0.7] min-w-[64px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">风险等级</label>
          <select value={riskLevel} onChange={e => setRiskLevel(e.target.value)}
            className={`${inp} w-full`} title="可空">
            <option value="">无</option>
            {['R1', 'R2', 'R3', 'R4', 'R5'].map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1 flex-1 min-w-[76px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">平台/发行机构</label>
          <input value={platform} onChange={e => setPlatform(e.target.value)} className={`${inp} w-full`}
            placeholder="招商银行 / 支付宝 / 交易所" />
        </div>

        <button onClick={submit} className="shrink-0 px-3 py-1.5 rounded-md bg-accent text-bg font-semibold text-[13px] hover:opacity-90 cursor-pointer">
          保存
        </button>
        <button onClick={onCancel} className="shrink-0 px-2.5 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text cursor-pointer">
          取消
        </button>
      </div>

      {/* 备注 */}
      <div className="flex flex-wrap gap-1.5 items-end">
        <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
          <label className="text-[12px] text-text-dim whitespace-nowrap">备注</label>
          <input value={note} onChange={e => setNote(e.target.value)} className={`${inp} w-full`}
            placeholder="锁定期 / 申赎规则 / 其他说明 (可空)" />
        </div>
      </div>

      {/* 锁定小类不是 yield 型(quote 型债券等): 本表单不适用, 给出明确指引 */}
      {subNotYield && (
        <div className="text-[10.5px] font-mono text-warn">
          「{sub}」不是固定收益(yield)估值, 本表单不适用 —— 市价交易型债券按持仓账本口径, 录入入口待单独开发。
        </div>
      )}

      {/* 单利计提预览 (与后端 _accrued_estimate 同式): 到期本息 + 日利息 */}
      {(expectedMaturity != null || dailyInterest != null) && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[10.5px] font-mono text-bull">
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

      <div className="text-[10.5px] text-text-muted">
        固定收益按「本金 + 年化 + 期限」记账: 估值 = 本金 × (1 + 年化 × 持有天数/365) 单利计提;
        App 显示余额可直接填手动市值覆盖估算 · 估值类型 yield → 计入固定收益账本(fix_income_assets)
        {matched && ` · 估值类型 ${matched.valuation_type || '(未配)'} · 小类 ${matched.sub_assets_type}`}
      </div>
    </div>
  )
}
