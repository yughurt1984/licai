import StockKlineModal from '../StockKlineModal'
import { QualityModal } from './QualityModal'
import { ThesisModal } from './ThesisModal'
import { AssetActionsModal } from './actions'
import { AddAShareForm } from './forms/AddAShareForm'
import { AddFundForm } from './forms/AddFundForm'
import { AddFuturesForm } from './forms/AddFuturesForm'
import { AssetEntryMenu } from './forms/AssetEntryMenu'
import { FuturesLotRow } from './forms/FuturesLotRow'
import { EditAssetRow } from './forms/EditAssetRow'
import { AddLotRow, CashAdjustRow, ReduceLotRow } from './forms/lots'

// 持仓页的全部弹窗与内联表单, 一处收口。开关状态来自 usePortfolioModals 的
// open/show/hide, 这里只负责"开着的那个渲染成什么"。
//
// 放在主组件里的时候这段是 75 行、11 个各带 onDone/onCancel 的条件块, 夹在
// 工具栏和列表之间 —— 读主组件时它是纯噪音, 而改弹窗时又要在 800 行里翻。
export function PortfolioModals({
  open, show, hide, brokers,
  onAdd,                 // A股 新增完成: 通知外层刷新 holdings
  onAssetsChanged,       // 资产类改动: 重载 /api/assets
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
      {/* 基金: ETF/LOF/REITs/货基(场内) 或 场外开放式; 按小类 trade 决定账本 */}
      {addCls === 'fund' && (
        <AddFundForm sub={addSub || undefined}
          brokers={brokers}
          onDone={() => { hide('add'); onAssetsChanged() }}
          onCancel={() => hide('add')} />
      )}
      {/* 衍生品(期货/期权): futures/options/warrant 中类决定 fut/opt, 一个入口覆盖 */}
      {addCls === 'derivatives' && (
        <AddFuturesForm sub={addSub || undefined}
          brokers={brokers}
          onDone={() => { hide('add'); onAdd?.() }} onCancel={() => hide('add')} />
      )}
      {/* 其余大类(理财/现金/加密/机器人/商品/外汇/保险…): 表单待单独开发 */}
      {addCls && addCls !== 'menu' && !['stock', 'fund', 'derivatives'].includes(addCls) && (
        <div className="px-6 py-4 bg-surface-2/50 border-b border-border text-[12.5px] text-text-muted">
          「{addCls}」添加表单待单独开发。
        </div>
      )}

      {open.edit && (
        <EditAssetRow asset={open.edit}
          brokers={brokers}
          onDone={() => { hide('edit'); onAssetsChanged() }}
          onCancel={() => hide('edit')} />
      )}

      {open.addLot && (
        <AddLotRow asset={open.addLot}
          brokers={brokers}
          onDone={() => { hide('addLot'); onRealizedChanged() }}
          onCancel={() => hide('addLot')} />
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

      {open.reduce && (
        <ReduceLotRow asset={open.reduce}
          onDone={() => { hide('reduce'); onRealizedChanged() }}
          onCancel={() => hide('reduce')} />
      )}

      {open.cashAdjust && (
        <CashAdjustRow asset={open.cashAdjust}
          onDone={() => { hide('cashAdjust'); onAssetsChanged() }}
          onCancel={() => hide('cashAdjust')} />
      )}

      {open.actions && (
        <AssetActionsModal asset={open.actions}
          onClose={() => hide('actions')}
          onChanged={() => onRealizedChanged()} />
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
