import { useState } from 'react'
import { fmtMoney, fmtPrice, fundKindOf, isOnchainFundKind, priceColor } from '../../helpers'
import Tooltip from '../Tooltip'
import { TYPE_COLOR, TYPE_META, TYPE_ORDER } from './constants'
import { currencySymbol, exchangeOf, fxSourceLabel } from './helpers'

// 国旗徽标(logostream /country/{ISO 3166-1 alpha-2}, 1x1 方形): 市场徽标统一改国旗。
// 加载失败(无效码/未配 LOGOSTREAM key/服务不可达) → 退回原文字徽标, 不留空位。
export function FlagChip({ cc, label, color }) {
  const [failed, setFailed] = useState(false)
  const [prevCc, setPrevCc] = useState(cc)
  if (prevCc !== cc) {
    setPrevCc(cc)
    setFailed(false)
  }
  const textChip = (
    <span className="inline-flex items-center rounded font-semibold shrink-0 px-1 py-[1px] text-[0.75rem]"
      style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}>
      {label}
    </span>
  )
  if (failed || !cc) return textChip
  return (
    <Tooltip content={label}>
      <img src={`/api/market/flag?cc=${encodeURIComponent(cc)}`} alt={label} loading="lazy" decoding="async"
        onError={() => setFailed(true)}
        className="shrink-0 object-contain rounded-[3px] border border-border-subtle"
        style={{ width: 20, height: 20 }} />
    </Tooltip>
  )
}

export function MarketChip({ market, code }) {
  // 市场徽标 → 国旗: 港/美/日直接按市场; A 股/场外基金(.OF)统一中国旗(沪/深/北悬停可见)。
  // 老裸码按代码段位兜底: 920/4/8 开头→北, 5/6/9 开头→沪, 其余→深 —— 与 init_db 补后缀一致。
  // 加密/理财/现金等无市场归属的码不渲染。
  const m = String(market || '').toUpperCase()
  const c = String(code || '').toUpperCase()
  let label = '', color = '', cc = ''
  if (m === 'HK') { label = '港'; color = '#5fa86c'; cc = 'HK' }
  else if (m === 'US') { label = '美'; color = '#85a0b4'; cc = 'US' }
  else if (m === 'JP') { label = '日'; color = '#b47f8d'; cc = 'JP' }
  else if (m === 'CN') { label = '固收'; color = '#9b8bb4'; cc = 'CN' }
  else if (m === 'A' || m === '') {
    const body = c.includes('.') ? c.split('.')[0] : c
    const suf = c.includes('.') && /^[A-Z]{2,4}$/.test(c.split('.').pop()) ? c.split('.').pop() : ''
    const ex = (suf === 'SH' || suf === 'SZ' || suf === 'BJ') ? suf
      : /^\d{6}$/.test(body) ? (/^(920|[48])/.test(body) ? 'BJ' : /^[569]/.test(body) ? 'SH' : 'SZ') : ''
    if (m === 'A' || ex) {
      label = ex === 'SH' ? '沪' : ex === 'SZ' ? '深' : ex === 'BJ' ? '北' : 'A'
      color = ex === 'SH' ? '#c87f6c' : ex === 'SZ' ? '#5f9fb4' : ex === 'BJ' ? '#9b8bb4' : '#c87f6c'
      cc = 'CN'
    }
  }
  if (!cc) return null
  return <FlagChip cc={cc} label={label} color={color} />
}

export function ExchangeChip({ code }) {
  // 期货/期权交易所徽标 → 国旗: 境内六大所全是中国旗, 外盘按交易所所属国家;
  // 港/美/日期权期货用市场后缀(.HK/.US/.JP)直接出旗(含连字符合约码 IO2701-C-4000.CFE)。
  // 悬停保留交易所名(上期/大商/…); 认不出的外盘后缀退回原文字徽标。
  const c = String(code).toUpperCase()
  const m = c.match(/^[A-Z0-9-]{1,20}\.([A-Z]{2,5})$/)
  const suf = m ? m[1] : ''
  if (!suf || suf === 'SH' || suf === 'SZ' || suf === 'BJ') return null   // A股交给 MarketChip
  const ex = exchangeOf(code) || suf                                      // 境内短名(上期/大商), 外盘原样
  const FUT_CC = { SHF: 'CN', DCE: 'CN', CZC: 'CN', CFE: 'CN', INE: 'CN', GFE: 'CN',
                   CME: 'US', COMEX: 'US', NYMEX: 'US', CBOT: 'US',
                   LME: 'GB', SGX: 'SG', HKEX: 'HK',
                   HK: 'HK', US: 'US', JP: 'JP' }
  const cc = FUT_CC[suf] || ''
  const MKT_LABEL = { HK: '港', US: '美', JP: '日' }
  const label = ex === suf && MKT_LABEL[suf] ? MKT_LABEL[suf] : ex
  const color = '#7e93a8'
  if (!cc) {
    return (
      <span className="inline-flex items-center rounded font-semibold shrink-0 px-1 py-[1px] text-[0.75rem]"
        style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}>
        {label}
      </span>
    )
  }
  return <FlagChip cc={cc} label={label} color={color} />
}

