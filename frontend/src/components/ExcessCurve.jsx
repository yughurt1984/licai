import { useCallback, useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// 超额收益曲线: 超额 = 当前收益(TWR) − 基准收益, 两条同起点归一 100 的曲线逐日相减。
// 复用 /api/portfolio/curve 端点(按 days+symbol 服务端缓存 1h), 换基准只换对照线。
// 红跑赢 / 绿跑输, 与看板红涨绿跌同一套颜色语义。
const SYMBOLS = [
  { v: 'sh000300', label: '沪深 300' },
  { v: 'sh000001', label: '上证指数' },
  { v: 'sz399006', label: '创业板指' },
  { v: 'sh000688', label: '科创 50' },
  { v: 'hkHSI',    label: '恒生指数' },
  { v: 'gb_inx',   label: '标普 500' },
  { v: 'gb_ixic',  label: '纳斯达克' },
]
const PERIODS = [
  { key: 60, label: '3月' },
  { key: 120, label: '半年' },
  { key: 250, label: '一年' },
]

// 红跑赢 / 绿跑输(priceColor 同款语义)
const WIN = '#e06666'
const LOSE = '#4f9e63'
const toneOf = (v) => (v > 0 ? WIN : v < 0 ? LOSE : 'var(--color-text-dim)')

const fmtPct = (v) => v == null ? '--' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'

function Chart({ dates, excess }) {
  const W = 680, H = 190, ML = 6, MR = 46, MT = 8, MB = 16
  const vals = excess.filter(v => v != null)
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals)
  const pad = Math.max((hi - lo) * 0.1, 0.3)
  const y0v = lo - pad, y1v = hi + pad
  const py = (v) => MT + (H - MT - MB) * (1 - (v - y0v) / (y1v - y0v))
  const n = dates.length
  const bw = Math.max((W - ML - MR) / Math.max(n, 1) - 0.6, 1)
  const px = (i) => ML + (W - ML - MR) * (i + 0.5) / Math.max(n, 1)
  const ticks = [0, 1, 2, 3].map(k => Math.round(k * (n - 1) / 3))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 'auto' }}>
      {/* 零轴: 超额为 0 = 与基准打平 */}
      <line x1={ML} y1={py(0)} x2={W - MR} y2={py(0)}
        stroke="var(--color-border-med)" strokeDasharray="4 4" strokeWidth="1" />
      {excess.map((v, i) => v == null ? null : (
        <rect key={i} x={px(i) - bw / 2} width={bw}
          y={v >= 0 ? py(v) : py(0)} height={Math.abs(py(v) - py(0))}
          fill={v >= 0 ? WIN : LOSE} opacity="0.8" />
      ))}
      <text x={W - MR + 4} y={py(excess[n - 1]) + 3.5} fontSize="10.5" fontWeight="600"
        fill={toneOf(excess[n - 1])}>{fmtPct(excess[n - 1])}</text>
      <text x={W - MR + 4} y={py(0) + 3.5} fontSize="9.5" fill="var(--color-text-muted)">0</text>
      {ticks.map(i => (
        <text key={i} x={px(i)} y={H - 3} fontSize="9.5" fill="var(--color-text-muted)"
          textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
          {dates[i]?.slice(5)}
        </text>
      ))}
    </svg>
  )
}

