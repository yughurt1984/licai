export const TYPE_META = {
  A: { label: '股票',   short: '股', tintVar: '--color-accent',     desc: 'A股 / 港股 / 美股' },
  F: { label: '基金',   short: '基', tintVar: '--color-info',       desc: '公募 / ETF / LOF' },
  W: { label: '固定收益', short: '固', tintVar: '--color-bull',     desc: '理财 / 债券 (固定收益)' },
  M: { label: '现金',   short: '现', tintVar: '--color-text-dim',   desc: 'T+0 货币基金 / 银行活期' },
  C: { label: '加密',   short: '加', tintVar: '--color-warn',       desc: 'BTC / ETH / …' },
  R: { label: '机器人', short: '量', tintVar: '--color-text-dim',   desc: '量化 / 跟投' },
  T: { label: '期货',   short: '期', tintVar: '--color-bear',       desc: '国内商品/金融 · 外盘' },
  O: { label: '期权',   short: '权', tintVar: '--color-text-dim',   desc: '手填市值' },
}

// Fallback color hex for donut strokes (Tailwind arbitrary values can't use var() easily)
export const TYPE_COLOR = {
  // twelvedata 风格冷色板(取自其官方 CSS 变量): 品牌蓝锚定, 8 类色相互不混淆。
  // 只标识"资产类别", 不表达涨跌 —— 涨跌语义仍走 bear/bull 系 token
  A: '#2469f0', // 股票: 品牌蓝 (twelvedata --blue)
  F: '#17a2b8', // 基金: 青蓝 (twelvedata --cyan)
  W: '#2cc97e', // 固收: 稳定绿 (twelvedata --green)
  M: '#94a3b8', // 现金: 中性灰蓝 (流动性, 不抢眼)
  C: '#fd7e14', // 加密: 橙 (twelvedata --orange, 高波动)
  R: '#6f42c1', // 机器人: 紫 (twelvedata --purple, 量化)
  T: '#e3342f', // 期货: 红 (twelvedata --danger, 杠杆风险)
  O: '#e83e8c', // 期权: 品红 (twelvedata --pink, 衍生品, 与期货红区分)
}

export const TYPE_ORDER = ['A', 'F', 'W', 'M', 'C', 'R', 'T', 'O']

export const ASSET_TYPE_TO_KEY = { FUND: 'F', CRYPTO: 'C', BOT: 'R', WEALTH: 'W', CASH: 'M' }
// W 桶已由 fix-income(固定收益) 大类驱动(fix_income_assets 账本), WEALTH 只是老数据兼容标记。

// 持仓视图的展示桶(A/F/W/M/C/R/T/O) → 录入入口的大类(assets_class)。
// 仅用于「快速添加」按钮按当前筛选桶直接打开对应大类表单; 期货/期权同归 derivatives。
// 注意: 这是展示桶→大类的元映射, 不是资产清单硬编码; 资产种类/中类/小类本身全来自 assets_classify。
export const BUCKET_TO_CLASS = {
  A: 'stock', F: 'fund', W: 'fix-income', M: 'cash',
  C: 'crypto', R: 'bot', T: 'derivatives', O: 'derivatives',
}

// ── 资产类型唯一标准: assets_classify ──────────────────────────────────
// 展示分组(A股票/F基金/T期货/O期权/C加密/W理财/M现金)由 assets_classify 的
// 大类(assets_class) + 中类(main_assets_type) 派生, 不再逐个小类硬编码枚举:
//   stock→A  fund→F  crypto→C  cash_equal→M  derivatives 按 main 拆 fut→T / opt→O。
// 新增小类自动继承父类桶 —— 目录加行即生效, 前端零改动。
// SUB_TO_TYPE 仅作「持仓行缺 assets_class 字段」时的兜底(老数据/未联表处)。
export const SUB_TO_TYPE = {
  a_comm_stock: 'A', hk_comm_stock: 'A', us_comm_stock: 'A', us_prio_stock: 'A',
  cn_etf_fund: 'F', cn_lof_fund: 'F', hk_etf_fund: 'F', us_etf_fund: 'F',
  cn_futures: 'T', hk_futures: 'T', us_futures: 'T',
  cn_options: 'O', hk_options: 'O', us_options: 'O', hk_warrant: 'O',
  crypto_perpetual: 'C', crypto_spot: 'C',
  cn_wealth: 'W', cn_bond: 'W',   // 固定收益(fix-income): 理财 / 债券
}

// 由 assets_classify 行(后端随持仓透传)推导展示类型 —— 单一事实来源。
export function typeFromClassify(cls = {}) {
  const ac = String(cls.assets_class || '').toLowerCase()
  const mt = String(cls.main_assets_type || '').toLowerCase()
  if (ac === 'stock') return 'A'
  if (/fund/.test(mt)) return 'F'                 // 货基/ETF/LOF/REITS 等基金中类 → 基金桶
  if (ac === 'fund') return 'F'                   // 兜底(中类未带 fund 字样)
  if (ac === 'crypto') return 'C'
  if (ac === 'cash_equal') return 'M'              // 纯现金等价物(非货基)
  if (ac === 'fix-income') return 'W'              // 固定收益(理财/债券, valuation_type=yield)
  if (ac === 'derivatives') return /fut/.test(mt) ? 'T' : 'O'   // 期货/期权同属衍生品, 按中类拆
  return null   // commodity / forex / insurance: 暂无独立展示桶
}

