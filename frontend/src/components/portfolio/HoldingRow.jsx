import Tooltip from '../Tooltip'
import StockLogo from '../StockLogo'
import { fmtMoney, fmtPct, priceColor } from '../../helpers'
import { BrokerChip, ExchangeChip, FxHint, MarketChip, RowActions, TodayPulse, TypeChip, TypeMiniInfo, WeightBar } from './atoms'
import { TYPE_COLOR } from './constants'
import { formatCurrencyMoney } from './helpers'

// 交易方向徽标(做多=涨向色 / 做空=跌向色): 颜色跟显示设置的红涨绿跌配置走 ——
// 红涨绿跌时 做多=红/做空=绿, 绿涨红跌时对调。用 --color-up/--color-down 变量即自动跟随。
function DirectionBadge({ direction }) {
  return (
    <span className={`whitespace-nowrap text-[0.7143rem] px-1 py-[1px] rounded border font-semibold
      ${direction === 'LONG'
        ? 'bg-up/15 text-up border-up/40' : 'bg-down/15 text-down border-down/40'}`}>
      {direction === 'LONG' ? '做多' : '做空'}
    </span>
  )
}

// 标的名称按字符数分档缩放(与容器宽度无关, 纯渲染无副作用):
//   ≤10 字符 = 大号 1rem / 11-12 = 中号 13px / 13-14 = 小号 12px / >14 保持小号。
// 中文等代理对用 Array.from 计 1 个字符(不用 .length, 避免 emoji 被算成 2)。
// 超长仍放不下时由 truncate 落省略号 + title 提示全名。
function FitName({ text }) {
  const chars = Array.from(String(text || '')).length
  const size = chars <= 10 ? 'text-[1rem]'
    : chars <= 12 ? 'text-[0.9286rem]'
      : 'text-[0.8571rem]'
  return (
    <span title={text}
      className={`flex-1 min-w-0 truncate font-semibold text-text-bright ${size}`}>
      {text}
    </span>
  )
}

