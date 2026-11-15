import { fmtMoney, isOnchainEtf } from '../../helpers'
import { A_SECTORS, fundKindLabel } from './constants'

export function stockMarketOfCode(code = '') {
  const c = String(code).toUpperCase()
  // 标准码「代码.市场」(00700.HK / SNDK.US / 7203.JP); 前缀形态是迁移前存量, 兼容读
  if (c.endsWith('.HK') || c.startsWith('HK.')) return 'HK'
  if (c.endsWith('.US') || c.startsWith('US.')) return 'US'
  if (c.endsWith('.JP') || c.startsWith('JP.')) return 'JP'
  return 'A'
}

// 按代码推原币(接口没带 currency 时兜底)
export function currencyOfCode(code = '') {
  const m = stockMarketOfCode(code)
  return m === 'HK' ? 'HKD' : m === 'US' ? 'USD' : m === 'JP' ? 'JPY' : 'CNY'
}

// 期货/期权 thscode 的交易所后缀 → 短名(徽标用, 与股票行的 沪/深/北 市场徽标同一排)。
// 同花顺后缀: SHF 上期所 / DCE 大商所 / CZC 郑商所 / CFE 中金所 / INE 上能源 / GFE 广期所;
// 认不出的外盘后缀原样展示(SGE/NYM/...); 股票/港美代码返回 null(交给 MarketChip)。
const FUT_EXCHANGES = {
  SHF: '上期', DCE: '大商', CZC: '郑商', CFE: '中金', INE: '上能', GFE: '广期',
}
export function exchangeOf(code = '') {
  const c = String(code).toUpperCase()
  if (c.endsWith('.HK') || c.endsWith('.US') || c.startsWith('HK.') || c.startsWith('US.')) return null
  const m = c.match(/^[A-Z0-9]{1,16}\.([A-Z]{2,5})$/)
  if (!m) return null
  const suf = m[1]
  if (suf === 'SH' || suf === 'SZ' || suf === 'BJ') return null
  return FUT_EXCHANGES[suf] || suf
}

export function stockCodeForMarket(market, code) {
  // 统一标准「代码.市场」: 00700.HK / SNDK.US(futu 的「市场.代码」只在后端 futu 模块内转换)
  const raw = String(code || '').trim().toUpperCase()
  if (market === 'HK') {
    const body = raw.replace(/^HK\./, '').replace(/\.HK$/, '')
    return `${body.padStart(5, '0')}.HK`
  }
  if (market === 'US') {
    const body = raw.replace(/^US\./, '').replace(/\.US$/, '')
    return `${body}.US`
  }
  if (market === 'JP') {
    const body = raw.replace(/^JP\./, '').replace(/\.JP$/, '')
    return `${body}.JP`
  }
  return raw.replace(/^A\./, '')
}

export function currencySymbol(currency = 'CNY') {
  if (currency === 'USD') return 'US$'
  if (currency === 'HKD') return 'HK$'
  if (currency === 'JPY') return 'JP¥'
  if (currency === 'U' || currency === 'USDT') return '$'   // 加密(assets_classify 里 USDT 记 'U')
  return '¥'
}

export function formatCurrencyMoney(currency, value) {
  return `${currencySymbol(currency)}${fmtMoney(value)}`
}

export function fxSourceLabel(source) {
  if (source === 'sina_bid_ask_mid') return '新浪外汇买卖价中间价'
  if (source === 'fallback') return '备用汇率'
  if (source === 'last_known') return '上次成功汇率'
  if (source === 'CNY') return '人民币'
  return source || '汇率'
}

// 基金类别: assets_classify 小类(市场×形态) → { id, label }。id 用小类原文,
// 持仓表内同类归簇时天然同组。不再按基金名关键词猜行业/主题(那套已随行业分类下线)。
export function fundCategoryOf(sub = '') {
  const s = String(sub || '').trim()
  return { id: s || 'fund', label: fundKindLabel(s) }
}

function aShareCategoryOf(name = '', sector = '') {
  // 优先直接用后端东财真实一级行业(如 "有色金属"/"电子设备"/"电气设备")当分类 ——
  // 这是权威数据, 比粗分桶准。后端拿不到(网络失败)才回退按股票名 regex 兜底。
  const s = String(sector || '').split('-')[0].trim()
  if (s) return { id: s, label: s }
  for (const c of A_SECTORS) if (c.match.test(name)) return c
  return { id: 'other', label: '其他' }
}

export function stockCategoryOf(h) {
  const market = h.market || stockMarketOfCode(h.stock_code)
  if (market === 'HK') return { id: 'hk', label: '港股' }
  if (market === 'US') return { id: 'us', label: '美股' }
  if (market === 'JP') return { id: 'jp', label: '日股' }
  return aShareCategoryOf(h.stock_name, h.sector)
}

// 场内 ETF/LOF 判定 — 统一走 isOnchainEtf (深 1xxxxx / 沪 5xxxxx)。
// 旧规则只认 5xxxxx/159xxx 会把 16xxxx 的 LOF (如互联网QD 164906) 误判成场外。
export const isEtfCode = isOnchainEtf
