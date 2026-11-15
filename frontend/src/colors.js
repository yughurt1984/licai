/**
 * canvas 图表取色: CSS 变量 → 实时计算值。
 *
 * canvas 不吃 CSS 层叠 —— fillStyle 必须是具体色值。图表组件在绘制时调 cssVar()
 * 读 documentElement 的计算值, 并把 useThemeTick() 加进重绘 useEffect 的依赖,
 * 主题/涨跌向切换时重读重画(变量值已换, 取到的是新色)。
 *
 * 变量值允许两种形态: '#rrggbb(aa)'(本库 @theme 全是这种) 与 'r,g,b'(无 alpha 需求
 * 的场景不走这条路径)。withAlpha 解析 hex(3/6/8 位)注入任意透明度 —— 图表里
 * "同色系淡一点"的渐变/背景块用它, 替代原先写死的 hex+透明度拼接。
 */

const _cache = new Map()

/** 读 CSS 变量当前计算值。取不到(变量名写错/SSR)返回 fallback。 */
export function cssVar(name, fallback = '#888888') {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    if (v) { _cache.set(name, v); return v }
  } catch { /* 无 document(测试环境) */ }
  return _cache.get(name) || fallback
}

function _parseHex(hex) {
  let h = hex.replace('#', '').trim()
  if (h.length === 3) h = h.split('').map(c => c + c).join('')
  if (h.length === 6) h += 'ff'
  if (h.length !== 8 || /[^0-9a-fA-F]/.test(h)) return null
  return [0, 2, 4, 6].map(i => parseInt(h.slice(i, i + 2), 16))
}

/** hex(含可选 alpha) → rgba(r,g,b,a)。解析失败返回原值(canvas 自己兜底)。 */
export function withAlpha(hex, alpha) {
  const c = _parseHex(hex)
  if (!c) return hex
  const [r, g, b] = c
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** 涨跌色直取: 涨/跌跟随显示设置的红涨绿跌(或绿涨红跌)。 */
export const upColor = () => cssVar('--color-up', '#c04a4a')
export const downColor = () => cssVar('--color-down', '#35824a')
