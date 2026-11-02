import { useCallback, useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'
import { fmtMoney } from '../helpers'

// 月度盈亏日历: 每天的当日盈亏 + 当日收益率, 与跑赢基准对照并列。
// 数据来自 portfolio_snapshots 每日快照(打开本页时后端 lazy 落当天), 不是逐日回算 ——
// 价格是易逝数据, 当天的盈亏只能在当天钉下来, 没打开应用的日子就是没有。
const WEEK = ['一', '二', '三', '四', '五', '六', '日']

// 与 today_pnl._today_cst 同口径的"今天"(北京时间), 避免 UTC toISOString 在凌晨错位
const todayCST = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10)

const shiftMonth = (ym, delta) => {
  const [y, m] = ym.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

const fmtPct = (v) => v == null ? '' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'
const fmtSigned = (v) => v == null ? '--' : (v > 0 ? '+' : v < 0 ? '−' : '') + '¥' + fmtMoney(Math.abs(v))

// 红盈绿亏(中国市场口径, 与 priceColor 一致)
const tone = (v) => (v > 0
  ? { text: '#e06666', bg: 'rgba(207,92,92,0.10)' }
  : v < 0
    ? { text: '#4f9e63', bg: 'rgba(95,168,108,0.10)' }
    : { text: 'var(--color-text-dim)', bg: 'transparent' })

export default function PnlCalendar() {
  const [month, setMonth] = useState(() => todayCST().slice(0, 7))
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try { setData(await fetchJSON(`/api/portfolio/pnl-calendar?month=${month}`)) }
    catch (e) { setErr(e?.message || '加载失败') }
    finally { setLoading(false) }
  }, [month])
  useEffect(() => { load() }, [load])

  if (loading && !data) return <SkeletonCard rows={4} label="盈亏日历加载中" />
  if (err) {
    return (
      <section className="rounded-xl border border-border bg-surface/60 px-3 md:px-5 py-3 text-[12px] text-bear">
        {err}
      </section>
    )
  }
  if (!data) return null

  const byDate = {}
  for (const d of data.days || []) byDate[d.date] = d
  const [y, m] = month.split('-').map(Number)
  const daysInMonth = new Date(y, m, 0).getDate()
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7      // 周一开头
  const today = todayCST()
  const mp = data.month_pnl
  const mpTone = tone(mp)
  const atLatest = month >= today.slice(0, 7)

  return (
    <section className="rounded-xl border border-border bg-surface/60 overflow-hidden"
      style={{ animation: 'fade-up 0.4s ease-out' }}>
      {/* 卡头: 标题 + 月份切换 + 当月合计 (与跑赢基准对照同款卡头) */}
      <div className="px-3 md:px-5 py-3 border-b border-border flex items-baseline justify-between flex-wrap gap-2"
        style={{ background: 'linear-gradient(180deg, var(--color-surface-2), var(--color-surface))' }}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-[13px] font-semibold text-text-bright m-0">盈亏日历</h3>
          <span className="text-[11px] text-text-dim">
            {data.recorded_days > 0
              ? <>当月 <span className="font-mono font-semibold" style={{ color: mpTone.text }}>{fmtSigned(mp)}</span> · {data.recorded_days} 天有记录</>
              : '当月暂无记录'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setMonth(shiftMonth(month, -1))} title="上个月"
            className="px-2 py-[2px] rounded text-[12px] border border-border-med text-text-dim hover:text-text cursor-pointer">‹</button>
          <span className="font-mono text-[12px] text-text px-1">{month}</span>
          <button onClick={() => setMonth(shiftMonth(month, 1))} disabled={atLatest} title={atLatest ? '没有更新的记录' : '下个月'}
            className={`px-2 py-[2px] rounded text-[12px] border border-border-med ${atLatest ? 'opacity-30 cursor-not-allowed text-text-muted' : 'text-text-dim hover:text-text cursor-pointer'}`}>›</button>
          {month !== today.slice(0, 7) && (
            <button onClick={() => setMonth(today.slice(0, 7))}
              className="ml-1 px-2 py-[2px] rounded text-[10.5px] border border-accent/40 text-accent hover:bg-accent/10 cursor-pointer">今</button>
          )}
        </div>
      </div>

      {/* 星期表头 + 日格 */}
      <div className="px-2 md:px-3 py-2.5">
        <div className="grid grid-cols-7 gap-1 mb-1">
          {WEEK.map(w => (
            <div key={w} className="text-center text-[10px] text-text-muted py-0.5">{w}</div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1">
          {Array.from({ length: lead }).map((_, i) => <div key={`lead${i}`} />)}
          {Array.from({ length: daysInMonth }).map((_, i) => {
            const date = `${month}-${String(i + 1).padStart(2, '0')}`
            const rec = byDate[date]
            const isToday = date === today
            const isFuture = date > today
            const t = rec?.pnl != null ? tone(rec.pnl) : null
            const assets = rec?.assets ? Object.entries(rec.assets) : []
            return (
              <div key={date}
                title={assets.length
                  ? `${date}\n${assets.map(([c, p]) => `${c} ${(p > 0 ? '+' : '') + p.toFixed(2)}`).join('\n')}`
                  : undefined}
                className="rounded-md px-1 py-1 min-h-[58px] flex flex-col"
                style={{
                  background: t?.bg || (isFuture ? 'transparent' : 'rgba(128,128,128,0.05)'),
                  border: isToday ? '1.5px solid var(--color-accent)' : '1px solid transparent',
                  opacity: isFuture ? 0.35 : 1,
                }}>
                <span className={`text-[10px] leading-none ${isToday ? 'text-accent font-semibold' : 'text-text-muted'}`}>
                  {i + 1}
                </span>
                {rec?.pnl != null ? (
                  <>
                    <span className="font-mono text-[10.5px] leading-tight mt-0.5" style={{ color: t.text }}>
                      {rec.pnl > 0 ? '+' : rec.pnl < 0 ? '−' : ''}{fmtMoney(Math.abs(rec.pnl))}
                    </span>
                    <span className="font-mono text-[9.5px] leading-tight opacity-80" style={{ color: t.text }}>
                      {fmtPct(rec.pct)}
                    </span>
                  </>
                ) : !isFuture && (
                  <span className="text-[9.5px] text-text-muted mt-auto pb-0.5">—</span>
                )}
              </div>
            )
          })}
        </div>
        <div className="text-[9.5px] text-text-muted mt-1.5 leading-relaxed">
          当日盈亏=现市值+今日卖出所得−昨收市值−今日买入成本(含手续费), 与顶栏「今日」同一份计算; 现金/理财利息不计。
          {data.snapshot_error && <span className="text-warn"> 今天快照失败: {data.snapshot_error}</span>}
        </div>
      </div>
    </section>
  )
}
