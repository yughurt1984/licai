import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { fmtMoney, fmtPct, loadBrokers, priceColor } from '../helpers'
import { api, fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import Tooltip from './Tooltip'
import { AllocationDonut, FxHint, MarketChip, ProxyPulse, RowActions, TodayPulse, TypeChip, TypeMiniInfo, WeightBar } from './portfolio/atoms'
import { TYPE_COLOR, TYPE_ORDER, TYPE_META, typeFromClassify } from './portfolio/constants'
import { formatCurrencyMoney } from './portfolio/helpers'
import { aggregate, normalizeAsset, normalizeFixIncomeAsset, normalizeHolding } from './portfolio/model'
import { HoldingRow } from './portfolio/HoldingRow'
import { PortfolioModals } from './portfolio/PortfolioModals'
import { ClosedPositionsBlock, SummaryStrip } from './portfolio/summary'
import { usePortfolioModals } from './portfolio/useModals'
import { useAllAssetClasses } from './portfolio/forms/AssetClassPicker'

// ============================================================
// UnifiedPortfolio — replaces Portfolio + ExternalAssets
// Groups: A股 / 基金 / 加密 / 机器人. Sorted by market value desc
// within groups. Hover-only row actions. Donut allocation header.
// ============================================================

// ============================================================
// Main
// ============================================================
export default function UnifiedPortfolio({ holdings, onEdit, onHistory, onAdd, dataVersion = 0, onHoldingsChanged }) {
  const [assets, setAssets] = useState([])
  const [brokers, setBrokers] = useState([])
  useEffect(() => { loadBrokers().then(setBrokers) }, [])
  // 个人中心改了券商费率 → 立即重拉(顺带清 helpers 的模块缓存)
  useEffect(() => {
    const onChange = () => { loadBrokers().then(setBrokers).catch(() => {}) }
    window.addEventListener('licai:brokers-changed', onChange)
    return () => window.removeEventListener('licai:brokers-changed', onChange)
  }, [])
  const [assetsLoaded, setAssetsLoaded] = useState(false)
  const [tradingDay, setTradingDay] = useState(null)
  const [collapsed, setCollapsed] = useState({})
  const [hoverId, setHoverId] = useState(null)
  const [filter, setFilter] = useState('ALL')
  const [sortKey, setSortKey] = useState('mv')
  // 顶部大类标签与 assets_classify 统一: 期货/期权同属 derivatives → 合并为一个「衍生品」tab;
  // 其余大类(股票/基金/加密/理财/现金…)直接取 DB 大类名。typeFromClassify 映射不到的小类桶(TYPE_ORDER)兜底保留。
  const { classOptions, rows: classRows } = useAllAssetClasses()
  const classBuckets = useMemo(() => {
    const m = {}
    for (const r of (classRows || [])) {
      const b = typeFromClassify(r)
      if (b) (m[r.assets_class] = m[r.assets_class] || new Set()).add(b)
    }
    return m
  }, [classRows])
  const tabDefs = useMemo(() => {
    const defs = []
    const seen = new Set()
    for (const [k, l] of classOptions) {
      const buckets = classBuckets[k]
      if (buckets && buckets.size) {
        const arr = [...buckets]
        defs.push({ key: k, label: l, buckets, color: TYPE_COLOR[arr[0]] || '#8a8378' })
        arr.forEach(b => seen.add(b))
      }
    }
    // 兜底: DB 里没有对应大类的展示桶(如机器人), 仍按原桶保留 tab
    for (const t of TYPE_ORDER) {
      if (!seen.has(t) && TYPE_META[t]) {
        defs.push({ key: t, label: TYPE_META[t].label, buckets: new Set([t]), color: TYPE_COLOR[t] || '#8a8378' })
      }
    }
    return defs
  }, [classOptions, classBuckets])
  const activeTab = filter === 'ALL' ? null : tabDefs.find(d => d.key === filter)
  // 九个弹窗/内联表单的开关(原来是九个同构 useState) —— 见 usePortfolioModals
  const { open, show, hide } = usePortfolioModals()
  const [thesisCodes, setThesisCodes] = useState(() => new Set())
  const loadThesisCodes = useCallback(async () => {
    try {
      const list = await fetchJSON('/api/portfolio/thesis')
      setThesisCodes(new Set((list || []).map(t => t.code)))
    } catch {}
  }, [])
  useEffect(() => { loadThesisCodes() }, [loadThesisCodes])
  const [realized, setRealized] = useState({ stock: 0, asset: 0 })
  const [todayPnl, setTodayPnl] = useState(null)     // 券商口径当日盈亏(后端算)
  const loadAssets = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/assets')
      setAssets(d.assets || [])
    } catch {} finally { setAssetsLoaded(true) }
  }, [])

  // 固定收益(fix-income: 理财/债券)走独立账本 /api/fix-income, 不在 /api/assets 里。
  const [fixAssets, setFixAssets] = useState([])
  const loadFixAssets = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/fix-income')
      setFixAssets(d.assets || [])
    } catch {}
  }, [])

  const loadRealized = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([
        fetchJSON('/api/portfolio/realized'),
        fetchJSON('/api/assets/realized'),
      ])
      // 总盈亏 = 浮动(行内 pnl) + 已实现. 但有两处会重复算, 必须从已实现里剔掉:
      //  1) 股票: 当前持仓段的 realized 已被「综合成本法」摊进剩余成本 → 行内浮动已含。
      //     只补 realized_carry (已平仓段 + 分红, 后端算好), 含清仓后又复活的旧那轮。
      //  2) CASH 的 realized(利息) 已作为现金行的 pnl 计入浮动 → 资产已实现里排除 CASH。
      // FUND/CRYPTO/WEALTH 的成本只算剩余 lot, 浮动不含 realized, 所以全额计入。
      const stockCarry = s.total_realized_carry != null
        ? s.total_realized_carry
        : (s.items || []).filter(it => !it.still_holding).reduce((sum, it) => sum + (it.realized_pnl || 0), 0)
      const assetExclCash = (a.items || [])
        .filter(it => it.asset_type !== 'CASH')
        .reduce((sum, it) => sum + (it.closed_realized ?? it.realized_pnl ?? 0), 0)
      setRealized({
        stock: Math.round(stockCarry * 100) / 100,
        asset: Math.round(assetExclCash * 100) / 100,
        stockItems: s.items || [],
        assetItems: a.items || [],
      })
    } catch (e) { console.error('realized load failed', e) }
  }, [])

  const loadTodayPnl = useCallback(async () => {
    try {
      const d = await fetchJSON('/api/portfolio/today-pnl')
      setTodayPnl(d && typeof d.total === 'number' ? d : null)   // 出错时置 null → 退回前端口径
    } catch { setTodayPnl(null) }
  }, [])

  useEffect(() => {
    loadAssets()
    loadFixAssets()
    loadRealized()
    loadTodayPnl()
    // Crypto & OKX bots are 24/7 markets; use shorter interval. Server-side
    // caches handle upstream rate limits (crypto 30s, fund 120s).
    const t = setInterval(() => { loadAssets(); loadFixAssets(); loadRealized(); loadTodayPnl() }, 20000)
    return () => clearInterval(t)
  }, [loadAssets, loadFixAssets, loadRealized, loadTodayPnl])

  useEffect(() => {
    fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {})
    const t = setInterval(() => fetchJSON('/api/market/trading-day').then(setTradingDay).catch(() => {}), 3600000)
    return () => clearInterval(t)
  }, [])

  // 交易流水编辑后 (dataVersion 变化): 立即重载已实现/已清仓/资产, 不用等 20s 轮询
  useEffect(() => {
    if (dataVersion > 0) { loadRealized(); loadAssets(); loadFixAssets(); loadTodayPnl() }
  }, [dataVersion, loadRealized, loadAssets, loadFixAssets, loadTodayPnl])

  const rows = useMemo(() => {
    // 0 持仓的股票/基金 不在主列表显示, 走 "已清仓" 区块
    const a = (holdings || []).filter(h => (h.shares || 0) > 0).map(normalizeHolding)
    // BOT/CASH 常驻; 基金/加密 已清仓(无成本/无市值/无在途)的不进主列表, 走"已清仓"区块
    const e = (assets || []).filter(x => x.asset_type === 'BOT' || x.asset_type === 'CASH'
      || (x.shares || 0) > 0 || (x.cost_amount || 0) > 0
      || (x.current_value || 0) > 0 || (x.pending_amount || 0) > 0).map(normalizeAsset)
    // 固定收益(W 桶): 本金或市值 > 0 的在持行; 已到期/结清(principal=0 且 mv=0)不进主列表
    const fi = (fixAssets || []).filter(x => (x.principal || 0) > 0 || (x.current_value || 0) > 0)
      .map(normalizeFixIncomeAsset)
    return [...a, ...e, ...fi]
  }, [holdings, assets, fixAssets])

  const filtered = useMemo(
    () => (filter === 'ALL' || !activeTab) ? rows : rows.filter(r => activeTab.buckets.has(r.type)),
    [rows, filter, activeTab]
  )

  const aShareTradingDay = tradingDay
    ? !!tradingDay.is_trading_day
    : ![0, 6].includes(new Date().getDay())
  const agg = useMemo(() => aggregate(filtered, aShareTradingDay), [filtered, aShareTradingDay])
  // 个股「历史已实现」(清仓段+分红, 不在当前浮动里). 用来在行内补「真实总盈亏」,
  // 否则清仓后复活的票只看浮动会误判 (锡业股份浮动+245 实则全周期 -373)。
  const carryByCode = useMemo(() => {
    const m = {}
    for (const it of (realized?.stockItems || [])) {
      if (it.realized_carry) m[it.stock_code] = it.realized_carry
    }
    return m
  }, [realized])
  // 顶部 tab 由 tabDefs 驱动(数据来自 assets_classify); 内容分组仍按桶(TYPE_ORDER)渲染
  // visibleTypes: from filtered rows — drives content section rendering
  const visibleTypes = TYPE_ORDER.filter(t => agg.groups[t])

  // Risk insights: 板块集中度提示(仅 A 股行业, 数据来自东财一级行业; 基金行业分类已下线,
  // 基金桶的 category 现在是「基金类别」(场内ETF/场外基金…), 不再参与板块分散判断)。
  // Always computed from ALL rows (not filtered) so toggling filter doesn't change advice.
  // 大类集中度提示已按需求移除。
  const insights = useMemo(() => {
    const allAgg = aggregate(rows, true)
    const warnings = []
    const g = allAgg.groups.A
    // 股票桶里港股/美股的分类是「市场」不是行业板块, 不参与板块分散判断(只看 A 股行业)
    const items = (g?.items || []).filter(it => (it.extra?.market || 'A') === 'A')
    if (items.length >= 2) {
      const itemsMv = items.reduce((s, it) => s + (it.mv || 0), 0)
      if (itemsMv > 0) {
        const subs = {}
        for (const it of items) {
          const cid = it.category?.id || 'other'
          subs[cid] = (subs[cid] || 0) + (it.mv || 0)
        }
        const entries = Object.entries(subs)
        if (entries.length === 1) {
          const cat = items[0].category?.label || '同一类'
          warnings.push({
            level: 'high',
            text: `A股全押「${cat}」，板块未分散`,
          })
        } else {
          const top = entries.sort((a, b) => b[1] - a[1])[0]
          const topPct = top[1] / itemsMv * 100
          if (topPct >= 70) {
            const cat = items.find(it => (it.category?.id || 'other') === top[0])?.category?.label || '某类'
            warnings.push({
              level: 'med',
              text: `A股内「${cat}」占 ${topPct.toFixed(0)}%，板块过度集中`,
            })
          }
        }
      }
    }
    return { warnings }
  }, [rows])

  const removeAsset = async (row) => {
    if (!confirm(`删除 ${row.name}？`)) return
    // 证券持仓(A股/期货/期权)是 holdings 表 → 按 code(+方向) 删; 固定收益走 fix-income 路由;
    // 其余场外资产走 assets 路由
    if (row.src === 'fix-income') {
      await fetchJSON(`/api/fix-income/${row._raw.id}`, { method: 'DELETE' })
      loadFixAssets()
      return
    }
    if (row.type === 'A' || row.type === 'T' || row.type === 'O') {
      await api.deleteHolding(row._raw.stock_code, row._raw.direction)
      onHoldingsChanged?.()
    } else {
      await fetchJSON(`/api/assets/${row._raw.id}`, { method: 'DELETE' })
    }
    loadAssets()
  }

  const handleEdit = (row) => {
    // 持仓行(A股/期货/期权/基金, src=holding) → App 的 EditModal(shares/成本/最新价/删除);
    // 固定收益(src=fix-income) → FixIncomeEditRow; 其余场外资产(src=asset) → EditAssetRow
    if (row.src === 'holding') onEdit?.(row._raw)
    else if (row.src === 'fix-income') show('editFix', row._raw)
    else show('edit', row._raw)
  }
  const handleAddLot = (row) => {
    if (row.type === 'A') return  // A股 走 TransactionHistory
    if (row.type === 'T' || row.type === 'O') return show('futLot', row._raw)  // 期货/期权: 同向 OPEN
    if (row.src === 'holding') return onHistory?.(row._raw, 'BUY')  // 基金持仓: 买入=持仓流水预选 BUY
    show('addLot', row._raw)
  }
  const handleCloseLot = (row) => {
    if (row.type === 'T' || row.type === 'O') show('futClose', row._raw)       // 平仓 CLOSE
  }
  const handleReduceLot = (row) => {
    if (row.src === 'holding') return onHistory?.(row._raw, 'SELL') // 基金持仓: 卖出=持仓流水预选 SELL
    show('reduce', row._raw)
  }
  const handleShowActions = (row) => {
    if (row.type === 'A') return
    if (row.src === 'holding') return onHistory?.(row._raw)   // 基金持仓: 流水=position_actions(按代码)
    show('actions', row._raw)
  }

  // 马丁策略的"总预算"。OKX 的 raw 里没有这个字段, 算法反推出来的常常不准,
  // 所以允许按 OKX 客户端里看到的实际值覆盖(留空 = 恢复估算)。
  const editBotBudget = async (row) => {
    const cur = row.extra?.okxTotalBudgetUsdt
    const next = prompt(
      `「${row.name}」马丁实际总预算 (USDT)\n填 OKX 客户端策略详情看到的"总投资额", 留空恢复算法估算`,
      cur ? String(cur) : ''
    )
    if (next === null) return
    const v = next.trim() === '' ? null : parseFloat(next)
    if (next.trim() !== '' && (!v || v <= 0)) { alert('总预算必须为正数'); return }
    try {
      await fetchJSON(`/api/assets/${row.extra.assetIdRaw}`, {
        method: 'PUT',
        body: JSON.stringify({ bot_budget_override_usdt: v }),
      })
      await loadAssets()
    } catch (err) { alert('保存失败: ' + (err?.message || '')) }
  }

  // 行内操作的一整套回调。RowActions 的入参本来就是这几个, 所以整体传,
  // 不在 HoldingRow 上摊成九个 prop。
  // onHistory(raw, preset): A股 买入/卖出按钮打开流水弹窗并预选方向, preset 挂在 raw 上带给 App。
  const rowActions = {
    onEditBotBudget: editBotBudget,
    onEdit: handleEdit,
    onHistory: (raw, preset) => onHistory?.(preset ? { ...raw, presetType: preset } : raw), onRemove: removeAsset,
    onAddLot: handleAddLot, onReduceLot: handleReduceLot, onCloseLot: handleCloseLot,
    onShowActions: handleShowActions,
    onCashAdjust: (r) => show('cashAdjust', r._raw),
    onKline: (h) => show('kline', h),
    onThesis: (r) => show('thesis', r),
    onQuality: (r) => show('quality', r),
  }

  const isEmpty = rows.length === 0
  if (isEmpty && !assetsLoaded) {
    return <SkeletonCard rows={8} label="持仓加载中" />
  }

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      {/* Header: summary + donut */}
      <div className="px-3 md:px-6 py-3 md:py-5 border-b border-border flex flex-col md:flex-row md:flex-wrap md:justify-between md:items-center gap-4 md:gap-8"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex flex-col gap-3 w-full md:flex-1 md:w-auto min-w-0 md:min-w-[340px]">
          <div className="flex items-baseline gap-3">
            <h2 className="text-[15]px font-semibold text-text-bright tracking-wide m-0">持仓总览</h2>
            <span className="text-[12]px text-text-dim">股票 · 基金 · 固定收益 · 现金 · 加密 · 机器人</span>
          </div>
          {isEmpty
            ? <div className="text-text-dim text-[13]px py-2">还没有持仓,点击下方「+ 添加」开始</div>
            : <SummaryStrip agg={agg} aShareClosed={!aShareTradingDay} realized={realized} todayPnl={todayPnl} scope={filter} />
          }
        </div>
        {!isEmpty && <AllocationDonut groups={agg.groups} totalMv={agg.totalMv} />}
      </div>

      {/* Risk insights strip */}
      {!isEmpty && insights.warnings.length > 0 && (
        <div className="px-3 md:px-6 py-2.5 border-b border-border bg-surface-2/60 flex flex-col gap-1.5">
          {insights.warnings.map((w, i) => {
            const color = w.level === 'high' ? '#e58a8a' : '#d4a05c'
            return (
              <div key={i} className="flex items-center gap-2 text-[12.5]px">
                <span className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[11]px font-bold shrink-0"
                  style={{ background: `${color}1f`, color, border: `1px solid ${color}60` }}>!</span>
                <span className="text-text">{w.text}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Toolbar */}
      <div className="px-3 md:px-6 py-2 border-b border-border flex justify-between items-center gap-3 flex-wrap"
        style={{ background: 'var(--color-surface-2)' }}>
        <div className="flex gap-1.5 flex-wrap">
          {[['ALL', '全部'], ...tabDefs.map(d => [d.key, d.label])].map(([k, l]) => {
            const active = filter === k
            const c = k === 'ALL' ? '#c8a876' : (tabDefs.find(d => d.key === k)?.color || '#8a8378')
            return (
              <button key={k} onClick={() => setFilter(k)}
                className="px-2.5 py-[3px] rounded-md text-[12]px border transition-colors cursor-pointer"
                style={{
                  borderColor: active ? c : 'var(--color-border-med)',
                  background: active ? `${c}1a` : 'transparent',
                  color: active ? c : 'var(--color-text-dim)',
                }}>
                {l}
              </button>
            )
          })}
        </div>
        <div className="flex gap-1.5">
          <button onClick={() => setSortKey(sortKey === 'mv' ? 'pnl' : 'mv')}
            className="px-2.5 py-[3px] rounded-md text-[12]px border border-border-med bg-transparent text-text-dim hover:text-text transition-colors cursor-pointer">
            排序 · {sortKey === 'mv' ? '市值' : '盈亏'}
          </button>
          {/* 已选中具体类型时直接打开对应大类表单(表单内再选中类/币种); 只有 ALL 才出类型菜单。
              展示桶 → 大类(assets_classify.assets_class): 期货/期权同归 derivatives。 */}
          <button onClick={() => show('add', filter === 'ALL' ? 'menu' : filter)}
            title={filter !== 'ALL' ? `添加${tabDefs.find(d => d.key === filter)?.label || ''}` : undefined}
            className="px-2.5 py-[3px] rounded-md text-[12]px border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer">
            {filter !== 'ALL' ? `+ 添加${tabDefs.find(d => d.key === filter)?.label || ''}` : '+ 添加'}
          </button>
        </div>
      </div>

      {/* 弹窗与内联表单: 全部收在 PortfolioModals 里 */}
      <PortfolioModals
        open={open} show={show} hide={hide} brokers={brokers}
        onAdd={onAdd}
        onAssetsChanged={loadAssets}
        onFixIncomeChanged={loadFixAssets}
        onRealizedChanged={() => { loadAssets(); loadFixAssets(); loadRealized() }}
        onThesisSaved={loadThesisCodes} />

      {/* Column headers */}
      {!isEmpty && (
        <div className="licai-row px-3 md:px-6 py-2 text-[11.5]px text-text-dim tracking-wider font-medium border-b border-border bg-surface">
          <div className="text-left">名称 · 代码</div>
          <div className="text-left">交易方向</div>
          <div className="text-right">市值</div>
          <div className="text-right licai-md-only">成本</div>
          <div className="text-right">浮动盈亏</div>
          <div className="text-right">今日</div>
          <div className="text-right licai-md-only">占比</div>
          <div className="text-left pl-2">操作</div>
        </div>
      )}

      {/* Groups + rows */}
      {visibleTypes.map(type => {
        const g = agg.groups[type]
        const isCol = collapsed[type]
        const groupFxExposure = type === 'A' ? (agg.fxExposure || []) : []
        const items = [...g.items].sort((a, b) =>
          sortKey === 'pnl'
            ? (b.pnl || 0) - (a.pnl || 0)
            : (b.mv || 0) - (a.mv || 0)
        )
        return (
          <div key={type}>
            {/* Group strip */}
            <div onClick={() => setCollapsed(c => ({ ...c, [type]: !c[type] }))}
              className="licai-row px-3 md:px-6 py-2 border-b border-border cursor-pointer select-none items-center text-[12]px font-semibold text-text"
              style={{ background: 'var(--color-surface-2)' }}>
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 text-text-dim transition-transform"
                  style={{ transform: isCol ? 'rotate(-90deg)' : 'rotate(0)' }}>▾</span>
                <div className="w-[3px] h-[13px] rounded-sm" style={{ background: TYPE_COLOR[type] }} />
                <span>{TYPE_META[type].label}</span>
                <span className="text-text-dim font-normal text-[11.5]px">{items.length} 项</span>
              </div>
              <div />
              <div className="text-right flex flex-col items-end">
                <span className="font-mono text-text">¥{fmtMoney(g.mv)}</span>
                {groupFxExposure.length > 0 && (
                  <span className="font-mono text-[10.5]px text-text-muted hidden md:inline">
                    {groupFxExposure.map(e => formatCurrencyMoney(e.currency, e.originalMarketValue)).join(' / ')}
                  </span>
                )}
              </div>
              <div className="text-right font-mono text-text-dim text-[11.5]px licai-md-only">¥{fmtMoney(g.cost)}</div>
              <div className={`text-right font-mono ${priceColor(g.pnl)}`}>
                {g.pnl >= 0 ? '+' : ''}{fmtMoney(g.pnl)}
                <span className="text-[11]px opacity-80 ml-1.5">({fmtPct(g.pnlPct)})</span>
              </div>
              <div />
              <div className="text-right font-mono text-text licai-md-only">{(g.weight * 100).toFixed(1)}%</div>
              <div />
            </div>

            {/* Rows (with optional fund subcategory headers) */}
            {!isCol && (() => {
              // key 必须挂在这里而不是 HoldingRow 内部的根 div: 这个函数是在 map 里
              // 调的, React 认的是 map 直接产出的那个元素上的 key。
              const renderRow = (row, isLast) => (
                <HoldingRow key={row.id} row={row} isLast={isLast}
                  hovered={hoverId === row.id} onHover={setHoverId}
                  totalMv={agg.totalMv}
                  carry={carryByCode[row.code]} hasThesis={thesisCodes.has(row.code)}
                  actions={rowActions} />
              )

              // 基金按「基金类别」子类分组(场内ETF/场外基金/REITs…); 股票桶(A)按**市场**分两层: A股 / 港股 / 美股
              // (以前 A 股按行业分组 —— 行业来自外网公司概况接口, 现改为整体三层)。
              if (type === 'A' && items.length > 1) {
                const MK_LABEL = { A: 'A股', HK: '港股', US: '美股', JP: '日股' }
                const MK_ORDER = ['A', 'HK', 'US', 'JP']
                const clusters = {}
                for (const it of items) {
                  const mk = it.extra?.market || 'A'
                  if (!clusters[mk]) clusters[mk] = { id: `mk-${mk}`, label: MK_LABEL[mk] || mk, items: [], mv: 0, cost: 0, pnl: 0 }
                  clusters[mk].items.push(it)
                  clusters[mk].mv += it.mv || 0
                  clusters[mk].cost += it.cost || 0
                  clusters[mk].pnl += it.pnl || 0
                }
                const ordered = MK_ORDER.filter(mk => clusters[mk]).map(mk => clusters[mk])
                if (ordered.length > 1) {
                  return ordered.map((cl, ci) => {
                    const isLastCluster = ci === ordered.length - 1
                    return (
                      <React.Fragment key={cl.id}>
                        <div className="licai-row px-3 md:px-6 py-1.5 items-center text-[11.5]px text-text-dim border-b border-border-subtle"
                          style={{ background: 'var(--color-surface)' }}>
                          <div className="flex items-center gap-1.5 pl-4">
                            <span className="inline-block w-1 h-1 rounded-full" style={{ background: TYPE_COLOR[type] }} />
                            <span className="font-medium text-text">{cl.label}</span>
                            <span className="opacity-70">{cl.items.length} 项</span>
                          </div>
                          <div />
                          <div className="text-right font-mono">¥{fmtMoney(cl.mv)}</div>
                          <div className="licai-md-only" />
                          <div className={`text-right font-mono ${priceColor(cl.pnl)}`}>
                            {cl.pnl >= 0 ? '+' : ''}{fmtMoney(cl.pnl)}
                          </div>
                          <div />
                          <div className="licai-md-only" />
                          <div />
                        </div>
                        {cl.items.map((row, ri) =>
                          renderRow(row, isLastCluster && ri === cl.items.length - 1))}
                      </React.Fragment>
                    )
                  })
                }
              }
              // 基金: 按「基金类别」子类分组(>1 项才分组); 类别来自 assets_classify 小类,
              // 不再按基金名关键词匹配行业/主题(行业分类已下线)
              if (type === 'F' && items.length > 1) {
                const clusters = {}
                for (const it of items) {
                  const cid = it.category?.id || 'other'
                  if (!clusters[cid]) clusters[cid] = { id: cid, label: it.category?.label || '其他', items: [], mv: 0, cost: 0, pnl: 0 }
                  clusters[cid].items.push(it)
                  clusters[cid].mv += it.mv || 0
                  clusters[cid].cost += it.cost || 0
                  clusters[cid].pnl += it.pnl || 0
                }
                const ordered = Object.values(clusters).sort((a, b) => b.mv - a.mv)
                if (ordered.length > 1) {
                  return ordered.map((cl, ci) => {
                    const isLastCluster = ci === ordered.length - 1
                    return (
                      <React.Fragment key={cl.id}>
                        <div className="licai-row px-3 md:px-6 py-1.5 items-center text-[11.5]px text-text-dim border-b border-border-subtle"
                          style={{ background: 'var(--color-surface)' }}>
                          <div className="flex items-center gap-1.5 pl-4">
                            <span className="inline-block w-1 h-1 rounded-full" style={{ background: TYPE_COLOR[type] }} />
                            <span className="font-medium text-text">{cl.label}</span>
                            <span className="opacity-70">{cl.items.length} 项</span>
                          </div>
                          <div />
                          <div className="text-right font-mono">¥{fmtMoney(cl.mv)}</div>
                          <div className="licai-md-only" />
                          <div className={`text-right font-mono ${priceColor(cl.pnl)}`}>
                            {cl.pnl >= 0 ? '+' : ''}{fmtMoney(cl.pnl)}
                          </div>
                          <div />
                          <div className="licai-md-only" />
                          <div />
                        </div>
                        {cl.items.map((row, ri) =>
                          renderRow(row, isLastCluster && ri === cl.items.length - 1))}
                      </React.Fragment>
                    )
                  })
                }
              }
              return items.map((row, ri) => renderRow(row, ri === items.length - 1))
            })()}
          </div>
        )
      })}

      {filtered.length === 0 && !isEmpty && (
        <div className="py-8 text-center text-text-dim text-[13]px">
          当前筛选下没有持仓
        </div>
      )}

      <ClosedPositionsBlock
        items={[
          ...(realized?.stockItems || []).filter(i => !i.still_holding),
          // 已清仓的理财/基金/加密 (排除 CASH/BOT): 同一只基金分多次买卖会记成多条(不同 asset_id),
          // 按 code 合并、已实现盈亏求和, 一只基金只显示一行(避免同名重复)。
          ...Object.values(
            (realized?.assetItems || [])
              .filter(i => !i.still_holding && i.asset_type !== 'CASH' && i.asset_type !== 'BOT'
                && (i.realized_pnl || 0) !== 0)
              .reduce((acc, i) => {
                const key = i.code || `#${i.asset_id}`
                if (!acc[key]) acc[key] = { stock_code: key, stock_name: i.name, realized_pnl: 0,
                  _asset: true, asset_id: i.asset_id, code: i.code }
                acc[key].realized_pnl += (i.realized_pnl || 0)
                return acc
              }, {})
          ),
        ]}
        onHistory={onHistory} onKline={(h) => show('kline', h)} />
    </section>
  )
}