export function typeOfSub(sub, cls) {
  // cls = { assets_class, main_assets_type } 来自后端 holdings JOIN assets_classify;
  // 有目录信息优先按目录派生, 否则回落 SUB_TO_TYPE(老数据/未联表)。
  if (cls && (cls.assets_class || cls.main_assets_type)) {
    const t = typeFromClassify(cls)
    if (t) return t
  }
  return SUB_TO_TYPE[String(sub || '').trim()] || null
}

export function isDerivSub(sub) {
  return ['T', 'O'].includes(typeOfSub(sub))
}

export const KEY_TO_ASSET_TYPE = { F: 'FUND', C: 'CRYPTO', R: 'BOT', W: 'WEALTH', M: 'CASH' }

// 券商佣金率（默认万 2.5; 在 config.py 改为你自己的费率）。影响"按股买"模式的手续费自动估算.
export const BROKER_COMMISSION_RATE = 0.00025

export const BROKER_COMMISSION_MIN = 5

export const STOCK_MARKETS = {
  A: { label: 'A股', placeholder: '600362', hint: '6位代码', minShares: 100, step: 100 },
  HK: { label: '港股', placeholder: '00700', hint: '港股5位代码', minShares: 1, step: 1 },
  US: { label: '美股', placeholder: 'AAPL', hint: '美股Ticker', minShares: 0.0001, step: 'any' },   // 美股可碎股(最多 4 位小数)
}

// 基金种类分类的唯一标准是 assets_classify.main_assets_type(后端透传 main_assets_type
// + assets_type_name 中文名)。这里只是后端字段缺失时的兜底标签, 不做名字关键词匹配 ——
// 原来按名字猜主题(黄金/半导体/消费…)已删: 名字匹配不严谨, 见 2026-11 分类改造。
export const FUND_MAIN_LABEL = {
  etf_fund: 'ETF',
  lof_fund: 'LOF',
  reits_fund: 'REITS',
  mm_fund: '场内货币基金',
  private_fund: '私募基金',
  otc_fund: '场外基金',
}

// A-share industry: keyword-driven. 优先匹配后端拉的真实行业字符串
// (e.g. "电气设备-电源设备")，否则回退到股票名兜底。
export const A_SECTORS = [
  { id: 'metals',    label: '有色金属', match: /有色|铜|铝|锌|镍|铅|稀土|钼|黄金|白银|钢铁/ },
  { id: 'newenergy', label: '电气新能源', match: /电气设备|电源设备|储能|光伏|锂电|动力电池|电池|风电设备|新能源(?!车)/ },
  { id: 'energy',    label: '能源',     match: /石油|煤|燃气|电力|核电|风电|公用事业|采掘/ },
  { id: 'finance',   label: '金融',     match: /银行|证券|保险|期货|信托|非银金融/ },
  { id: 'realestate',label: '地产',     match: /地产|置业|建设|建筑/ },
  { id: 'tech',      label: '科技',     match: /科技|半导体|芯片|软件|信息|电子|通信|计算|传媒/ },
  { id: 'consumer',  label: '消费',     match: /消费|食品|酒|乳业|家电|家用电器|零售|百货|医美|纺织|休闲服务/ },
  { id: 'medical',   label: '医药',     match: /医药|生物|医疗|制药|疫苗/ },
  { id: 'auto',      label: '汽车',     match: /汽车|新能源车|整车/ },
  { id: 'materials', label: '材料',     match: /钢|水泥|玻璃|化工|塑料|纤维|建筑材料|建筑装饰|轻工制造/ },
  { id: 'machinery', label: '机械',     match: /机械|装备|工程|重工|军工|国防/ },
]

export const FAMILY_LABEL = {
  silver: '白银', gold: '黄金', metals: '有色金属',
  overseas: '海外股票', cn_broad: 'A股宽基',
}

// 同源家族索引: 以后端穿透出来的**行业**为准。
// 为什么不能再靠名字: 「山金国际」是山金矿(东财行业 贵金属), 跟「中金黄金」本来就是同一块
// 风险, 但名字里一个金属字都没有 —— 下面那套关键词认不出它, 于是行内只有中金黄金挂了
// ↔同源, 山金干干净净, 看着像是两注不相干的仓。穿不动明细的基金(黄金ETF联接这种)后端
// 已按名字兜底挂了行业, 这里一并算进来。
// 只有 ≥2 行一起扛着才算"同源"; 一行独占那是集中度, 另有规则管。
export const MIN_HOLDER_PCT = 0.5     // 一行在这个家族里不到总资产 0.5% 就不标了: 债基前十大里

// ============================================================
// Add stock form — compact, inline
// ============================================================
// 漂移三档的配色: 跟股价走是唯一需要刺眼的一档 —— 它说的是"你改的是说法, 不是事实"
export const DRIFT_TONE = {
  '跟股价走': 'text-bear-bright border-bear-border bg-bear-bg',
  '跟事实走': 'text-text border-border bg-surface-3',
  '只改了说法': 'text-warn border-border-med bg-surface-3',
  '判不了': 'text-text-muted border-border bg-surface-3',
}

// 去劣筛选: 只有"被排除/未被排除/判不了"三种结论。判不了刻意用灰而不是绿 ——
// 缺数据不是通过, 混成一个颜色就等于用缺失冒充合格。
export const SCREEN_TONE = {
  '通过': 'text-bull',
  '排除': 'text-bear-bright',
  '豁免': 'text-warn',
  '判不了': 'text-text-muted',
  '不适用': 'text-text-muted',
}
