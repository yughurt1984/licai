export const ACQUIRE = new Set(['BUY', 'ADD', 'BONUS'])

export const MA_WARMUP = 60           // 日K 多取的均线预热根数(够 MA60 从首根可见蜡烛起连续)

// 涨跌/买卖色: 指向 index.css 的语义变量(--color-up/down/buy/sell), 由显示设置
// (红涨绿跌开关 + 深/浅主题)统一换色。SVG/DOM 场景可直接用作 stroke/fill/style;
// canvas 场景(lightweight-charts 等)必须经 colors.js 的 cssVar() 取解析值。
export const UP = 'var(--color-up)', DOWN = 'var(--color-down)'

export const BUY_COLOR = 'var(--color-buy)', SELL_COLOR = 'var(--color-sell)'

export const fmtVal = (v) => v == null ? '--' : v < 10 ? v.toFixed(3) : v < 100 ? v.toFixed(2) : v.toFixed(1)

export const fmtPct = (v) => v == null ? '--' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%'

export const colorPct = (v) => v == null ? 'text-text-dim' : v >= 0 ? 'text-up-bright' : 'text-down-bright'

export const fmtHand = (h, unit = '手') => h == null ? '--'
  : h >= 1e8 ? (h / 1e8).toFixed(2) + '亿' + unit
  : h >= 1e4 ? (h / 1e4).toFixed(1) + '万' + unit
  : Math.round(h) + unit

export const colorPctHex = (v) => v >= 0 ? UP : DOWN

// ProKline / DayOverlay 用的价格格式化。⚠️ 与上面的 fmtVal 只差一点(取绝对值判档、
// 档位也不同), 但两边的显示精度都是调过的 —— 合并会静默改掉其中一边的位数, 所以分开放。
export const fmt = (v) => v == null ? '--' : Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 10 ? v.toFixed(2) : v.toFixed(3)