// 持仓列表的一行。原来是主组件里一个 256 行的 renderRow 闭包 —— 它捕获了
// hoverId / insights / carryByCode / thesisCodes 和六个 handle* , 所以整段
// 只能待在主组件里, 而主组件因此没法读。
//
// 这些闭包变量按性质分成了三类 props:
//   - 行自己的: row / isLast / hovered / carry / hasThesis
//   - 跨行才算得出来的: totalMv(算占比)
//   - 动作: actions 一个对象整体转给 RowActions(它的入参本来就是这几个)
export function HoldingRow({ row, isLast, hovered, onHover, totalMv, carry, hasThesis, actions }) {
  return (
  <div
    onMouseEnter={() => onHover(row.id)}
    onMouseLeave={() => onHover(null)}
    className="licai-row px-3 md:px-6 py-[11px] items-center transition-colors"
    style={{
      borderBottom: isLast ? '1px solid var(--color-border)' : '1px solid var(--color-border-subtle)',
      background: hovered ? 'var(--color-surface-2)' : 'transparent',
    }}>
    <div className="flex flex-col gap-1.5 min-w-0">
      <div className="flex items-center gap-1.5 min-w-0">
        <StockLogo type={row.type} code={row.code} name={row.name} size={28} />
        <FitName text={row.name} />
      </div>
      {/* 资产类别 / 国旗 / 券商统一放第二行, 跟在 code 后面(名称行只留 logo+名称) */}
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="font-mono text-[0.7857rem] text-text-muted truncate">{row.code}</span>
        <TypeChip type={row.type} compact />
        {/* 期货/期权: 交易所国旗(上期/大商/… 统一中国旗, 外盘按所属国家) — 与股票行的国旗同位;
            多空方向(做多/做空)在后面"交易方向"列统一展示, 不挤在这里 */}
        {(row.type === 'T' || row.type === 'O') && <ExchangeChip code={row.code} />}
        {(row.type === 'A' || row.type === 'F' || row.type === 'W') && <MarketChip market={row.type === 'W' ? 'CN' : row.extra?.market} code={row.code} />}
        {row.broker && <BrokerChip name={row.broker} />}
      </div>
    </div>
    {/* 交易方向(统一列, 名称·代码 与 市值之间): 股票/期货 显示 做多/做空;
        期权在此基础上追加 权利方/义务方 标签(与做多/做空正交的另一维度), 上下堆叠避免挤压折行 */}
    <div className="flex flex-col items-start gap-1">
      {row.extra?.direction && <DirectionBadge direction={row.extra.direction} />}
      {row.type === 'O' && row.extra?.direction && (
        <span className="whitespace-nowrap text-[0.7143rem] px-1 py-[1px] rounded border font-semibold bg-accent/15 text-accent border-accent/40"
          title={row.extra.direction === 'LONG'
            ? '权利方: 买入期权, 付权利金, 最大亏损=权利金'
            : '义务方: 卖出/写期权, 收权利金, 有履约义务'}>
          {row.extra.direction === 'LONG' ? '权利' : '义务'}
        </span>
      )}
    </div>
    <div className="text-right flex flex-col items-end">
      <span className="font-mono text-[0.9643rem] text-text-bright tabular-nums">
        ¥{fmtMoney(row.mv)}
      </span>
      {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalMarketValue != null && (
        <FxHint extra={row.extra}>
          <span className="font-mono text-[0.7857rem] text-text-muted tabular-nums cursor-help">
            {formatCurrencyMoney(row.extra.currency, row.extra.originalMarketValue)}
          </span>
        </FxHint>
      )}
      {/* 第三行 = 最新价(原币标价; 现金类无价不显示) */}
      {row._raw?.current_price != null && (
        <span className="font-mono text-[0.7857rem] text-text-muted/80 tabular-nums">
          {formatCurrencyMoney(row.extra?.currency || 'CNY', row._raw.current_price)}
        </span>
      )}
    </div>
    <div className="text-right flex flex-col items-end licai-md-only">
      <span className="font-mono text-[0.9643rem] text-text-dim tabular-nums">
        ¥{fmtMoney(row.cost)}
      </span>
      {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalCostValue != null && (
        <FxHint extra={row.extra}>
          <span className="font-mono text-[0.7857rem] text-text-muted tabular-nums cursor-help">
            {formatCurrencyMoney(row.extra.currency, row.extra.originalCostValue)}
          </span>
        </FxHint>
      )}
      {/* 第三行 = 成本价(原币标价) */}
      {row._raw?.cost_price != null && (
        <span className="font-mono text-[0.7857rem] text-text-muted/80 tabular-nums">
          {formatCurrencyMoney(row.extra?.currency || 'CNY', row._raw.cost_price)}
        </span>
      )}
    </div>
    <div className="text-right flex flex-col items-end">
      {row.type === 'M' && row.extra?.monthlyInterestEst ? (
        <Tooltip content={
          <div className="leading-relaxed">
            <div className="text-text-bright font-semibold mb-1">月息流估算</div>
            <div>当前余额 ¥{fmtMoney(row.mv)} × 年化 {(row.extra.annualYield * 100).toFixed(2)}% / 12</div>
            <div className="mt-1 text-[0.8214rem] text-text-dim">实际利率每天微变，估算 ±5% 误差</div>
            <div className="mt-1 text-[0.8214rem] text-bull-bright">
              日息 ≈ +¥{(row.extra.dailyInterestEst || 0).toFixed(2)} · 年息 ≈ +¥{fmtMoney(row.extra.yearlyInterestEst || 0)}
            </div>
          </div>
        }>
          <div className="flex flex-col items-end cursor-help">
            <span className="font-mono text-[0.9643rem] font-semibold tabular-nums text-bull-bright">
              ≈ +¥{fmtMoney(row.extra.monthlyInterestEst)}/月
            </span>
            <span className="font-mono text-[0.7857rem] text-text-dim">
              年化 {(row.extra.annualYield * 100).toFixed(2)}%
            </span>
          </div>
        </Tooltip>
      ) : (
        <>
          <span className={`font-mono text-[0.9643rem] font-semibold tabular-nums ${priceColor(row.pnl)}`}>
            {row.pnl != null ? (row.pnl >= 0 ? '+' : '') + fmtMoney(row.pnl) : '--'}
          </span>
          <span className={`font-mono text-[0.7857rem] opacity-85 ${priceColor(row.pnlPct)}`}>
            {row.pnlPct != null ? fmtPct(row.pnlPct) : ''}
          </span>
          {row.type === 'A' && Math.abs(carry || 0) > 0.5 && (() => {
            const total = (row.pnl || 0) + carry
            return (
              <Tooltip content="全周期真实盈亏 = 当前持仓浮动 + 历史已实现(清仓段/部分卖出)。清仓后又买回的票, 行内浮动只算这手, 这里把历史亏赚补回来。">
                <div className={`font-mono text-[0.7857rem] ${priceColor(total)} opacity-90 cursor-help`}>
                  真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                </div>
              </Tooltip>
            )
          })()}
          {row.type !== 'A' && row.type !== 'M' && Math.abs((row._raw?.realized_pnl || 0) - (row._raw?.cycle_realized || 0)) > 0.5 && (() => {
            const total = (row.pnl || 0) + (row._raw.realized_pnl || 0) - (row._raw.cycle_realized || 0)
            return (
              <Tooltip content="全周期真实盈亏 = 当前浮动 + 已实现(卖出/赎回的配对盈亏 + 利息分红)。卖掉部分的亏赚不在浮动里, 这里补齐——卖亏了再低位买回时, 浮动转正但真实口径仍记着那笔亏。">
                <div className={`font-mono text-[0.7857rem] ${priceColor(total)} opacity-90 cursor-help`}>
                  真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                </div>
              </Tooltip>
            )
          })()}
        </>
      )}
    </div>
    <div className="text-right">
      {row.type === 'M' && row.extra?.dailyInterestEst ? (
        <Tooltip content={
          <div>
            <div className="text-text-bright font-semibold mb-1">日息估算</div>
            <div>当前余额 × 年化 / 365</div>
            <div className="mt-1 text-text-dim text-[0.8214rem]">货币基金每日结息</div>
          </div>
        }>
          <div className="inline-flex flex-col items-end cursor-help">
            <span className="font-mono text-[0.9643rem] font-semibold tabular-nums text-bull-bright">
              +¥{row.extra.dailyInterestEst.toFixed(2)}
            </span>
            {row.today != null && (
              <span className="font-mono text-[0.7857rem] opacity-85 text-bull-bright">
                {row.today === 0 ? '0.00%' : (row.today > 0 ? '+' : '') + row.today.toFixed(2) + '%'}
              </span>
            )}
          </div>
        </Tooltip>
      ) : (
        <TodayPulse change={row.today} mv={row.mv} stale={row.type === 'A' && row._raw?.today_valid === false} />
      )}
    </div>
    <div className="text-right licai-md-only">
      {/* 期货(type=T)按保证金口径计入占比, 与 totalMv(含保证金折扣)及环形图口径一致; 其余用名义市值 */}
      <WeightBar weight={(row.type === 'T' && row.extra?.marginValue != null ? row.extra.marginValue : row.mv) / (totalMv || 1)} color={TYPE_COLOR[row.type]} />
    </div>
    <div className="relative pl-1 md:pl-2">
      {/* TypeMiniInfo: 桌面始终可见 (不再 hover 隐藏); 移动隐藏 */}
      <div className="hidden md:block">
        <TypeMiniInfo row={row} />
      </div>
      {/* RowActions: 桌面 hover 显示, 绝对覆盖右半. 容器 pointer-events-none 让事件
          穿透到 TypeMiniInfo (反推 tooltip 之类), 子元素重置 auto 接收点击.
          hover 时给个 surface-2 渐变遮罩, 与 row hover 背景色一致, 视觉自然. */}
      <div className="md:absolute md:inset-y-0 md:right-0 flex items-center md:pr-3 md:pointer-events-none [&>div]:pointer-events-auto transition-opacity"
        style={{
          background: hovered
            ? 'linear-gradient(to right, transparent 0%, var(--color-surface-2) 18%, var(--color-surface-2) 100%)'
            : 'transparent',
          transition: 'background .18s',
        }}>
        <RowActions row={row} visible={hovered} hasThesis={hasThesis} {...actions} />
      </div>
    </div>
  </div>
  )
}