// 券商徽标: logo 来自 icons/brokers/{券商名}.png(手动放图, 无上游), 文件缺失退回纯文字。
export function BrokerChip({ name }) {
  const [failed, setFailed] = useState(false)
  const [prevName, setPrevName] = useState(name)
  if (prevName !== name) {
    setPrevName(name)
    setFailed(false)
  }
  if (!name) return null
  if (failed) {
    return (
      <span className="text-[0.7143rem] px-1 py-[1px] rounded bg-info/15 text-info border border-info/40 shrink-0 whitespace-nowrap font-medium">
        {name}
      </span>
    )
  }
  return (
    <Tooltip content={name}>
      <img src={`/api/brokers/logo?name=${encodeURIComponent(name)}`} alt={name} loading="lazy" decoding="async"
        onError={() => setFailed(true)}
        className="w-[20px] h-[20px] object-contain rounded-[3px] border border-border-subtle shrink-0" />
    </Tooltip>
  )
}

export function FxHint({ extra, children }) {
  if (!extra?.currency || extra.currency === 'CNY') return children
  return (
    <Tooltip content={
      <div className="leading-relaxed">
        <div className="text-text-bright font-semibold mb-1">人民币折算口径</div>
        <div>{extra.currency}/CNY {Number(extra.fxRate || 1).toFixed(4)}</div>
        <div className="text-text-dim text-[0.8214rem] mt-1">
          {fxSourceLabel(extra.fxSource)} · 5分钟缓存
          {extra.fxTime ? ` · ${extra.fxTime}` : ''}
        </div>
      </div>
    }>
      {children}
    </Tooltip>
  )
}

// ============================================================
// Small row primitives
// ============================================================
export function TypeChip({ type, compact = false }) {
  const color = TYPE_COLOR[type]
  return (
    <span className="inline-flex items-center rounded font-semibold shrink-0"
      style={{
        padding: compact ? '2px 6px' : '2px 7px',
        border: `1px solid ${color}50`,
        background: `${color}18`,
        color,
        fontSize: compact ? 11 : 10.5,
        letterSpacing: '.02em',
        lineHeight: 1.2,
      }}>
      {compact ? TYPE_META[type].short : TYPE_META[type].label}
    </span>
  )
}

export function WeightBar({ weight, color, width = 48 }) {
  const pct = Math.min(1, weight || 0) * 100
  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="font-mono text-[0.8571rem] text-text tabular-nums min-w-[36px] text-right">
        {(weight * 100).toFixed(1)}%
      </span>
      <div className="h-1 rounded-sm overflow-hidden" style={{ width, background: 'var(--color-surface-3)' }}>
        <div className="h-full rounded-sm" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  )
}

// stale: 港美股今日没开盘(today_valid===false), 这个涨跌是上一交易日的 —— 置灰并标注,
// 与总计里不计入它的口径一致。
// 布局与浮动盈亏列同构: 上层今日盈亏金额, 下层涨跌比例, 不再画区间小图。
export function TodayPulse({ change, mv, stale = false }) {
  if (change == null) return <span className="text-text-muted text-[0.8571rem]">--</span>
  const color = change > 0 ? 'var(--color-bear-bright)' : change < 0 ? 'var(--color-bull-bright)' : 'var(--color-text-dim)'
  const pct = change === 0 ? '0.00%' : (change > 0 ? '+' : '') + change.toFixed(2) + '%'
  // 今日盈亏金额 = 今日市值 - 昨收市值 = mv × r/(1+r) (与 aggregate 的今日盈亏合计同式)
  const amt = mv != null ? mv * change / 100 / (1 + change / 100) : null
  const twoLines = (
    <>
      <span className="font-mono text-[0.9643rem] font-semibold tabular-nums text-right" style={{ color }}>
        {amt != null ? (amt >= 0 ? '+' : '') + fmtMoney(amt) : '--'}
      </span>
      <span className="font-mono text-[0.7857rem] opacity-85 text-right" style={{ color }}>{pct}</span>
    </>
  )
  if (stale) {
    return (
      <Tooltip content="该市场今天还没开盘(或休市), 这是上一交易日的涨跌, 不计入今日盈亏">
        <div className="inline-flex flex-col items-end cursor-help opacity-45">
          {twoLines}
          <span className="text-[0.7143rem] text-text-muted leading-none mt-0.5">上一交易日</span>
        </div>
      </Tooltip>
    )
  }
  return <div className="flex flex-col items-end">{twoLines}</div>
}

