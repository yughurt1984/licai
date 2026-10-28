"""行情综合路由(瘦身版): 只留手动刷新 + 交易日判断。其余行情端点已随功能删除移除。"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from pydantic import BaseModel

from services.market_data import normalize_stock_code, get_realtime_quotes

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/symbol-name")
async def symbol_name(code: str = ""):
    """添加股票的名称自动匹配: 优先按 symbol_dict.ticker(裸码, 如 600111)匹配,
    退回按 code(数据源标准格式 600111.SH)匹配。纯本地零联网, 查不到返回 null。"""
    bare = str(code or "").strip().upper()
    if not bare:
        return {"name": None, "code": None}
    from database import get_db
    db = await get_db()
    try:
        cur = await db.execute("SELECT name, code FROM symbol_dict WHERE ticker = ? LIMIT 1", (bare,))
        r = await cur.fetchone()
        if r is None:
            cur = await db.execute("SELECT name, code FROM symbol_dict WHERE code = ? LIMIT 1", (bare,))
            r = await cur.fetchone()
        return {"name": r[0] if r else None, "code": r[1] if r else None}
    finally:
        await db.close()


class MarketRefreshReq(BaseModel):
    codes: list[str] = []        # 空 = 当前用户全部股票/ETF 持仓


@router.post("/refresh")
async def market_refresh(body: MarketRefreshReq):
    """手动刷新行情: 联网抓取 → write-through 落 PG(market.quote_latest)。"""
    from services.market_data import quote_auto_fetch, get_stock_sector
    codes = [normalize_stock_code(c) for c in (body.codes or []) if str(c or "").strip()]
    if not codes:
        from database import get_all_holdings
        codes = [h["stock_code"] for h in await get_all_holdings()]
    codes = sorted(set(codes))
    if not codes:
        return {"ok": True, "auto_fetch": quote_auto_fetch(),
                "updated": 0, "missing": [], "quotes": {}, "sectors": {}}
    quotes = await get_realtime_quotes(codes, force=True)
    # 行业标签(内存缓存 24h): 只补缓存里没有的, A 股才需要联网
    sectors = {}
    sec_results = await asyncio.gather(
        *(get_stock_sector(c, allow_network=True) for c in codes), return_exceptions=True,
    )
    for code, sec in zip(codes, sec_results):
        if isinstance(sec, str) and sec:
            sectors[code] = sec
    missing = [c for c in codes if c not in quotes]
    return {"ok": True, "auto_fetch": quote_auto_fetch(),
            "updated": len(quotes), "missing": missing,
            "quotes": quotes, "sectors": sectors}


@router.get("/trading-day")
async def trading_day_status():
    """Whether today (CST) is an A-share trading day. Excludes weekends + 法定假日.
    Also returns the next trading day for UX hints.
    """
    try:
        import chinese_calendar as cc
    except ImportError:
        # Fallback: weekend-only detection
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        is_weekend = today.weekday() >= 5
        return {
            "date": str(today),
            "is_trading_day": not is_weekend,
            "is_weekend": is_weekend,
            "is_holiday": False,
            "next_trading_day": None,
            "fallback": True,
        }

    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    is_holiday = cc.is_holiday(cst_today)
    is_weekend = cst_today.weekday() >= 5
    # A 股交易日: 必须工作日且非周末. chinese_calendar 的 is_workday 在 调休补班的
    # 周六/日 也返 True, 但 A 股交易所节假日调休时不开市, 周末永远不交易.
    is_a_share_trading_day = cc.is_workday(cst_today) and not is_weekend

    # Find next A-share trading day (skip weekends + holidays + 调休 weekends)
    next_td = None
    if not is_a_share_trading_day:
        d = cst_today + timedelta(days=1)
        for _ in range(14):
            if cc.is_workday(d) and d.weekday() < 5:
                next_td = str(d)
                break
            d += timedelta(days=1)

    HOLIDAY_CN = {
        "New Year's Day": "元旦",
        "Spring Festival": "春节",
        "Tomb-sweeping Day": "清明",
        "Labour Day": "劳动节",
        "Dragon Boat Festival": "端午",
        "National Day": "国庆",
        "Mid-autumn Festival": "中秋",
        "Anti-Fascist 70th Day": "抗战胜利纪念",
    }
    holiday_name = ""
    # 法定节假日 (含调休关联) 都尝试取名字, 包括"调休补班但 A 股不开市"的周末
    try:
        _, name = cc.get_holiday_detail(cst_today)
        if name:
            holiday_name = HOLIDAY_CN.get(name, name)
            if is_weekend and cc.is_workday(cst_today):
                holiday_name = f"{holiday_name}调休补班"
    except Exception:
        pass

    return {
        "date": str(cst_today),
        "is_trading_day": is_a_share_trading_day,
        "is_weekend": is_weekend,
        "is_holiday": is_holiday and not is_weekend,
        "holiday_name": holiday_name,
        "next_trading_day": next_td,
    }
