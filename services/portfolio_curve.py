"""组合净值曲线(TWR) + 超额收益对照 — 逐日重建。

上一版 curve 服务在"大删除"时被清掉, 前端卡片退化成"曲线暂不可达"。这里按同样的
口径重建, 但只依赖现存积木:

  - 个股/场内ETF 逐日收盘: get_historical_data(前复权, 自带 kline_cache 持久缓存)
  - 指数日线: fetch_index_history(sh000300/hkHSI/gb_ixic…)
  - 历史汇率: fx_rate_on(港美股逐日折 CNY)

重建口径(与「跑赢基准对照」一致):
  A股 + 港美股 + 场内 ETF 逐日按收盘估值; 场外基金/理财/现金/机器人无逐日价格,
  按成本基线近似(只在申赎/存取那天跳变, 中间不产生收益)。
  TWR(时间加权)剥离出入金: 每日收益率 = (V(d) − V(d−1) − 当日净流入) / V(d−1)。
  份额拆分: 历史份额乘「该日之后的拆算因子」折到前复权标度(adjust_to_final_scale)。

算力成本: 每只标的一次日K拉取(kline_cache 落库后零联网), 结果内存缓存 1h。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

from services.market_data import code_currency, fetch_index_history, fx_rate_on
from services.position_ledger import ACQUIRE, RELEASE, split_ratio

_CACHE: dict[tuple, tuple] = {}      # (days, symbol) → (result, ts)
_CACHE_TTL = 3600.0
_LOCK = asyncio.Lock()

# 与跑赢基准对照同一份可选基准(前端 chips 也用这组)
SYMBOLS = ("sh000300", "sh000001", "sz399006", "sh000688", "hkHSI", "gb_inx", "gb_ixic")

ACQUIRE_BASE = ("BUY", "ADD", "DEPOSIT")
RELEASE_BASE = ("REDEEM", "SELL", "REDUCE", "WITHDRAW")


def _today_cst() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _flow_cash(act: dict, *, etf: bool) -> float:
    """单笔流水的现金流(币种口径在调用方折): 买入为正(钱进组合), 卖出为负(钱出组合)。

    个股账本只有 price/shares/fee; 场外账本 amount 买入已含费、卖出是毛额(自减 fee) ——
    与跑赢基准对照 etf_flows 归一段同规则。
    """
    t = (act.get("action_type") or "").upper()
    sh = float(act.get("shares") or 0)
    amt = float(act.get("amount") or 0)
    fee = float(act.get("fee") or 0)
    px = float(act.get("price") or 0)
    if t in ACQUIRE:
        return (amt if etf and amt > 0 else px * sh) + fee
    if t in RELEASE:
        return -((amt if etf and amt > 0 else px * sh) - fee)
    return 0.0


async def _collect_universe() -> dict:
    """两类账本归一成逐标的流水表 + 无价资产的成本基线流水。"""
    from database import get_position_actions, list_external_assets, list_external_actions
    from services.external_assets import _is_onchain_etf

    stocks: dict[str, list] = {}
    for a in await get_position_actions(limit=5000):
        code = a.get("stock_code") or ""
        t = (a.get("action_type") or "").upper()
        td = (a.get("trade_date") or "")[:10]
        if not code or not td:
            continue
        rec = {"date": td, "shares": 0.0, "cash": _flow_cash(a, etf=False), "type": t,
               "fx_rate": float(a.get("fx_rate") or 0)}
        if t == "SPLIT" and split_ratio(a) > 0:
            rec["split"] = split_ratio(a)
        elif t in ACQUIRE:
            rec["shares"] = float(a.get("shares") or 0)
        elif t in RELEASE:
            rec["shares"] = -float(a.get("shares") or 0)
        stocks.setdefault(code, []).append(rec)

    etfs: dict[str, list] = {}
    baseline: list[dict] = []
    for x in await list_external_assets():
        is_etf = (x.get("asset_type") or "") == "FUND" and _is_onchain_etf(str(x.get("code") or ""))
        for act in await list_external_actions(x["id"]):
            if (act.get("status") or "confirmed") != "confirmed":
                continue
            td = (act.get("trade_date") or "")[:10]
            if not td:
                continue
            t = (act.get("action_type") or "").upper()
            if is_etf:
                rec = {"date": td, "shares": 0.0, "cash": _flow_cash(act, etf=True), "type": t}
                if t == "SPLIT" and float(act.get("shares") or 0) > 0:
                    rec["split"] = float(act["shares"])
                elif t in ACQUIRE:
                    rec["shares"] = float(act.get("shares") or 0)
                elif t in RELEASE:
                    rec["shares"] = -float(act.get("shares") or 0)
                etfs.setdefault(str(x.get("code") or ""), []).append(rec)
            else:
                # 场外基金/理财/现金/机器人: 成本基线。申购/存入进, 赎回/取出出
                cash = float(act.get("amount") or 0)
                if cash <= 0:
                    continue
                if t in ACQUIRE_BASE:
                    baseline.append({"date": td, "cash": cash})
                elif t in RELEASE_BASE:
                    baseline.append({"date": td, "cash": -cash})

    for lst in (*stocks.values(), *etfs.values()):
        lst.sort(key=lambda r: r["date"])
    baseline.sort(key=lambda r: r["date"])
    codes = sorted(set(stocks) | set(etfs))
    return {"stocks": stocks, "etfs": etfs, "baseline": baseline, "codes": codes,
            "fx_codes": {c for c in codes if code_currency(c) != "CNY"}}


async def _daily_closes(codes: list[str], min_days: int) -> dict[str, dict]:
    """code → {date: 收盘(前复权)}。逐标的一次拉取, kline_cache 落库后零联网。"""
    from services.market_data import get_historical_data

    async def one(code: str):
        try:
            df = await get_historical_data(code, max(min_days, 120))
        except Exception:
            return code, {}
        if df is None or not len(df) or "日期" not in df.columns:
            return code, {}
        out = {}
        for _, r in df.iterrows():
            d = str(r.get("日期") or "")[:10]
            try:
                c = float(r.get("收盘"))
            except (TypeError, ValueError):
                continue
            if d and c > 0:
                out[d] = c
        return code, out

    pairs = await asyncio.gather(*(one(c) for c in codes))
    return dict(pairs)


def _split_factor_after(recs: list[dict], d: str) -> float:
    """d 之后发生的拆算因子累计 —— 把 d 日的原始份额折到前复权(现行)标度。"""
    f = 1.0
    for r in recs:
        if r["date"] > d and r.get("split"):
            f *= r["split"]
    return f


def _shares_at(recs: list[dict], d: str) -> float:
    s = 0.0
    for r in recs:
        if r["date"] > d:
            break
        s += r["shares"]
    return s


def _baseline_at(baseline: list[dict], d: str) -> float:
    v = 0.0
    for r in baseline:
        if r["date"] > d:
            break
        v += r["cash"]
    return v


def _max_drawdown(series: list) -> float:
    peak, worst = series[0], 0.0
    for v in series:
        peak = max(peak, v)
        worst = min(worst, (v / peak - 1) * 100)
    return round(worst, 2)


async def build_curve(days: int = 60, symbol: str = "sh000300") -> dict:
    """→ {dates, twr, bench:{symbol,series}, metrics{…}, coverage{…}}; 无任何记录时给 error。

    twr/bench 同起点归一 100(起点 = 第一笔钱进场后首个交易日); 超额 = twr − bench。
    """
    symbol = symbol if symbol in SYMBOLS else "sh000300"
    uni = await _collect_universe()
    recs_by_code = {**uni["stocks"], **uni["etfs"]}
    n_flows = sum(len(v) for v in recs_by_code.values()) + len(uni["baseline"])
    if n_flows == 0:
        return {"error": "还没有任何交易记录 —— 先在持仓页记账, 这里会自动生成收益曲线", "empty": True}

    # ---- 日期轴: 用指数交易日(各市场日历对不齐时, 个股按「≤d 最近收盘」估值) ----
    df = await asyncio.to_thread(fetch_index_history, symbol, int(days) + 20 if days else 1200)
    if df is None or len(df) == 0:
        return {"error": f"基准 {symbol} 行情加载失败, 稍后再试"}
    axis_all = [str(d)[:10] for d in df["date"]]
    if days:
        axis = [d for d in axis_all if d >= (date.today() - timedelta(days=int(days))).isoformat()]
    else:
        first_flow = min((r["date"] for r in uni["baseline"]), default=None)
        for lst in recs_by_code.values():
            if lst and (first_flow is None or lst[0]["date"] < first_flow):
                first_flow = lst[0]["date"]
        axis = [d for d in axis_all if d >= (first_flow or axis_all[0])]
    if not axis:
        axis = axis_all[-30:]

    closes = await _daily_closes(uni["codes"], int(days or 400) + 30)
    missing = [c for c in uni["codes"] if not closes.get(c)]

    def fx_of(code: str, d: str) -> float:
        if code not in uni["fx_codes"]:
            return 1.0
        try:
            return float(fx_rate_on(code_currency(code), d)) or 1.0
        except Exception:
            return 1.0

    def close_on_or_before(m: dict, d: str):
        last = None
        for cd, c in m.items():              # m ≤ ~400 条, 线性扫足够
            if cd <= d and (last is None or cd > last[0]):
                last = (cd, c)
        return last[1] if last else None

    def value_on(d: str) -> float:
        v = _baseline_at(uni["baseline"], d)
        for code, m in closes.items():
            recs = recs_by_code.get(code) or []
            sh = _shares_at(recs, d)
            if sh == 0:
                continue
            px = close_on_or_before(m, d)
            if px:
                v += px * sh * _split_factor_after(recs, d) * fx_of(code, d)
        return v

    def flow_cny_on(d: str) -> float:
        """当日净流入(CNY): 个股流水折成交日汇率(记了用记的, 没记按当日牌价)。"""
        f = sum(r["cash"] for r in uni["baseline"] if r["date"] == d)
        for code, recs in recs_by_code.items():
            for r in recs:
                if r["date"] != d:
                    continue
                if code in uni["fx_codes"]:
                    f += r["cash"] * (r.get("fx_rate") or fx_of(code, d) or 1.0)
                else:
                    f += r["cash"]
        return f

    # ---- TWR 链条 ----
    prev_v = None
    twr = 1.0
    dates_out, twr_out = [], []
    for d in axis:
        v = value_on(d)
        if prev_v is not None and prev_v > 1e-6:
            twr *= 1 + (v - prev_v - flow_cny_on(d)) / prev_v
        elif prev_v is None:
            if v > 1e-6 or flow_cny_on(d) != 0:
                prev_v = v                   # 第一笔钱进场, 链条从这里起步
                dates_out.append(d)
                twr_out.append(100.0)
            continue
        prev_v = v
        dates_out.append(d)
        twr_out.append(round(twr * 100, 4))
    if not dates_out:
        return {"error": "窗口内没有形成持仓 —— 换更长的时间段, 或先记几笔交易", "empty": True}

    # ---- 基准同起点归一(起点 = 曲线起点 d0) ----
    bench_by_date = {str(d)[:10]: float(c) for d, c in zip(df["date"], df["close"])}
    d0 = dates_out[0]
    base0 = next((bench_by_date[d] for d in axis_all if d >= d0 and bench_by_date.get(d)), None)
    if not base0:
        return {"error": f"基准 {symbol} 在起点之后没有数据"}
    bench_series, last = [], base0
    for d in dates_out:
        c = bench_by_date.get(d)
        if c:
            last = c
        bench_series.append(round(last / base0 * 100, 4))

    p_ret = round(twr_out[-1] - 100, 2)
    b_ret = round(bench_series[-1] - 100, 2)
    beat_days = sum(1 for a, b in zip(twr_out, bench_series) if a >= b)
    return {
        "dates": dates_out,
        "twr": twr_out,
        "bench": {"symbol": symbol, "series": bench_series},
        "metrics": {
            "区间收益%": p_ret,
            "最大回撤%": _max_drawdown(twr_out),
            "基准收益%": b_ret,
            "超额%": round(p_ret - b_ret, 2),
            "起点": d0,
        },
        "coverage": {
            "priced_symbols": len(uni["codes"]),
            "missing_prices": missing,
            "baseline_assets": len(uni["baseline"]),
            "beat_days": beat_days,
            "total_days": len(dates_out),
        },
        "note": "TWR 剥离出入金; 现金/理财/场外基金/机器人按成本基线近似(不产生日内收益); "
                "港美股按成交日汇率折 CNY。",
    }


async def curve_cached(days: int = 60, symbol: str = "sh000300") -> dict:
    key = (int(days or 0), symbol)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[1] < _CACHE_TTL:
        return hit[0]
    async with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[1] < _CACHE_TTL:
            return hit[0]
        out = await build_curve(int(days or 0), symbol)
        if not out.get("error"):
            _CACHE[key] = (out, time.time())
        return out