export default function ExcessCurve() {
  const [symbol, setSymbol] = useState(() => localStorage.getItem('excessSymbol') || 'sh000300')
  const [days, setDays] = useState(() => parseInt(localStorage.getItem('excessDays') || '60'))
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const d = await fetchJSON(`/api/portfolio/curve?days=${days}&symbol=${symbol}`)
      setData(d)
      if (d.error) setErr(d.error)
    } catch (e) { setErr(e?.message || '加载失败') }
    finally { setLoading(false) }
  }, [days, symbol])

  useEffect(() => { load() }, [load])

  const pick = (v) => { setSymbol(v); localStorage.setItem('excessSymbol', v) }
  const pickDays = (v) => { setDays(v); localStorage.setItem('excessDays', String(v)) }

  if (loading && !data) return <SkeletonCard bare rows={4} label="超额收益计算中…（首算要拉各标的的历史K线, 之后走缓存）" />
  if (err && !data?.dates) {
    return <div className="text-[11.5px] text-text-dim py-3">{err}</div>
  }
  if (!data?.dates) return null

  const { dates, twr, bench } = data
  const excess = twr.map((t, i) => (t == null || bench.series[i] == null) ? null : t - bench.series[i])
  const m = data.metrics || {}
  const cov = data.coverage || {}
  const beatPct = cov.total_days ? Math.round(cov.beat_days / cov.total_days * 100) : null

  return (
    <div className="mb-3">
      {/* 控制条: 基准 + 区间 (chips 同跑赢基准对照) */}
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <div className="text-[11px] text-text-muted tracking-wider">超额 = 当前收益 − 基准收益（同起点归一）</div>
        <div className="ml-auto flex gap-1 items-center flex-wrap">
          {SYMBOLS.map(s => (
            <button key={s.v} onClick={() => pick(s.v)}
              className="px-2 py-0.5 rounded text-[10.5px] cursor-pointer"
              style={{
                border: '1px solid',
                borderColor: symbol === s.v ? 'var(--color-accent)' : 'var(--color-border-med)',
                color: symbol === s.v ? 'var(--color-accent)' : 'var(--color-text-dim)',
                background: symbol === s.v ? 'var(--color-accent)1a' : 'transparent',
              }}>
              {s.label}
            </button>
          ))}
          <span className="mx-0.5 text-text-muted">·</span>
          {PERIODS.map(p => (
            <button key={p.key} onClick={() => pickDays(p.key)}
              className={`px-2 py-0.5 rounded text-[10.5px] cursor-pointer ${days === p.key ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-4 mb-1 flex-wrap">
        <span className="text-[11px] text-text-muted">区间超额
          <span className="font-mono font-semibold text-[13px] ml-1"
            style={{ color: toneOf(m['超额%']) }}>{fmtPct(m['超额%'])}</span>
        </span>
        <span className="text-[11px] text-text-muted">我的
          <span className="font-mono text-[12px] ml-1"
            style={{ color: toneOf(m['区间收益%']) }}>{fmtPct(m['区间收益%'])}</span>
        </span>
        <span className="text-[11px] text-text-muted">
          {SYMBOLS.find(s => s.v === symbol)?.label || symbol}
          <span className="font-mono text-[12px] ml-1"
            style={{ color: toneOf(m['基准收益%']) }}>{fmtPct(m['基准收益%'])}</span>
        </span>
        {beatPct != null && (
          <span className="text-[11px] text-text-muted">跑赢 <span className="font-mono text-[12px] text-text">{beatPct}%</span> 的交易日</span>
        )}
        <span className="text-[10px] text-text-dim ml-auto">起点 {m['起点'] || '--'}</span>
      </div>

      <Chart dates={dates} excess={excess} />

      <div className="flex items-center gap-3 mt-0.5 flex-wrap">
        <span className="text-[9.5px] text-text-dim"><span className="inline-block w-2.5 h-[7px] align-middle mr-1" style={{ background: WIN, opacity: 0.8 }} />跑赢基准</span>
        <span className="text-[9.5px] text-text-dim"><span className="inline-block w-2.5 h-[7px] align-middle mr-1" style={{ background: LOSE, opacity: 0.8 }} />跑输基准</span>
        {cov.missing_prices?.length > 0 && (
          <span className="text-[9.5px] text-warn">部分标的取不到历史价({cov.missing_prices.join('、')}), 该部分市值未计入</span>
        )}
        <span className="text-[9px] text-text-dim ml-auto">{data.note}</span>
      </div>
    </div>
  )
}
