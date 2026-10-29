"""基金代理标的 — 用底层真实持仓 (天天基金 top10) 实时行情预判基金当日走势.

策略 (优先级从高到低):
  1. 真实 top10 持仓加权 (准确度 ~85-95%)，每只成分股实时拉报价 (A股/港股/美股)
  2. 硬编码代理回退 (NQ/HSI/AU0 等)，覆盖商品基金或 top10 拉不到时

为啥要这个: 场外基金 NAV 是 T+1 公布的 (晚 21:00+), 盘中 fundgz 估值对 QDII /
跨市场基金偏差大. 用真实成分股实时数据,盘中就能看出基金大概率涨/跌,
帮助定投/减仓决策.
"""
from __future__ import annotations
import asyncio
import time

# 兜底硬编码代理 (用于商品基金 / top10 拉不到 / 加权计算失败时)
HARDCODED_PROXIES: dict[str, list[dict]] = {
    "000217": [  # 华安黄金 ETF 联接 C — 商品基金, 用商品期货代理
        {"code": "AU0", "name": "沪金主连", "type": "commodity", "weight": 0.6},
        {"code": "GC",  "name": "COMEX 金", "type": "overseas",   "weight": 0.4},
    ],
    "000041": [  # 华夏全球精选 QDII — 兜底用 NQ + 沪深300 (top10 含 A股科技)
        {"code": "NQ",     "name": "纳指期货",    "type": "overseas", "weight": 0.6},
        {"code": "510300", "name": "沪深300",     "type": "a_etf",    "weight": 0.4},
    ],
    "159941": [  # 纳指 ETF 广发 — 跟踪纳指 100, 100% NQ
        {"code": "NQ", "name": "纳指期货", "type": "overseas", "weight": 1.0},
    ],
    "161226": [  # 国投瑞银白银期货 LOF — 跟踪上海白银期货
        {"code": "AG0", "name": "沪银主连", "type": "commodity", "weight": 0.7},
        {"code": "SI",  "name": "COMEX 银", "type": "overseas",   "weight": 0.3},
    ],
}

# 哪些基金跳过 top10 直接走硬编码 (商品基金 top10 全是商品期货 ETF, 不如直接拉期货)
SKIP_HOLDINGS = {"000217", "161226"}  # 黄金/白银联接

def _load_local_overrides() -> None:
    """本机自己持有基金的代理(data/fund_proxy_local.json, 不进仓):
    {"proxies": {代码: [...]}, "skip_holdings": [代码]}。"""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "fund_proxy_local.json"
    if not p.exists():
        return
    try:
        d = json.loads(p.read_text())
    except Exception:
        return
    HARDCODED_PROXIES.update(d.get("proxies") or {})
    SKIP_HOLDINGS.update(d.get("skip_holdings") or [])


_load_local_overrides()

_proxy_cache: dict[str, tuple[dict, float]] = {}
_PROXY_TTL = 30  # 秒


# ---- 硬编码代理: 商品/期货/指数实时 ----

def _fetch_hardcoded_one(p: dict) -> dict | None:
    from services.market_data import (
        _fetch_futures_quote, _fetch_overseas_quote, _fetch_hk_index_quote,
    )
    t = p["type"]
    sym = p["code"]
    if t == "commodity":
        q = _fetch_futures_quote(sym)
    elif t == "overseas":
        q = _fetch_overseas_quote(sym)
    elif t == "hk_index":
        q = _fetch_hk_index_quote(sym)
    elif t == "a_etf":
        # A股 ETF 用同步 sina quotes (运行在 thread executor 里, 不能再嵌一层 event loop)
        from services.sina_quote import _fetch_sina_quotes
        r = _fetch_sina_quotes([sym])
        qq = r.get(sym)
        q = {"price": qq["price"], "change_pct": qq["change_pct"]} if qq else None
    else:
        return None
    if not q:
        return None
    return {
        "code": sym, "name": p["name"], "weight": p["weight"],
        "price": q.get("price"), "change_pct": q.get("change_pct", 0),
        "source": "hardcoded",
    }


async def _hardcoded_proxy(fund_code: str) -> dict | None:
    proxies = HARDCODED_PROXIES.get(fund_code)
    if not proxies:
        return None
    results = await asyncio.gather(
        *(asyncio.to_thread(_fetch_hardcoded_one, p) for p in proxies),
        return_exceptions=True,
    )
    valid = [r for r in results if r and not isinstance(r, Exception)]
    if not valid:
        return None
    total_w = sum(r["weight"] for r in valid)
    weighted = sum(r["change_pct"] * r["weight"] for r in valid) / total_w if total_w > 0 else 0
    label = " + ".join(f"{r['name']}({r['change_pct']:+.2f}%)" for r in valid)
    return {
        "fund_code": fund_code,
        "label": label,
        "weighted_change_pct": round(weighted, 2),
        "proxies": valid,
        "method": "hardcoded",
    }


# ---- 主入口 ----

async def get_fund_proxy(fund_code: str, force: bool = False) -> dict | None:
    """硬编码代理(基金前十大穿透已移除)。"""
    now = time.time()
    if not force:
        c = _proxy_cache.get(fund_code)
        if c and now - c[1] < _PROXY_TTL:
            return c[0]

    out = None
    try:
        out = await _hardcoded_proxy(fund_code)
    except Exception as e:
        print(f"[fund-proxy] hardcoded path failed for {fund_code}: {e}")

    if out:
        _proxy_cache[fund_code] = (out, now)
    return out