// 基金代理标的脉搏：用底层市场（沪金/纳指期货/恒生等）实时涨跌预判基金当日走势.
// 布局与浮动盈亏列同构: 上层金额(带"代"角标), 下层比例, 不再画区间小图。
export function TypeMiniInfo({ row }) {
  const { type, extra } = row
  if (type === 'T' || type === 'O') {
    // 期货/期权: 手数×乘数 · 开仓均价(与报价同单位)
    return (
      <span className="text-[0.8214rem] text-text-muted font-mono">
        {extra.shares} 手 × {extra.multiplier} · 开仓 {fmtPrice(extra.avgCost, row.code)}
        {extra.price != null && <> · 现价 <span className="text-text">{fmtPrice(extra.price, row.code)}</span></>}
      </span>
    )
  }
  if (type === 'A') {
    return (
      <span className="text-[0.8214rem] text-text-muted font-mono">
        {extra.shares} 股 · {currencySymbol(extra.currency)}{fmtPrice(extra.avgCost, row.code)}
      </span>
    )
  }
  if (type === 'F') {
    // 场内 ETF/LOF/REITs: 按股票口径展示 (持有份额 · 成本价), 它本就是市价实时成交, 不是净值 T+1.
    if (isOnchainFundKind(fundKindOf(row._raw || {}))) {
      const sh = Number(row._raw?.shares) || 0
      const ca = Number(row._raw?.cost_amount) || 0
      const avg = sh > 0 ? ca / sh : 0
      return (
        <span className="text-[0.8214rem] text-text-muted font-mono">
          {sh} 份 · ¥{fmtPrice(avg)}
        </span>
      )
    }
    // 场外公募基金: 净值 T+1 口径.
    return (
      <span className="text-[0.8214rem] text-text-dim">
        {extra.platform || '基金'}
        {extra.nav != null && <> · 净值 <span className="font-mono text-text">{Number(extra.nav).toFixed(4)}</span>
          {!extra.realtime && <span className="text-text-muted ml-1">T+1</span>}
        </>}
      </span>
    )
  }
  if (type === 'W') {
    // 固收已并入持仓账本: 行是 holdings 行(src=holding), 按 W 桶口径展示。
    //   债券(quote): 份额(张) · 成本/张(全价口径) · 票面利率 · 到期日(剩N天)
    //   理财(nav):   份额 × 最新净值(净值在持仓「改」里更新), 平台/产品属性看流水
    const sh = Number(row._raw?.shares) || 0
    const ca = Number(row._raw?.cost_price) || 0
    const rate = Number(row._raw?.coupon_rate)
    const md = row._raw?.maturity_date
    const daysLeft = md ? Math.max(0, Math.ceil((new Date(`${String(md).slice(0, 10)}T00:00:00`) - new Date()) / 86400000)) : null
    const nav = row.extra?.price   // nav 型理财: 最新净值(EditModal 改最新价)
    const navRow = /wealth/.test(String(row._raw?.sub_assets_type || ''))
    return (
      <span className="text-[0.8214rem] text-text-dim">
        {navRow && nav != null
          ? <>{sh.toFixed(4)} 份 · 净值 <span className="font-mono text-text">{Number(nav).toFixed(4)}</span></>
          : <>{sh} 张 · 成本 ¥{fmtPrice(ca)}/张<span className="text-text-muted">(全价)</span></>}
        {Number.isFinite(rate) && rate > 0 && (
          <> · 票面 <span className="font-mono text-bull">{(rate * 100).toFixed(2)}%</span></>
        )}
        {daysLeft != null && (
          <Tooltip content={`到期日 ${String(md).slice(0, 10)} — 到期兑付在该行「流水」里记一笔原价卖出`}>
            <span className="cursor-help">
              {' '}· <span className="font-mono">剩 {daysLeft}天</span>
              <span className="text-text-muted"> ({String(md).slice(5, 10)})</span>
            </span>
          </Tooltip>
        )}
      </span>
    )
  }
  if (type === 'C') {
    return (
      <span className="text-[0.8214rem] text-text-dim">
        {extra.platform || 'OKX'}
        {extra.price != null && <> · <span className="font-mono text-text">${extra.price}</span></>}
        {extra.amount != null && <> · <span className="font-mono">{extra.amount}</span></>}
      </span>
    )
  }
  if (type === 'R') {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.8214rem] text-text-dim">
        {extra.platform || '量化'}
      </span>
    )
  }
  return null
}

