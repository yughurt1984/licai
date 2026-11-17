"""Compute current position state from chronological buy/sell actions.

加权平均成本法(2026-11 定稿, 替代 FIFO+综合成本摊薄):
- BUY / ADD / OPEN → 加权进成本: 累计买入(含买入费)金额 ÷ 累计买入股数
- SELL / REDUCE / CLOSE → 立即结算已实现 = (卖价−当时均价)×股数−卖出费, 生成事件;
  **成本价不动**(总成本与份额同减, 均价不变)
- BONUS → 送股(价 0): 份额增加、金额不变, 均价被动摊低
- SPLIT → 拆股/合股(港美股): 比例记在 price(新股数/旧股数, >1 拆股、<1 合股), shares 记 0;
  份额×比例、总成本不动 → 均价自动 ÷比例
- DIVIDEND → 现金分红, 全额进已实现(不动成本, 用户定稿口径)
- 份额归零 → 成本基准清零, 重新建仓从新买价起算

港美股账本是原币, 另并行一套人民币口径: 每笔按自己的 fx_rate(成交日汇率, 没有就用当前汇率)折算,
已实现的汇兑损益单列(*_cny / realized_fx_pnl 字段)。A 股汇率恒为 1。

Derived quantities:
- shares        : current total shares
- cost_price    : 加权平均成本价(含买入费, 原币每单位; 卖出不改它)
- realized_pnl  : Σ 切换日后平仓事件 + 分红(原币口径; *_cny 为人民币)
- realized_events / realized_events_all : 每笔平仓的事件明细(前者按切换日过滤)
- weighted_days : capital-weighted holding days (for TVM calculations)
- lots          : 剩余买入批次 with (shares, price, trade_date)
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Iterable

from services.market_data import is_a_share

ACQUIRE = {"BUY", "ADD", "BONUS"}     # 买入 / 加仓 / 送股转增 (BONUS price=0 amount=0 只加 shares)
RELEASE = {"SELL", "REDUCE"}          # 卖出 / 减仓
INCOME  = {"DIVIDEND"}                # 现金分红 → realized_pnl (不动 lot, 不动 cost_price)
SPLIT   = "SPLIT"                     # 拆股/合股: 比例见 split_ratio

# A-share standard transaction fees
_COMMISSION_RATE = 0.00025  # 万2.5 (user's broker rate)
_COMMISSION_MIN = 5.0         # ¥5 per trade minimum
_STAMP_RATE = 0.0005         # 0.05% sell side only (since 2023-08)
_TRANSFER_RATE = 0.00001     # 过户费 万0.1, 沪深双向都收 (深市 2022-04-29 起也收, 此前仅沪市)
# 规费 (双向收, 沪深都有)
_EXCHANGE_HANDLE_RATE = 0.0000341  # 经手费 万0.341 (2025-07-01 起下调)
_REGULATORY_FEE_RATE  = 0.00002    # 证管费 万0.2 (证监会)


def estimate_trade_fee(action_type: str, price: float, shares: float, stock_code: str = "",
                       commission_rate: float | None = None,
                       commission_min: float | None = None,
                       trade_date: str | None = None, broker: str | None = None,
                       fee_overrides: str | None = None) -> float:
    """Estimate A-share trading fees (commission + stamp + transfer + regulatory).

    Returns total fee in yuan. Used to adjust cost basis to match broker display.
    commission_rate / commission_min default to the 招商证券 constants when not passed.
    港美股按 trade_date 当时的规费(SEC/TAF 分段)和 broker 的收费方案算, 不传日期才用今天。
    """
    # 非买入/卖出 (DIVIDEND 分红 / BONUS 送股 等) 不收费
    if action_type not in ACQUIRE and action_type not in RELEASE:
        return 0.0
    mkt = _foreign_market(stock_code)
    if mkt:                                      # 港美股: 原币, 按券商收费方案(默认盈立)
        from services.market_fees import order_fee, schedules
        return order_fee(mkt, "sell" if action_type in RELEASE else "buy", price * shares, shares,
                         str(trade_date or "")[:10] or date.today().isoformat(),
                         broker=broker, sched=schedules(fee_overrides))
    if stock_code and not is_a_share(stock_code):
        return 0.0
    amount = price * shares
    if amount <= 0:
        return 0.0
    c_rate = _COMMISSION_RATE if commission_rate is None else commission_rate
    c_min = _COMMISSION_MIN if commission_min is None else commission_min
    commission = max(amount * c_rate, c_min)
    stamp = amount * _STAMP_RATE if action_type in RELEASE else 0.0
    # 过户费沪深双向都收 (函数开头已对非 A 股 return 0; stock_code 为空时默认按 A 股算)
    transfer = amount * _TRANSFER_RATE
    regulatory = amount * (_EXCHANGE_HANDLE_RATE + _REGULATORY_FEE_RATE)
    return commission + stamp + transfer + regulatory


def _foreign_market(code: str) -> str | None:
    """港/美股判定(标准码「代码.市场」: 00700.HK / SNDK.US; 兼容旧前缀形态)。"""
    c = (code or "").upper()
    if c.endswith(".HK") or c.startswith("HK."):
        return "HK"
    if c.endswith(".US") or c.startswith("US."):
        return "US"
    return None


def _order_key(a: dict) -> tuple:
    """粗分组键: 代码 + 日期 + 方向。**同一张委托的判定不在这儿** —— 组内还要按
    trade_fills.one_order_clusters(同一分钟 + 首尾 ≤4 跳板)再聚一次。

    为什么不能只按 代码+日期+方向+成交时刻(这是旧写法): 那样完全不看价格, 于是
    9-01 长江电力 148.62 与 148.95 同在 09:32 被算成一张委托, 只收了一次 5 元最低佣金 ——
    可它们差 33 个跳板, 一张委托的分笔成交吃不掉那么多档(用户确认是两次下单)。
    另外没记成交时刻的老流水会整天同方向并成一组, 少算最低佣金, 也是旧写法的已知代价。

    换成"粗分组 + 跳板聚簇"后, 全账本只有 6 组被拆开: 5 组是没记时刻的老行(价差
    15~86 跳板, 显然是多张单)、1 组是长电。零误拆, 总费用 1745.42 → 1795.42 元
    (差额全是 5 元最低佣金该收几次)。

    仍然修不了的方向: 真的分几次下单、而价格恰好落在同一分钟 4 跳板内, 照样会被合掉。
    那一半只有券商交割单里的**合同编号**能判。真实费用可以直接填 action 的 fee 字段覆盖估算。
    """
    t = (a.get("action_type") or "")
    side = "A" if t in ACQUIRE else ("R" if t in RELEASE else "?")
    return (a.get("stock_code") or "", str(a.get("trade_date") or "")[:10], side)


def allocate_trade_fees(actions: list[dict], commission_rate: float | None = None,
                        commission_min: float | None = None, fee_overrides: str | None = None) -> list[float]:
    """按「委托单」算佣金最低值, 再按成交额分摊回每一笔。返回与 actions 等长的费用列表。

    印花税/过户费/规费都是按比例收的, 逐笔算不会出错, 只有最低佣金需要合并。
    """
    c_rate = _COMMISSION_RATE if commission_rate is None else commission_rate
    c_min = _COMMISSION_MIN if commission_min is None else commission_min
    from services.trade_fills import one_order_clusters, tick_for_code
    groups: dict[tuple, list[int]] = {}
    for i, a in enumerate(actions):
        groups.setdefault(_order_key(a), []).append(i)

    out = [0.0] * len(actions)
    for key, coarse in groups.items():
        code = key[0]
        mkt = _foreign_market(code)
        if code and not mkt and not is_a_share(code):
            continue
        t0 = (actions[coarse[0]].get("action_type") or "")
        if t0 not in ACQUIRE and t0 not in RELEASE:
            continue
        # 粗分组里再按「同一分钟 + ≤4 跳板」切出真正的委托单(判据与复盘共用一份实现)
        rows = [actions[i] for i in coarse]
        clusters = one_order_clusters(rows, price_of=lambda a: a.get("price"),
                                      time_of=lambda a: a.get("trade_time"),
                                      tick=tick_for_code(code, float(rows[0].get("price") or 0) or None))
        if mkt:                                  # 港美股: 一张委托整体算(平台费/最低佣金/封顶按单), 再按成交额分摊
            from services.market_fees import order_fee, schedules
            sched = schedules(fee_overrides)
            for cl in clusters:
                idxs = [coarse[j] for j in cl]
                amts = [float(actions[i].get("price") or 0) * float(actions[i].get("shares") or 0) for i in idxs]
                total, sh = sum(amts), sum(float(actions[i].get("shares") or 0) for i in idxs)
                if total <= 0:
                    continue
                fee = order_fee(mkt, "sell" if t0 in RELEASE else "buy", total, sh, key[1],
                                broker=actions[idxs[0]].get("broker"), sched=sched)
                for i, amt in zip(idxs, amts):
                    out[i] = fee * (amt / total)
            continue
        for cl in clusters:
            idxs = [coarse[j] for j in cl]
            amts = [float(actions[i].get("price") or 0) * float(actions[i].get("shares") or 0)
                    for i in idxs]
            total = sum(amts)
            if total <= 0:
                continue
            commission = max(total * c_rate, c_min)     # ← 整张委托只收一次最低佣金
            stamp_rate = _STAMP_RATE if t0 in RELEASE else 0.0
            per_unit = stamp_rate + _TRANSFER_RATE + _EXCHANGE_HANDLE_RATE + _REGULATORY_FEE_RATE
            for i, amt in zip(idxs, amts):
                out[i] = commission * (amt / total) + amt * per_unit
    return out


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def split_ratio(a: dict) -> float:
    """SPLIT 行的拆合股比例(新股数/旧股数)。position_actions 记在 price(shares 为 0);
    场外账本(external_asset_actions)沿用 shares 记倍数、price 为空 —— 两种写法都认。"""
    try:
        r = float(a.get("price") or 0) or float(a.get("shares") or 0)
    except (TypeError, ValueError):
        return 0.0
    return r if r > 0 else 0.0


def _action_fx(a: dict, fx_now) -> float:
    """这笔流水折人民币的汇率: 有成交日汇率用它, 否则用当前汇率(A 股传进来的 fx_now 是 1)。"""
    try:
        v = float(a.get("fx_rate") or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else fx_now()


def compute_position_state(
    actions: Iterable[dict],
    today: date | None = None,
    stock_code: str = "",
    commission_rate: float | None = None,
    commission_min: float | None = None,
    fx_now: float | None = None,
    switch_date: str | None = None,
) -> dict:
    """加权平均成本法: 一次按时间顺序遍历流水, 成本=累计买入(含费)÷累计买入股数。

    每笔卖出/平仓立即结算为已实现事件((卖价−当时均价)×股数−卖出费), 全部事件在
    realized_events(切换日后)/realized_events_all(诊断) 里; 已实现 = Σ切换日后事件+分红。
    份额归零 → 成本基准清零重起。卖出不改成本价。

    Each action must have: action_type, price (float), shares (int), trade_date (str).
    Actions with missing trade_date are ordered by created_at fallback (today).

    If stock_code is provided, applies A-share trading fees (佣金/印花税/过户费) to the
    weighted-average cost so it matches what the brokerage app displays.

    fx_now: 港美股当前汇率(没记 fx_rate 的流水按它折人民币); 不传且用得到时现取 code_fx。
    switch_date: 核算切换日(YYYY-MM-DD), 该日(含)之后的平仓事件才计入已实现; None=不限。
    """
    if today is None:
        today = date.today()

    # ── 期货/期权方向语义(负价技巧) ──────────────────────────────
    # 加权平均成本法下同样成立: SHORT 全部价格取负 → 均价为负, 展示时取负还原;
    # realized = (−卖价 − (−均价))×手数 = (均价−卖价)×手数 = 空头真实盈亏, 无需翻转。
    direction = next((str(a.get("direction") or "").upper() for a in actions if a.get("direction")), "")
    if direction in ("LONG", "SHORT"):
        sign = -1.0 if direction == "SHORT" else 1.0
        remap = {"OPEN": "BUY", "CLOSE": "SELL"}
        actions = [dict(a,
                        price=(float(a.get("price") or 0) * sign),
                        action_type=remap.get(a.get("action_type"), a.get("action_type")))
                   for a in actions]

    def sort_key(a):
        return _parse_date(a.get("trade_date") or a.get("created_at"))

    sorted_actions = sorted(actions, key=sort_key)

    foreign = _foreign_market(stock_code or next((a.get("stock_code") for a in sorted_actions
                                                  if a.get("stock_code")), "")) is not None
    _fx_cur: list = [1.0 if not foreign else fx_now]

    def _now_fx() -> float:
        if _fx_cur[0] is None:                    # 懒取: 每笔都记了 fx_rate 就不用打网络
            from services.market_data import code_fx
            _fx_cur[0] = code_fx(stock_code or next(a.get("stock_code") for a in sorted_actions
                                                    if a.get("stock_code")))
        return _fx_cur[0]

    def _fx_of(a) -> float:
        return _action_fx(a, _now_fx) if foreign else 1.0

    # 估算费用先按「委托单」整体算一遍再分摊回每笔 —— 逐笔各取一次 5 元最低佣金
    # 会把同一张委托的分笔成交重复收费(实测这份流水多收 101.22 元)。
    _alloc = allocate_trade_fees(
        [dict(a, stock_code=a.get("stock_code") or stock_code) for a in sorted_actions],
        commission_rate, commission_min) if stock_code else []

    def _fee_of(a, t, price, shares, idx=None):
        # action.fee 非 NULL = 用户手填覆盖; 否则用调用方按每笔 broker 预算的 _auto_fee;
        # 都没有再用分摊后的估算。费用一律 CNY。
        if not stock_code:
            return 0.0
        override = a.get("fee")
        if override is not None:
            return float(override)
        if a.get("_auto_fee") is not None:
            return float(a["_auto_fee"])
        return _alloc[idx] if (idx is not None and idx < len(_alloc)) else 0.0

    # ── 加权平均成本法(2026-11 定稿, 替代 FIFO+综合成本摊薄) ──────────────
    #   · 成本价 = 累计买入(含买入费)金额 ÷ 累计买入股数 —— 只由买入决定, 卖出不改
    #   · 卖出/平仓: 已实现 = (卖价 − 当时均价)×股数 − 卖出费, 立即结算并生成事件
    #   · 加仓按加权更新; BONUS(送股, 价 0)摊低均价; SPLIT 份额×r → 均价自动 ÷r
    #   · 份额归零 → 成本基准清零(重新建仓从新买价起算, 无"持仓段"概念)
    #   · 分红(INCOME): 全额进已实现, 不碰成本(用户定稿口径, 与成本法无关)
    #   · 汇率: 账本按原币记, 每笔的 CNY 口径 = 原币 × 该笔汇率; 已实现的汇兑部分单列
    #     (basis 的隐含平均汇率 = basis_c/basis_n, 卖出汇率偏离它即是汇兑损益)
    #   · 切换日: trade_date >= realized_switch_date 的平仓事件才进 realized_pnl/
    #     落 realized_events(用户定稿: 历史不回溯); 分红全额计入, 不受切换日约束
    basis_n = basis_c = 0.0        # 累计买入金额(原币 / CNY), 含买入费
    basis_sh = 0.0                 # 累计买入股数(剩余持仓口径)
    buy_lots = []                  # 剩余买入批次 {shares, price, fx, date}: 天数加权与展示用
    all_events: list[dict] = []    # 每笔卖出/平仓的已实现事件(切换日过滤在汇总层做)
    income_realized = income_realized_c = 0.0   # 现金分红累计(税后, 原币/CNY)
    dividend_tax = 0.0             # 港美股分红预扣税累计(原币)
    fx_realized = 0.0              # 已实现里的汇兑部分(CNY)
    total_fees = 0.0               # 全周期手续费(CNY, 展示用)

    for _i, a in enumerate(sorted_actions):
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = float(a.get("shares") or 0)
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE and shares > 0:
            fx = _fx_of(a)
            f = _fee_of(a, t, price, shares, _i)
            basis_n += price * shares + (f / fx if fx else 0.0)
            basis_c += price * shares * fx + f
            basis_sh += shares
            buy_lots.append({"shares": shares, "price": price, "fx": fx, "date": ad})
            total_fees += f
        elif t in RELEASE and shares > 0:
            fx = _fx_of(a)
            if basis_sh <= 1e-9:
                continue                    # 无持仓的卖出(漏录买入): 不硬算, 不生成事件
            avg_n = basis_n / basis_sh
            avg_c = basis_c / basis_sh
            matched = min(shares, basis_sh)   # 超卖钳位(漏录买入的防御), 事件按实际配平的股数
            f = _fee_of(a, t, price, shares, _i)
            # 衍生品乘数: 账本按"每单位"记, 真实盈亏 ×乘数(流水每笔带历史乘数); 费用是整单绝对值, 不乘
            mult = float(a.get("contract_multiplier") or 0) or 1.0
            pnl_n = ((price - avg_n) * matched - (f / fx if fx else 0.0)) * mult
            pnl_c = ((price * fx - avg_c) * matched - f) * mult
            fx_realized += avg_n * matched * (fx - (avg_c / avg_n if avg_n else 1.0)) * mult
            basis_n -= avg_n * matched
            basis_c -= avg_c * matched
            basis_sh -= matched
            scale = basis_sh / (basis_sh + matched) if (basis_sh + matched) > 1e-9 else 0.0
            for lot in buy_lots:            # 天数加权批次按比例缩减(非配对, 只做权重)
                lot["shares"] *= scale
            buy_lots = [l for l in buy_lots if l["shares"] > 1e-9]
            total_fees += f
            all_events.append({
                "action_id": a.get("id"),
                "side": t,
                "direction": direction or None,
                "close_shares": matched,
                # 空头走负价技巧(price 已取负) → 还原真实成交价; 事件表存正数
                "close_price": (-price if direction == "SHORT" else price),
                "avg_cost": abs(avg_n),
                "realized_pnl": round(pnl_c, 2),
                "realized_pnl_native": round(pnl_n, 2),
                "fee": round(f, 2),
                "fx_rate": fx,
                "contract_multiplier": float(a.get("contract_multiplier") or 0) or None,
                "trade_date": ad.isoformat(),
            })
            if basis_sh <= 1e-9:            # 清仓: 成本基准清零(浮点尾数一并归零)
                basis_n = basis_c = 0.0
                basis_sh = 0.0
                buy_lots = []
        elif t == SPLIT:
            r = split_ratio(a)
            if r > 0 and basis_sh > 1e-9:
                # 拆股/合股: 股数×r, 总成本一分不动 → 均价自动 ÷r。合股零碎股券商折现金
                # (cash in lieu): 零头从最后一批买入扣掉, 它的成本并进剩余买入。
                basis_sh *= r
                for lot in buy_lots:
                    lot["shares"] *= r
                    lot["price"] /= r
                total = sum(l["shares"] for l in buy_lots)
                whole = total if _foreign_market(a.get("stock_code") or stock_code) == "US" else int(total + 1e-6)
                frac, moved = total - whole, 0.0
                for lot in reversed(buy_lots):
                    if frac <= 1e-9:
                        break
                    take = min(frac, lot["shares"])
                    lot["shares"] -= take
                    moved += take * lot["price"]
                    frac -= take
                buy_lots = [l for l in buy_lots if l["shares"] > 1e-9]
                if buy_lots and moved:
                    buy_lots[-1]["price"] += moved / buy_lots[-1]["shares"]
                basis_sh = whole
                if basis_sh <= 1e-9:
                    basis_n = basis_c = 0.0
                    basis_sh = 0.0
                    buy_lots = []
        elif t in INCOME:
            # 现金分红: 显式 amount, 或 price(每股股息) × shares(持股数) —— 都是税前。
            # 全额进已实现, 不碰成本(用户定稿口径), 不受切换日约束(与成本法无关的收益流)。
            amt = float(a.get("amount") or 0)
            if amt <= 0:
                amt = float(a.get("price") or 0) * float(a.get("shares") or 0)
            # 预扣税: 港美股的分红行用 fee 字段记税(手填为准); 没填按收费方案的默认税率估(美股 W-8BEN 10%, 港股 0)
            tax = 0.0
            mkt = _foreign_market(a.get("stock_code") or stock_code)
            if mkt:
                if a.get("fee") is not None:
                    tax = float(a["fee"])
                else:
                    from services.market_fees import dividend_tax_rate
                    tax = amt * dividend_tax_rate(mkt, a.get("broker"))
            dividend_tax += tax
            income_realized += amt - tax
            income_realized_c += (amt - tax) * _fx_of(a)

    # ── 汇总: 已实现 = Σ 切换日后的平仓事件 + 分红(全历史) ──────────────
    def _in_switch(e):
        return switch_date is None or (e.get("trade_date") or "") >= switch_date
    switched = [e for e in all_events if _in_switch(e)]
    realized_pnl = round(sum(e["realized_pnl_native"] for e in switched) + income_realized, 2)
    realized_pnl_cny = round(sum(e["realized_pnl"] for e in switched) + income_realized_c, 2)
    # carry 概念已死: 浮动 = 市值 − 成本×份额, 从不含已实现 → 顶栏总盈亏直接相加。
    # 兼容旧消费方: realized_carry = 已实现本身(切换日后事件 + 分红)。
    realized_carry = realized_pnl
    realized_carry_cny = realized_pnl_cny

    total_shares = basis_sh
    avg_n = basis_n / total_shares if total_shares > 1e-9 else 0.0
    avg_c = basis_c / total_shares if total_shares > 1e-9 else 0.0

    # Capital-weighted days on remaining buy lots (each lot has a concrete date)
    capital_days_sum = sum(
        l["shares"] * l["price"] * max(0, (today - l["date"]).days)
        for l in buy_lots
    )
    lot_amount = sum(l["shares"] * l["price"] for l in buy_lots)
    weighted_days = capital_days_sum / lot_amount if lot_amount > 0 else 0
    whole = int(round(total_shares))

    return {
        "shares": whole if abs(total_shares - whole) < 1e-6 else round(total_shares, 4),
        # SHORT(负价技巧): 成本价是负的每单位均价 → 取负还原为正的展示值
        "cost_price": round(-avg_n, 4) if direction == "SHORT" else round(avg_n, 4),
        "cost_price_raw": round(-avg_n, 4) if direction == "SHORT" else round(avg_n, 4),
        "fifo_cost_price": round(-avg_n, 4) if direction == "SHORT" else round(avg_n, 4),
        "total_fees": round(total_fees, 2),
        "weighted_days": int(round(weighted_days)),
        "realized_pnl": realized_pnl,                    # 原币口径(切换日后事件 + 分红)
        "realized_carry": realized_carry,                # 兼容键 = realized_pnl(浮动已不含已实现)
        "realized_events": switched,                     # 切换日后的平仓事件(落 realized_events 用)
        "realized_events_all": all_events,               # 全部事件(诊断/重放校验用, 不落表)
        "income_realized": round(income_realized, 2),   # 累计现金分红 (DIVIDEND, 全额进已实现)
        "dividend_tax": round(dividend_tax, 2),         # 港美股分红预扣税累计(原币; 手填 fee 才有)
        "cost_value_cny": round(abs(avg_c * total_shares), 2) if direction == "SHORT" else round(avg_c * total_shares, 2),
        "realized_pnl_cny": realized_pnl_cny,
        "realized_carry_cny": realized_carry_cny,
        "realized_fx_pnl": round(fx_realized, 2),       # 人民币: 已实现里本金的汇兑部分(A 股恒 0)
        "lots": [
            {"shares": round(l["shares"], 4) if l["shares"] % 1 else int(l["shares"]), "price": l["price"],
             "trade_date": l["date"].isoformat()}
            for l in buy_lots
        ],
    }


# ---------- 带费用/汇率解析的统一入口(异步, 读库) ----------
# 所有要「某只票现在的账」的地方都走 ledger_state: 每笔按自己的券商(没填用持仓券商)估费,
# 港美股收费方案吃 app_config.fee_schedules 的覆盖, 当前汇率懒取。各处自己拼参数容易漏一项
# (曾经 realized / trade-review / today-pnl 都没走自动估费, 港美股的费用回退成内置默认方案)。

async def _product_fee_of(code: str) -> dict | None:
    """thscode → 品种行(symbol_dict JOIN assets_classify), 含比例费率与每手乘数。

    品种 = 合约主体的字母前缀(CU2601 → CU / IO2601-C-4000 → IO); 查不到品种行 → None。
    费率口径: symbol_dict 品种行比例费率(品种级, 原在 assets_classify 小类级 —— 粒度错了已归位)
    (open_fee_rate × 名义金额, 0.0001 = 万一); 行里没配费率时由调用方回退券商档案。
    """
    try:
        import re as _re
        from database import get_futures_product
        body = (code or "").split(".")[0]
        m = _re.match(r"^[A-Za-z]+", body)
        if not m:
            return None
        return await get_futures_product(m.group(0).upper())
    except Exception:
        return None


async def broker_fee_resolver():
    """一次性读券商表, 返回 (name, market)->(rate,min) 解析器(找不到→内置默认), 避免每笔查库。

    同名券商可按类型并存(富途同时作港股/美股), 解析时按流水代码的市场挑对应类型的那条;
    市场对不上或同名只有一条时取第一条, 都没有再兜底内置默认费率。
    """
    from database import list_brokers
    brokers = await list_brokers()
    default = None  # 已无默认券商概念: 找不到具体券商→(None, None) 走模块内置默认费率
    byname: dict = {}
    for x in brokers:
        byname.setdefault(x["name"], []).append(x)
    _MKT_TYPE = {"A": "A股", "HK": "港股", "US": "美股"}

    def resolve(name, market=None):
        rows = byname.get(name) if name else None
        if rows:
            want = _MKT_TYPE.get(market)
            hit = next((x for x in rows if x.get("type") == want), None) if want else None
            pick = hit or rows[0]
            return (pick["stock_rate"], pick["stock_min"])
        return (default["stock_rate"], default["stock_min"]) if default else (None, None)

    def resolve_fut(name):
        """期货/期权手续费档(每手, 原币)。同名券商同口径; 没有券商 → (0,0)=逐笔手填。"""
        rows = byname.get(name) if name else None
        pick = (rows[0] if rows else None) or default
        if not pick:
            return (0.0, 0.0)
        return (pick.get("fut_rate") or 0.0, pick.get("fut_min") or 0.0)

    resolve.fut = resolve_fut       # 挂在同一个 resolver 上, 免得调用方多管一个对象
    return resolve


_UNSET = object()


async def attach_auto_fees(actions: list, stock_code: str, holding_broker, resolve=None,
                           fee_overrides=_UNSET):
    """给每笔流水按自己的 broker(没有则用持仓券商)预算自动手续费 → a['_auto_fee']。

    佣金最低值(5 元起)按「委托单」收一次, 不是按成交笔 —— 一张单分几个价位成交时
    逐笔各取一次最低值会重复收费(实测这份流水多收 101.22 元)。所以先按券商分组,
    组内交给 allocate_trade_fees 合并同一委托再分摊。港美股的收费方案也按这笔实际券商取。
    """
    if resolve is None:
        resolve = await broker_fee_resolver()
    if fee_overrides is _UNSET:
        from database import get_config
        fee_overrides = await get_config("fee_schedules")   # 港美股收费方案的覆盖(JSON), 见 services/market_fees.py
    by_broker: dict = {}
    fut_actions: list = []
    # 股票空头(OPEN/CLOSE): 估费按**真实交易方向** —— 开空是卖出(印花税在卖出腿),
    # 平空是买入。账本那边(FIFO 负价技巧)的 OPEN→BUY 映射只服务配对数学, 不代表资金方向。
    # 直接改 action_type 会污染原行, 复制一份带新类型的进费用管线。
    _STOCK_FEE_TYPE = {"OPEN": "SELL", "CLOSE": "BUY"}
    from services.ths_fuyao import is_thscode
    for a in actions:
        code = a.get("stock_code") or stock_code
        b = a.get("broker") or holding_broker
        if is_thscode(code):
            # 期货/期权: 费率优先级: assets_classify 比例费率(open_fee_rate × 名义金额 = 价×乘数×手数)
            #   > brokers.fut_rate(元/手 × 手数)兜底 > 0。逐笔手填的 fee 覆盖优先(compute 处已按
            #   a["fee"] 优先)。名义金额是"钱数", 不进按成交金额分摊的 A股管线。
            lots = float(a.get("shares") or 0)
            price = float(a.get("price") or 0)
            fee = 0.0
            prod = await _product_fee_of(code)          # 品种行(JOIN assets_classify)
            rate = None
            if prod:
                rate = prod.get("open_fee_rate") if (a.get("action_type") or "OPEN") == "OPEN" \
                    else prod.get("close_fee_rate")
            if rate is not None and price > 0 and lots > 0:
                mult = float(a.get("contract_multiplier") or 0) or float(prod.get("trade_amount") or 0) or 1.0
                fee = price * mult * lots * float(rate)
            else:
                fr, fmin = resolve.fut(b)
                fee = max((fr or 0) * lots, fmin or 0) if fr else 0.0
            a["_auto_fee"] = fee
            fut_actions.append(a)
            continue
        r, m = resolve(b, _foreign_market(code))
        # 港美股按券商名分组(方案按名字取); A 股沿用按费率分组
        by_broker.setdefault((r, m, b if _foreign_market(code) else None), []).append(a)
    for (r, m, _b), group in by_broker.items():
        fees = allocate_trade_fees(
            [dict(a, stock_code=a.get("stock_code") or stock_code, broker=a.get("broker") or holding_broker,
                  action_type=_STOCK_FEE_TYPE.get((a.get("action_type") or "").upper(), a.get("action_type")))
             for a in group], r, m, fee_overrides)
        for a, f in zip(group, fees):
            a["_auto_fee"] = f
    return resolve


async def ledger_state(code: str, actions: list | None = None, holding_broker=_UNSET, *,
                       resolve=None, fee_overrides=_UNSET,
                       fx_now: float | None = None) -> dict:
    """一只票的账(compute_position_state 的结果), 费用与汇率都按统一口径解析好。

    actions 不传则读库; holding_broker 不传则读持仓行的 broker。会原地给 actions 挂 _auto_fee。
    核算口径(加权平均): 成本只由买入决定, 每笔卖出立即结算为已实现事件; 已实现 = Σ
    切换日后平仓事件 + 分红。切换日从 app_config 读(60s 缓存)。
    港美股没传 fx_now 时取一次当前汇率(放线程里, 不卡事件循环)。
    """
    import asyncio
    if actions is None:
        from database import get_position_actions
        actions = await get_position_actions(code, limit=1000)
    if holding_broker is _UNSET:
        from database import get_holding
        holding_broker = ((await get_holding(code)) or {}).get("broker")
    resolve = await attach_auto_fees(actions, code, holding_broker, resolve, fee_overrides)
    if fx_now is None and _foreign_market(code) and any(not a.get("fx_rate") for a in actions):
        from services.market_data import code_fx
        fx_now = await asyncio.to_thread(code_fx, code)
    c_rate, c_min = resolve(holding_broker, _foreign_market(code))
    st = compute_position_state(actions, stock_code=code, commission_rate=c_rate, commission_min=c_min,
                                fx_now=fx_now, switch_date=await get_realized_switch_date())
    return st


# ── 核算切换日(realized_switch_date, 60s 缓存): 该日(含)之后的平仓事件才计入已实现 ──
_switch_cache: list = [None, 0.0]


async def get_realized_switch_date() -> str:
    import time
    if _switch_cache[0] and time.time() - _switch_cache[1] < 60:
        return _switch_cache[0]
    from database import get_realized_switch_date as _db_get
    v = await _db_get()
    _switch_cache[0] = v
    _switch_cache[1] = time.time()
    return v


async def rebuild_realized_events(code: str):
    """重放一只标的的全部流水, 重建 realized_events(幂等, 多空各一本账)。

    direction 分组重放(与 /realized、_recompute_holding 同口径); 切换日之后的平仓
    事件才落表(用户定稿: 历史不回溯)。事件带 action_id 可追溯到原流水。返回落表数。
    """
    from database import (get_position_actions, replace_realized_events,
                          get_holding, get_all_holdings)
    acts_all = await get_position_actions(code, limit=1000)
    if not acts_all:
        return await replace_realized_events(code, [])
    h0 = (await get_holding(code)) or {}
    # holdings 行可能已删(清仓后) → sub_assets_type 兜底, 没有就空着
    holdings_map = {h.get("stock_code"): h for h in await get_all_holdings()}
    h0 = h0 or holdings_map.get(code) or {}
    sub = h0.get("sub_assets_type")
    ticker = h0.get("ticker") or code.split(".")[0]
    resolve = await broker_fee_resolver()
    from database import get_config
    fee_overrides = await get_config("fee_schedules")
    switch_date = await get_realized_switch_date()

    dir_groups: dict = {}
    for a in acts_all:
        dir_groups.setdefault(a.get("direction") or "LONG", []).append(a)
    rows: list[dict] = []
    for d, acts in dir_groups.items():
        st = await ledger_state(code, acts, h0.get("broker"), resolve=resolve,
                                fee_overrides=fee_overrides)
        for e in st.get("realized_events") or []:
            rows.append({
                "code": code,
                "ticker": ticker,
                "direction": d if d != "LONG" else (h0.get("direction") or None),
                "sub_assets_type": sub,
                "action_id": e.get("action_id"),
                "side": e.get("side"),
                "close_shares": e.get("close_shares"),
                "close_price": e.get("close_price"),
                "avg_cost": e.get("avg_cost"),
                "realized_pnl": e.get("realized_pnl"),
                "realized_pnl_native": e.get("realized_pnl_native"),
                "fee": e.get("fee"),
                "fx_rate": e.get("fx_rate"),
                "contract_multiplier": e.get("contract_multiplier") or h0.get("contract_multiplier"),
                "trade_date": e.get("trade_date"),
            })
    return await replace_realized_events(code, rows)


async def rebuild_all_realized_events() -> int:
    """全量重建(启动迁移/手工触发): 逐用户 × 逐 code 重放。返回处理的 code 数。

    启动时无用户上下文(_uid()=0), 这里按 position_actions 里出现过的 user_id 逐个
    set_current_user 切上下文, 保证每个用户的 realized_events 按自己的流水重建。
    """
    from database import get_db
    from services.context import set_current_user
    db = await get_db()
    try:
        cur = await db.execute("SELECT DISTINCT user_id, code FROM position_actions")
        pairs = [(r[0], r[1]) for r in await cur.fetchall()]
    finally:
        await db.close()
    n = 0
    for uid in sorted({(u or 0) for u, _ in pairs}):
        set_current_user(uid)
        for code in sorted({c for u, c in pairs if (u or 0) == uid}):
            try:
                await rebuild_realized_events(code)
                n += 1
            except Exception as e:
                print(f"[realized-rebuild] {code}(user={uid}) 重建失败: {e}")
    set_current_user(0)
    return n
