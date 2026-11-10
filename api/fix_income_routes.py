"""Fixed income (fix-income 大类: 银行理财/债券) REST endpoints.

固定收益账本 (valuation_type=yield): 本金 + 年化 + 期限模型, 与 external_assets 的
WEALTH/CASH 分支同一套 principal-based FIFO 记账 (compute_external_state 复用),
但有固定收益特有的期限/到期语义: 估值走单利计提, 支持到期兑付(mature)。
分类归口 assets_classify 大类 fix-income (理财 bank_wealth / 债券 bond)。
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    list_fix_income_assets, get_fix_income_asset, add_fix_income_asset,
    update_fix_income_asset, delete_fix_income_asset, recompute_fix_income_asset,
    list_fix_income_actions, add_fix_income_action, update_fix_income_action,
    delete_fix_income_action, get_fix_income_action, get_assets_classify_row,
)
from services.external_ledger import compute_external_state

router = APIRouter(prefix="/api/fix-income", tags=["fix-income"])


def _today() -> date:
    return date.today()


def _parse_date(s) -> date | None:
    if not s:
        return None
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


class FixIncomeCreate(BaseModel):
    """新建固定收益资产 (理财/债券)。principal 即首笔买入本金, 会落一条种子 BUY 流水。"""
    code: str
    name: str
    principal: float = 0                       # 首笔买入本金 (CNY)
    sub_assets_type: Optional[str] = None      # → assets_classify (fix-income 大类下)
    platform: Optional[str] = ""               # 发行/购买平台
    currency: Optional[str] = "CNY"
    manual_value: Optional[float] = None       # 手动市值 (App 余额; 优先于年化估算)
    annual_yield_rate: Optional[float] = None  # 年化收益率/票面利率 (小数, 0.025 = 2.5%)
    rate_type: Optional[str] = "fixed"         # fixed | floating
    value_date: Optional[str] = None           # 起息日 YYYY-MM-DD (计息起点, 缺省今天)
    maturity_date: Optional[str] = None        # 到期日 (可空 = 开放式)
    term_days: Optional[int] = None            # 期限(天); 与到期日二填一互相推算
    interest_type: Optional[str] = "bullet"    # bullet | monthly | quarterly | semiannual | annual
    risk_level: Optional[str] = None           # R1~R5
    note: Optional[str] = ""
    trade_date: Optional[str] = None           # 种子 BUY 流水日期 (缺省 = value_date 或今天)


class FixIncomeUpdate(BaseModel):
    """编辑资产行。本金类字段 (principal) 由流水重算, 不在这里改。"""
    code: Optional[str] = None
    name: Optional[str] = None
    platform: Optional[str] = None
    currency: Optional[str] = None
    manual_value: Optional[float] = None
    annual_yield_rate: Optional[float] = None
    rate_type: Optional[str] = None
    value_date: Optional[str] = None
    maturity_date: Optional[str] = None
    term_days: Optional[int] = None
    interest_type: Optional[str] = None
    risk_level: Optional[str] = None
    status: Optional[str] = None               # holding | matured | closed
    note: Optional[str] = None


class FixIncomeLotAdd(BaseModel):
    """加仓: 本金 + 本笔起息日 + 本笔年化 (可选, 按本金加权混合进资产行)。"""
    principal: float
    fee: Optional[float] = None                # 手续费 ¥ (含在 principal 内, 单存便于流水展示)
    lot_start_date: Optional[str] = None       # 本笔起息日 (default: 今天)
    lot_yield_rate: Optional[float] = None     # 本笔年化 (default: 沿用资产行)
    note: Optional[str] = ""


class FixIncomeRedeem(BaseModel):
    """赎回/卖出: 本金到账金额; interest_part 可选拆出利息部分 (进已实现)。"""
    amount: float
    interest_part: Optional[float] = None
    trade_date: Optional[str] = None
    note: Optional[str] = ""


class FixIncomeInterest(BaseModel):
    """利息到账 (按月/按季付息类产品每期到账一笔)。"""
    amount: float
    trade_date: Optional[str] = None
    note: Optional[str] = ""


class FixIncomeMature(BaseModel):
    """到期兑付: 本金返还 + 最后一笔利息 (interest_part 缺省按 单利计提 自动算)。"""
    trade_date: Optional[str] = None
    interest_part: Optional[float] = None      # 显式指定利息; 缺省 = principal × rate × days/365
    note: Optional[str] = ""


class ActionPatch(BaseModel):
    """流水编辑 (金额/利息拆分/费率/日期/状态)。改完重算本金。"""
    amount: Optional[float] = None
    interest_part: Optional[float] = None
    fee: Optional[float] = None
    yield_rate: Optional[float] = None
    trade_date: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None


def _accrued_estimate(principal: float, rate: float | None, value_date: str | None,
                      fallback_start: str | None) -> tuple[float, int, str | None]:
    """单利计提: principal × (1 + rate × days/365)。返回 (估算值, 持有天数, 计息起点)。"""
    start = _parse_date(value_date) or _parse_date(fallback_start) or _today()
    days = max(0, (_today() - start).days)
    if principal <= 0:
        return 0.0, days, start.isoformat()
    if rate is None:
        return round(principal, 2), days, start.isoformat()
    return round(principal * (1 + float(rate) * days / 365), 2), days, start.isoformat()


def _enrich(asset: dict, actions: list[dict]) -> dict:
    """估值 enrich: 本金账本状态 + 固定收益计提。只算数, 不联网。

    流水由调用方(async 端点)取好传入 — compute_external_state 是纯内存计算。
    """
    out = {**asset, "principal": round(float(asset.get("principal") or 0), 2)}
    state = compute_external_state(actions, "WEALTH")
    principal = float(state.get("cost_amount") or 0)
    out["principal"] = round(principal, 2)
    out["ledger"] = {
        "realized_pnl": state.get("realized_pnl"),
        "income_realized": state.get("income_realized"),
        "lots": state.get("lots"),
        "total_acquired_cost": state.get("total_acquired_cost"),
        "total_release_proceeds": state.get("total_release_proceeds"),
    }

    rate = asset.get("annual_yield_rate")
    rate = float(rate) if rate is not None else None
    est, days_held, start_iso = _accrued_estimate(
        principal, rate, asset.get("value_date"), str(asset.get("created_at") or "")[:10])

    status = asset.get("status") or "holding"
    if principal <= 0:
        # 本金已全部赎回/兑付: 仓位已清, 利息在 realized_pnl 里 — 市值归零, 不能把
        # 残留的 manual_value 当市值(会变成纯利润)。
        current = 0.0
        source = "closed"
    elif asset.get("manual_value") is not None:
        current = round(float(asset["manual_value"]), 2)
        source = "manual"
    elif rate is not None:
        current = est
        source = "yield"
    else:
        current = round(principal, 2)
        source = None

    # 期限/到期: maturity_date 与 term_days 互推 (有到期日算剩余天数; 有期限算预计到期本息)
    maturity = _parse_date(asset.get("maturity_date"))
    term_days = asset.get("term_days")
    days_to_maturity = None
    if maturity:
        days_to_maturity = max(0, (maturity - _today()).days)
    elif term_days:
        days_to_maturity = max(0, int(term_days) - days_held)
    expected_maturity_value = None
    if rate is not None and term_days:
        expected_maturity_value = round(principal * (1 + rate * int(term_days) / 365), 2)

    out["current_value"] = current
    out["accrued_interest"] = round(current - principal, 2) if current is not None else None
    out["pnl"] = round(current - principal, 2) if current is not None else None
    out["pnl_pct"] = round((current - principal) / principal * 100, 2) if current is not None and principal > 0 else None
    out["quote"] = {
        "annual_yield_rate": rate,
        "rate_type": asset.get("rate_type") or "fixed",
        "value_date": asset.get("value_date") or start_iso,
        "days_held": days_held,
        "days_to_maturity": days_to_maturity,
        "maturity_date": asset.get("maturity_date"),
        "term_days": term_days,
        "interest_type": asset.get("interest_type") or "bullet",
        "expected_maturity_value": expected_maturity_value,
        "value_source": source,   # 'manual' | 'yield' | 'closed' | None
        "status": status,
    }
    return out


@router.get("")
async def list_assets(include_nonholding: bool = False):
    """固定收益资产列表 + 估值 + 按中类汇总。

    默认只回在持(status=holding); ?include_nonholding=true 带上已到期/已结清。
    """
    import asyncio

    rows = await list_fix_income_assets(include_nonholding=include_nonholding)
    async def _enrich_one(a):
        actions = await list_fix_income_actions(a["id"])
        return _enrich(a, actions)
    enriched = await asyncio.gather(*[_enrich_one(a) for a in rows])

    total_principal = sum(float(a.get("principal") or 0) for a in enriched)
    total_value = sum((a.get("current_value") or 0) for a in enriched)
    total_accrued = sum((a.get("accrued_interest") or 0) for a in enriched)
    total_realized = sum(float((a.get("ledger") or {}).get("realized_pnl") or 0) for a in enriched)

    by_main: dict[str, dict] = {}
    for a in enriched:
        m = a.get("main_assets_type") or a.get("sub_assets_type") or "other"
        if m not in by_main:
            by_main[m] = {"principal": 0.0, "value": 0.0, "accrued": 0.0, "count": 0}
        by_main[m]["principal"] += float(a.get("principal") or 0)
        by_main[m]["value"] += a.get("current_value") or 0
        by_main[m]["accrued"] += a.get("accrued_interest") or 0
        by_main[m]["count"] += 1
    for v in by_main.values():
        for k in ("principal", "value", "accrued"):
            v[k] = round(v[k], 2)

    return {
        "assets": enriched,
        "summary": {
            "total_principal": round(total_principal, 2),
            "total_value": round(total_value, 2),
            "total_accrued": round(total_accrued, 2),
            "total_realized": round(total_realized, 2),
            "by_main": by_main,
        },
    }


async def _validate_sub(sub: str | None) -> dict | None:
    """小类校验: 传了小类就必须是 fix-income 大类下的 yield 型 (quote/nav 走持仓账本)。

    没传小类的调用方 (AI 记账手填流程) 不受校验影响 — 与 assets_routes.create_asset 同一约定。
    """
    if not sub:
        return None
    cls = await get_assets_classify_row(sub)
    if cls is None:
        raise HTTPException(400, f"资产小类「{sub}」不在分类目录(assets_classify)中")
    vt = (cls.get("valuation_type") or "").strip().lower()
    if vt in ("quote", "nav"):
        raise HTTPException(
            400, f"「{cls.get('sub_assets_type_name') or sub}」估值类型为 {vt}，"
                 f"应记入持仓账本(holdings)，请走对应录入入口")
    if (cls.get("assets_class") or "").strip().lower() != "fix-income":
        raise HTTPException(
            400, f"「{cls.get('sub_assets_type_name') or sub}」不属于固定收益大类(fix-income)，"
                 f"请走对应资产类型的录入入口")
    return cls


def _derive_term_dates(value_date: str | None, maturity_date: str | None,
                       term_days: int | None) -> tuple[str | None, int | None]:
    """期限与到期日互推: 有起息日+期限 → 推到期日; 有起息日+到期日 → 推期限。"""
    vd = _parse_date(value_date)
    md = _parse_date(maturity_date)
    if vd and md and (term_days is None or term_days <= 0):
        term_days = (md - vd).days
    elif vd and term_days and (term_days > 0) and md is None:
        from datetime import timedelta
        md = vd + timedelta(days=int(term_days))
    return (md.isoformat() if md else None, int(term_days) if term_days else None)


@router.post("")
async def create_asset(data: FixIncomeCreate):
    """新建固定收益资产 + 种子 BUY 流水。小类必须 fix-income 大类下且 valuation_type=yield。"""
    cls = await _validate_sub(data.sub_assets_type)
    principal = float(data.principal or 0)
    if principal < 0:
        raise HTTPException(400, "本金不能为负数")

    code = (data.code or "").strip() or (data.name or "").strip()
    if not code or not (data.name or "").strip():
        raise HTTPException(400, "名称必填")

    value_date = (data.value_date or _today().isoformat())
    maturity_date, term_days = _derive_term_dates(value_date, data.maturity_date, data.term_days)

    aid = await add_fix_income_asset(
        code=code,
        name=(data.name or "").strip(),
        principal=0,   # 本金由流水重算 (种子 BUY 落库后 recompute), 不直接信任入参
        sub_assets_type=data.sub_assets_type,
        main_assets_type=(cls or {}).get("main_assets_type"),
        platform=data.platform or "",
        currency=data.currency or "CNY",
        manual_value=data.manual_value,
        annual_yield_rate=data.annual_yield_rate,
        rate_type=data.rate_type or "fixed",
        value_date=value_date,
        maturity_date=maturity_date,
        term_days=term_days,
        interest_type=data.interest_type or "bullet",
        risk_level=data.risk_level,
        note=data.note or "",
    )

    if principal > 0:
        await add_fix_income_action(
            aid, "BUY", amount=principal,
            yield_rate=data.annual_yield_rate,
            trade_date=data.trade_date or value_date,
            note="initial",
        )
        await recompute_fix_income_asset(aid)

    return {"message": "added", "id": aid}


@router.put("/{asset_id}")
async def update_asset(asset_id: int, data: FixIncomeUpdate):
    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")

    # exclude_unset: 只取客户端显式传的字段 —— 显式传 null 可以清空可空字段
    # (如 manual_value 清掉恢复年化估算), 未传的字段不动。
    upd: dict = data.model_dump(exclude_unset=True)
    # 期限/到期日互推 (以编辑后的组合为准; 缺一头就补另一头)
    if "maturity_date" in upd or "term_days" in upd:
        vd = upd.get("value_date", asset.get("value_date"))
        md, td = _derive_term_dates(vd, upd.get("maturity_date", asset.get("maturity_date")),
                                    upd.get("term_days", asset.get("term_days")))
        upd["maturity_date"] = md
        upd["term_days"] = td
    if upd:
        await update_fix_income_asset(asset_id, **upd)
    return {"message": "updated", "updated": list(upd.keys())}


@router.delete("/{asset_id}")
async def delete_asset(asset_id: int):
    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    await delete_fix_income_asset(asset_id)
    return {"message": "deleted", "id": asset_id}


@router.post("/{asset_id}/add-lot")
async def add_lot(asset_id: int, data: FixIncomeLotAdd):
    """加仓: 写 ADD 流水 + 本金重算 + 本笔年化按本金加权混合进资产行 (同 WEALTH 加仓语义)。"""
    from datetime import date as _date

    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if (asset.get("status") or "holding") != "holding":
        raise HTTPException(400, "已到期/已结清的仓位不能加仓, 请先恢复为在持")
    if data.principal is None or data.principal <= 0:
        raise HTTPException(400, "principal 必须为正数")

    old_principal = float(asset.get("principal") or 0)
    old_rate = asset.get("annual_yield_rate")
    lot_rate = float(data.lot_yield_rate) if data.lot_yield_rate is not None else None

    await add_fix_income_action(
        asset_id, "ADD", amount=float(data.principal),
        fee=float(data.fee) if data.fee else None,
        yield_rate=lot_rate,
        trade_date=data.lot_start_date or _date.today().isoformat(),
        note=data.note or "add-lot",
    )
    state = await recompute_fix_income_asset(asset_id)
    new_principal = float(state.get("principal") or 0)

    payload: dict = {"principal": round(new_principal, 2)}
    if lot_rate is not None and new_principal > 0:
        # 加权混合: (旧本金 × 旧年化 + 本笔本金 × 本笔年化) / 新本金
        base = float(old_rate) if old_rate is not None else lot_rate
        blended = (old_principal * base + float(data.principal) * lot_rate) / new_principal
        payload["annual_yield_rate"] = round(blended, 5)
        await update_fix_income_asset(asset_id, annual_yield_rate=payload["annual_yield_rate"])
    return {"message": "lot added", "new_state": payload}


@router.post("/{asset_id}/redeem")
async def redeem(asset_id: int, data: FixIncomeRedeem):
    """赎回: 本金到账金额; interest_part 拆出利息部分(进已实现), 本金清零自动结清。"""
    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "赎回金额必须为正数")

    await add_fix_income_action(
        asset_id, "REDEEM", amount=float(data.amount),
        interest_part=float(data.interest_part) if data.interest_part is not None else None,
        trade_date=data.trade_date or _today().isoformat(),
        note=data.note or "",
    )
    state = await recompute_fix_income_asset(asset_id)
    return {
        "message": "redeemed",
        "principal": state.get("principal"),
        "status": state.get("status"),
        "realized_pnl_total": state.get("realized_pnl"),
    }


@router.post("/{asset_id}/interest")
async def record_interest(asset_id: int, data: FixIncomeInterest):
    """利息到账 (按月/按季付息类产品每期一笔)。不动本金, 直接进已实现。"""
    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if data.amount is None or data.amount <= 0:
        raise HTTPException(400, "利息金额必须为正数")

    aid = await add_fix_income_action(
        asset_id, "INTEREST", amount=float(data.amount),
        trade_date=data.trade_date or _today().isoformat(),
        note=data.note or "",
    )
    state = await recompute_fix_income_asset(asset_id)
    return {"message": "interest recorded", "action_id": aid, "realized_pnl_total": state.get("realized_pnl")}


@router.post("/{asset_id}/mature")
async def mature(asset_id: int, data: FixIncomeMature):
    """到期兑付: 本金返还 + 最后一笔利息, status → matured。

    利息缺省按 单利计提近似 (principal × rate × 持有天数/365) — 分批加仓的混合仓位
    这只是估算, 可显式传 interest_part 覆盖, 或事后在流水里改。
    """
    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if (asset.get("status") or "holding") != "holding":
        raise HTTPException(400, "该仓位已到期/结清")

    state0 = compute_external_state(await list_fix_income_actions(asset_id), "WEALTH")
    principal = float(state0.get("cost_amount") or 0)
    if principal <= 0:
        raise HTTPException(400, "本金已清零, 无需兑付")

    rate = asset.get("annual_yield_rate")
    interest_part = data.interest_part
    if interest_part is None and rate is not None:
        est, _, _ = _accrued_estimate(principal, float(rate), asset.get("value_date"),
                                      str(asset.get("created_at") or "")[:10])
        interest_part = round(max(0.0, est - principal), 2)

    await add_fix_income_action(
        asset_id, "REDEEM", amount=round(principal + (interest_part or 0), 2),
        interest_part=interest_part,
        trade_date=data.trade_date or _today().isoformat(),
        note=data.note or "mature",
    )
    state = await recompute_fix_income_asset(asset_id)
    # 到期兑付即使有计提误差导致本金残留(应≈0), 也按 matured 定格退出在持
    await update_fix_income_asset(asset_id, status="matured", manual_value=None)
    return {
        "message": "matured",
        "principal_released": round(principal, 2),
        "interest_part": interest_part,
        "realized_pnl_total": state.get("realized_pnl"),
    }


@router.get("/{asset_id}/actions")
async def list_actions(asset_id: int):
    """流水 + 账本状态。返回形状 {actions, state} 与 /api/assets/{id}/actions 同构,
    前端流水弹窗共用。"""
    asset = await get_fix_income_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    actions = await list_fix_income_actions(asset_id)
    state = compute_external_state(actions, "WEALTH")
    state["principal"] = state.get("cost_amount")
    return {"actions": actions, "state": state}


@router.patch("/{asset_id}/actions/{action_id}")
async def patch_action(asset_id: int, action_id: int, data: ActionPatch):
    """改流水 (金额/利息拆分/费率/日期/状态), 改完重算本金。"""
    target = await get_fix_income_action(asset_id, action_id)
    if not target:
        raise HTTPException(404, "action not found")
    upd = {k: v for k, v in {
        "amount": data.amount, "interest_part": data.interest_part, "fee": data.fee,
        "yield_rate": data.yield_rate, "trade_date": data.trade_date,
        "status": data.status, "note": data.note,
    }.items() if v is not None}
    if upd:
        await update_fix_income_action(action_id, **upd)
    state = await recompute_fix_income_asset(asset_id)
    return {"message": "updated", "principal": state.get("principal")}


@router.delete("/{asset_id}/actions/{action_id}")
async def remove_action(asset_id: int, action_id: int):
    """删流水 (后续本金按剩余流水重算)。"""
    target = await get_fix_income_action(asset_id, action_id)
    if not target:
        raise HTTPException(404, "action not found")
    await delete_fix_income_action(action_id)
    state = await recompute_fix_income_asset(asset_id)
    return {"message": "deleted", "principal": state.get("principal")}