// ============================================================
// Hover action buttons
// ============================================================
export function RowActions({ row, visible, onEdit, onHistory, onRemove, onAddLot, onReduceLot, onShowActions, onCloseLot }) {
  // 桌面: hover 才显示 (opacity 控制); 移动: 始终显示, 1 字按钮
  // 统一四按钮口径(按期货样式): 证券类=开仓|买入 / 平仓|卖出 / 流水 / 删除
  const btnBase = 'rounded border border-border-med bg-surface-2 text-text-dim ' +
    'hover:border-accent hover:text-accent transition-colors cursor-pointer whitespace-nowrap'
  const actions = []
  if (row.type === 'M') {
    // 现金就是一个余额数字: 直接编辑, 免交易流水逻辑
    actions.push({ short: '改', label: '编辑', fn: () => onEdit?.(row) })
    actions.push({ short: '删', label: '删除', fn: () => onRemove?.(row), danger: true })
  } else if (row.type === 'A') {
    if (row.extra?.direction === 'SHORT') {
      // 做空的股票行(参考期货模式): 开仓(卖出开仓) / 平仓(买入平仓) — 预选 OPEN/CLOSE
      actions.push({ short: '开', label: '开仓', fn: () => onHistory?.(row._raw, 'OPEN') })
      actions.push({ short: '平', label: '平仓', fn: () => onHistory?.(row._raw, 'CLOSE') })
    } else {
      // A股: 买入/卖出 = 打开流水弹窗并预选 BUY/SELL
      actions.push({ short: '买', label: '买入', fn: () => onHistory?.(row._raw, 'BUY') })
      actions.push({ short: '卖', label: '卖出', fn: () => onHistory?.(row._raw, 'SELL') })
    }
    actions.push({ short: '史', label: '流水', fn: () => onHistory?.(row._raw) })
    actions.push({ short: '删', label: '删除', fn: () => onRemove?.(row), danger: true })
  } else if (row.type === 'T' || row.type === 'O') {
    // 期货/期权: 开仓(同向 OPEN) / 平仓(CLOSE) / 流水 / 删除
    actions.push({ short: '开', label: '开仓', fn: () => onAddLot?.(row) })
    actions.push({ short: '平', label: '平仓', fn: () => onCloseLot?.(row) })
    actions.push({ short: '史', label: '流水', fn: () => onHistory?.(row._raw) })
    actions.push({ short: '删', label: '删除', fn: () => onRemove?.(row), danger: true })
  } else {
    // 场外(基金/加密/理财等): 买入=加仓 / 卖出=减仓 / 流水 / 删除
    if (row.type === 'F' || row.type === 'C' || row.type === 'W') {
      actions.push({ short: '买', label: '买入', fn: () => onAddLot?.(row) })
      actions.push({ short: '卖', label: '卖出', fn: () => onReduceLot?.(row) })
      const pendingN = row._raw?.pending_actions_count || 0
      const histLabel = pendingN > 0 ? `流水 (${pendingN}!)` : '流水'
      actions.push({ short: pendingN > 0 ? `史${pendingN}` : '史', label: histLabel,
        fn: () => onShowActions?.(row), highlight: pendingN > 0 })
    }
    // 其余场外类型(如 R 机器人): 只留 编辑/删除
    if (actions.length === 0) actions.push({ short: '改', label: '编辑', fn: () => onEdit?.(row) })
    actions.push({ short: '删', label: '删除', fn: () => onRemove?.(row), danger: true })
  }
  const dangerHover = (a) => a.danger ? {
    onMouseEnter: e => { e.currentTarget.style.borderColor = 'var(--color-bear)'; e.currentTarget.style.color = 'var(--color-bear)' },
    onMouseLeave: e => { e.currentTarget.style.borderColor = ''; e.currentTarget.style.color = '' },
  } : {}
  return (
    <>
      {/* 移动端: 始终显示, 1 字按钮, 紧凑 */}
      <div className="flex md:hidden gap-0.5 justify-end items-center">
        {actions.map(a => (
          <button key={a.label} onClick={a.fn}
            className={`${btnBase} px-1.5 py-[2px] text-[0.8571rem] min-w-[20px] ${a.highlight ? 'border-warn/60 text-warn' : ''}`}
            {...dangerHover(a)}
          >{a.short}</button>
        ))}
      </div>
      {/* 桌面: hover 显示, 全名. 容器 pointer-events:none, 按钮自己 auto;
          按钮间空隙能让 hover 事件穿透到下层 TypeMiniInfo (反推 tooltip 等). */}
      <div className="hidden md:flex gap-1 justify-end items-center"
        style={{
          opacity: visible ? 1 : 0,
          transform: visible ? 'translateX(0)' : 'translateX(4px)',
          transition: 'opacity .18s, transform .18s',
          pointerEvents: 'none',
        }}>
        {actions.map(a => (
          <button key={a.label} onClick={a.fn}
            className={`${btnBase} px-2 py-[3px] text-[0.8214rem] ${a.highlight ? 'border-warn/60 text-warn' : ''}`}
            style={{ pointerEvents: visible ? 'auto' : 'none' }}
            {...dangerHover(a)}
          >{a.label}</button>
        ))}
      </div>
    </>
  )
}

