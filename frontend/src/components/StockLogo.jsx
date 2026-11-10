import { useState } from 'react'

// 股票/基金 logo 圆标: <img src="/api/market/logo?code=..."> → 后端 icons/ 磁盘缓存,
// 未命中才联网拉 logostream(需在 config.env 配 LOGOSTREAM_API_KEY)。
// 404(无 logo/未配 key/场外基金等无交易所 ticker 的码) → 退回首字母圆标, 不留空位。
// 加密/期货/期权/量化(type 不在 LOGO_TYPES)天生没有品牌 logo, 直接不渲染。
const LOGO_TYPES = new Set(['A', 'F'])

export default function StockLogo({ type, code, name, size = 20 }) {
  const [failed, setFailed] = useState(false)
  // 换码时重置失败标记: 复用同一处组件(列表虚拟滚动/行内更新)不能背着上一次的失败状态。
  // 渲染期重置(官方 "adjusting state during render" 模式), 不用 effect —— effect 里同步
  // setState 会被 react-hooks 规则拦(级联渲染), 本仓 lint 已开这条。
  const [prevCode, setPrevCode] = useState(code)
  if (prevCode !== code) {
    setPrevCode(code)
    setFailed(false)
  }

  if (!LOGO_TYPES.has(type) || !code) return null
  const ch = String(name || code).trim().charAt(0).toUpperCase()

  if (failed) {
    return (
      <span aria-hidden className="rounded-full shrink-0 inline-flex items-center justify-center
        font-semibold select-none bg-surface-3 text-text-dim border border-border-subtle"
        style={{ width: size, height: size, fontSize: Math.round(size * 0.52) }}>
        {ch}
      </span>
    )
  }
  return (
    <img
      src={`/api/market/logo?code=${encodeURIComponent(code)}`}
      alt="" loading="lazy" decoding="async"
      onError={() => setFailed(true)}
      className="rounded-full shrink-0 object-contain bg-surface border border-border-subtle"
      style={{ width: size, height: size }}
    />
  )
}
