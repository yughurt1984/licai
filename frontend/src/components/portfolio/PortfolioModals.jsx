import StockKlineModal from '../StockKlineModal'
import { QualityModal } from './QualityModal'
import { ThesisModal } from './ThesisModal'
import { AddAShareForm } from './forms/AddAShareForm'
import { AddFundForm } from './forms/AddFundForm'
import { AddFuturesForm } from './forms/AddFuturesForm'
import { AddFixIncomeForm } from './forms/AddFixIncomeForm'
import { AssetEntryMenu } from './forms/AssetEntryMenu'
import { FuturesLotRow } from './forms/FuturesLotRow'

// 持仓页的全部弹窗与内联表单, 一处收口。开关状态来自 usePortfolioModals 的
// open/show/hide, 这里只负责"开着的那个渲染成什么"。
//
// 放在主组件里的时候这段是 75 行、11 个各带 onDone/onCancel 的条件块, 夹在
// 工具栏和列表之间 —— 读主组件时它是纯噪音, 而改弹窗时又要在 800 行里翻。
export function PortfolioModals({
  open, show, hide, brokers,
  onAdd,                 // 新增完成: 通知外层刷新 holdings(固收已并入持仓)
  onRealizedChanged,     // 影响已实现的改动(加减仓/流水)
  onThesisSaved,         // 买入逻辑存好: 重拉有逻辑的代码集合
}) {
  const addTarget = open.add
  // addTarget 编码: 'menu' = 分类菜单; '<大类>::<小类>' = 菜单级联选中小类后直达;
  // 仅 '<大类>' = 快速添加(按筛选桶直达, 表单内再选中类/币种)。大类即 assets_classify.assets_class。
  const [addCls, addSub] = String(addTarget || '').split('::')

  return (
    <>
      {addTarget === 'menu' && (
        <AssetEntryMenu
          onPick={(cls, sub) => show('add', `${cls}::${sub}`)}
          onCancel={() => hide('add')} />
      )}

      {/* 股票: 口径同股票(份额/单价/券商), 走 holdings */}
      {addCls && addCls !== 'menu' && addCls === 'stock' && (
        <AddAShareForm sub={addSub || undefined}
          brokers={brokers}
          onDone={() => { hide('add'); onAdd?.() }} onCancel={() => hide('add')} />
      )}
      {/* 基金: ETF/LOF/REITs/货基(场内) 或 场外开放式; 全部走持仓账本 holdings(POST /api/portfolio) */}
      {addCls === 'fund' && (
        <AddFundForm sub={addSub || undefined}
          brokers={brokers}
          onDone={() => { hide('add'); onAdd?.() }}
          onCancel={() => hide('add')} />
      )}
      {/* 衍生品(期货/期权): futures/options/warrant 中类决定 fut/opt, 一个入口覆盖 */}
      {addCls === 'derivatives' && (
        <AddFuturesForm sub={addSub || undefined}
          brokers={brokers}
          onDone={() => { hide('add'); onAdd?.() }} onCancel={() => hide('add')} />
      )}
      {/* 固定收益(理财/债券全谱系): 已并入持仓账本 —— 中类从 assets_classify fix-income
          大类自动同步, nav(理财净值化)/quote(债券) 双模式统一 POST /api/portfolio */}
      {addCls === 'fix-income' && (
        <AddFixIncomeForm sub={addSub || undefined}
          brokers={brokers}
          onDone={() => { hide('add'); onAdd?.() }}
          onCancel={() => hide('add')} />
      )}
      {/* 其余大类(现金/加密/机器人/商品/外汇/保险…): 表单待单独开发 */}
      {addCls && addCls !== 'menu' && !['stock', 'fund', 'derivatives', 'fix-income'].includes(addCls) && (
        <div className="px-6 py-4 bg-surface-2/50 border-b border-border text-[0.8929rem] text-text-muted">
          「{addCls}」添加表单待单独开发。
        </div>
      )}


      {/* 期货/期权: 加仓(同向 OPEN) / 平仓(CLOSE), 走持仓流水端点 */}
      {open.futLot && (
        <FuturesLotRow holding={open.futLot} mode="add"
          onDone={() => { hide('futLot'); onAdd?.(); onRealizedChanged() }}
          onCancel={() => hide('futLot')} />
      )}
      {open.futClose && (
        <FuturesLotRow holding={open.futClose} mode="close"
          onDone={() => { hide('futClose'); onAdd?.(); onRealizedChanged() }}
          onCancel={() => hide('futClose')} />
      )}

      {open.kline && (
        <StockKlineModal holding={open.kline} onClose={() => hide('kline')} />
      )}
      {open.thesis && (
        <ThesisModal row={open.thesis} onClose={() => hide('thesis')}
          onSaved={() => { onThesisSaved(); hide('thesis') }} />
      )}
      {open.quality && (
        <QualityModal row={open.quality} onClose={() => hide('quality')} />
      )}
    </>
  )
}
