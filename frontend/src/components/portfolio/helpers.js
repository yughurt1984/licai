import { fmtMoney, isOnchainEtf } from '../../helpers'
import { A_SECTORS, FUND_MAIN_LABEL, MIN_HOLDER_PCT } from './constants'

export function stockMarketOfCode(code = '') {
  const c = String(code).toUpperCase()
  // 标准码「代码.市场」(00700.HK / SNDK.US); 前缀形态是迁移前存量, 兼容读
  if (c.endsWith('.HK') || c.startsWith('HK.')) return 'HK'
  if (c.endsWith('.US') || c.startsWith('US.')) return 'US'
  return 'A'
}

// 按代码推原币(接口没带 currency 时兜底)
export function currencyOfCode(code = '') {
  const m = stockMarketOfCode(code)
  return m === 'HK' ? 'HKD' : m === 'US' ? 'USD' : 'CNY'
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
  return raw.replace(/^A\./, '')
}

export function currencySymbol(currency = 'CNY') {
  if (currency === 'USD') return 'US$'
  if (currency === 'HKD') return 'HK$'
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

// 基金种类: 唯一标准是 assets_classify.main_assets_type —— 后端透传中类名
// (assets_type_name: ETF/LOF/场外基金/…)和 main_assets_type 键, 这里只做取值与兜底,
// 不再按名字关键词猜分类(名字匹配不严谨, 已删, 见 2026-11 分类改造)。
export function fundCategoryOf(h = {}) {
  const label = String(h.assets_type_name || '').trim()
  if (label) return { id: label, label }
  const main = String(h.main_assets_type || '').trim()
  if (main && main !== 'common_share') return { id: main, label: FUND_MAIN_LABEL[main] || main }
  return { id: 'other', label: '其他' }
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
  return aShareCategoryOf(h.stock_name, h.sector)
}

                               // 混进一只光模块(实测 392 元)也算"同源"的话, 角标就成噪音了
export function buildFamilyIndex(expo) {
  if (!expo || !Array.isArray(expo.industries)) return null
  const total = expo.total || 0
  const floor = total * MIN_HOLDER_PCT / 100
  const byRow = new Map()      // "A:600489" -> [行业名]
  const fams = new Map()       // 行业名 -> {holders, mv}
  for (const g of expo.industries) {
    const ind = g.industry || ''
    if (!ind || ind === '未知行业' || ind.startsWith('海外')) continue
    const holders = (g.holders || []).filter(h => (h.mv || 0) >= floor)
    if (holders.length < 2) continue
    // 家族的钱按**穿透后**算: 那几只基金并不是整只都在这个行业里
    fams.set(ind, { holders, mv: holders.reduce((s, h) => s + (h.mv || 0), 0) })
    for (const h of holders) {
      for (const c of (h.codes && h.codes.length ? h.codes : [h.code])) {
        const k = `${h.kind}:${c}`
        byRow.set(k, [...(byRow.get(k) || []), ind])
      }
    }
  }
  return { byRow, fams }
}

// 后端还没返回时的兜底(只认名字, 认不出的多了去了 —— 见上)。
// Returns array of family ids (a row can belong to multiple, e.g. 白银 is both silver + metals).
export function riskFamiliesOf(row) {
  if (row.type === 'A') {
    const name = row.name || ''
    const fams = []
    if (/白银/.test(name)) fams.push('silver', 'metals')
    else if (/黄金/.test(name)) fams.push('gold')
    else if (/有色|铜|铝|锌|镍|铅|锂|钴|稀土|钼/.test(name)) fams.push('metals')
    return fams
  }
  if (row.type === 'F') {
    // 分类改造后 category.id 是 assets_classify 中类(etf_fund/otc_fund/…), 分不出主题,
    // 所以家族兜底和 A 股分支一样直接认名字 —— 这里只是"后端还没返回时"的兜底角标,
    // 不是展示分类, 名字匹配的粗糙度可以接受(与上面 A 股分支同一精神)。
    const name = row.name || ''
    if (/白银|银ETF/.test(name)) return ['silver', 'metals']
    if (/黄金|金ETF/.test(name)) return ['gold']
    if (/原油|能源|煤炭|豆粕|商品/.test(name)) return ['metals']
    if (/QDII|纳斯达克|纳指|标普|恒生|中概|海外|日经|全球|港美/.test(name)) return ['overseas']
    if (/沪深300|中证500|中证1000|中证2000|上证50|创业板|科创|双创|中证|国证|红利/.test(name)) return ['cn_broad']
    return []
  }
  return []
}

// 场内 ETF/LOF 判定 — 统一走 isOnchainEtf (深 1xxxxx / 沪 5xxxxx)。
// 旧规则只认 5xxxxx/159xxx 会把 16xxxx 的 LOF (如互联网QD 164906) 误判成场外。
export const isEtfCode = isOnchainEtf