// ============================================================
// Allocation donut
// ============================================================
export function AllocationDonut({ groups, totalMv }) {
  // 移动端用更小的环 + 紧凑 legend, 桌面用大环 (160/120: 比旧 140/100 大一号)
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768
  const size = isMobile ? 120 : 160
  const r = size / 2 - 14
  const cx = size / 2
  const circ = 2 * Math.PI * r
  const order = TYPE_ORDER.filter(t => groups[t])
  let offset = 0
  const arcs = order.map(type => {
    const frac = groups[type].weight
    const length = frac * circ
    const arc = { type, frac, length, offset, color: TYPE_COLOR[type] }
    offset += length
    return arc
  })
  // 饼图是"图": 文字用固定 px, 与 canvas 图表同口径 —— 不随显示设置的字体大小档缩放,
  // 否则换字体档会改变图内排版(数字样式保持稳定)。
  return (
    <div className="flex items-center gap-3 md:gap-5 w-full md:w-auto min-w-0">
      <svg width={size} height={size} className="shrink-0">
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--color-surface-3)" strokeWidth="12" />
        {arcs.map(a => (
          <circle key={a.type} cx={cx} cy={cx} r={r} fill="none"
            stroke={a.color} strokeWidth="12"
            strokeDasharray={`${a.length} ${circ}`}
            strokeDashoffset={-a.offset}
            transform={`rotate(-90 ${cx} ${cx})`}
            style={{ transition: 'stroke-dasharray 0.4s' }} />
        ))}
        <text x={cx} y={cx - 4} textAnchor="middle" className="text-[11px]"
          fill="var(--color-text-dim)">总资产</text>
        <text x={cx} y={cx + 13} textAnchor="middle" className="font-mono text-[13px] md:text-[15px] font-bold"
          fill="var(--color-text-bright)">
          ¥{fmtMoney(totalMv)}
        </text>
      </svg>
      <div className="flex flex-col gap-1 md:gap-1.5 flex-1 min-w-0">
        {order.map(type => {
          const g = groups[type]
          return (
            <div key={type} className="flex items-center gap-1.5 md:gap-2 text-[11.5px] md:text-[12px]">
              <div className="w-2 h-2 rounded-sm shrink-0" style={{ background: TYPE_COLOR[type] }} />
              <span className="text-text shrink-0">{TYPE_META[type].label}</span>
              <span className="font-mono text-text-bright tabular-nums shrink-0">
                {(g.weight * 100).toFixed(1)}%
              </span>
              {/* 金额和盈亏只在桌面显示 (移动端空间不够, 数据已在持仓表里)。
                  金额用资产贡献口径(g.asset, 期货=保证金占用), 与中心总资产/权重同口径 */}
              <span className="hidden md:inline font-mono text-text-dim text-[11px] ml-auto truncate">
                ¥{fmtMoney(g.asset)}
              </span>
              <span className={`hidden md:inline font-mono text-[11px] text-right shrink-0 ${priceColor(g.pnl)}`}>
                {g.pnl >= 0 ? '+' : ''}{fmtMoney(g.pnl)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
