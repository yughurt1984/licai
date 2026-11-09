"""行情综合路由(瘦身版): 只留手动刷新 + 交易日判断。其余行情端点已随功能删除移除。"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from pydantic import BaseModel

from services.market_data import normalize_stock_code, get_realtime_quotes

router = APIRouter(prefix="/api/market", tags=["market"])



# ── 标准码拼接: assets_classify.code_suffix 驱动 ──────────────────────────
# 小类 → 市场后缀(如 HK/US/JP/OF)从 DB 读取(60s 缓存)。
# code_suffix = 'AUTO' 或 NULL → A股等码形推市场的小类, 走 normalize_stock_code 码形推断。
_suffix_cache: dict = {"data": {}, "ts": 0.0}


async def _get_code_suffixes() -> dict:
    """{sub_assets_type: code_suffix} 从 assets_classify 读取, 60s 缓存。"""
    import time as _t
    if _suffix_cache["data"] and _t.time() - _suffix_cache["ts"] < 60:
        return _suffix_cache["data"]
    from database import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT sub_assets_type, code_suffix FROM assets_classify "
            "WHERE code_suffix IS NOT NULL AND code_suffix <> ''")
        data = {r[0]: r[1].strip().upper() for r in await cur.fetchall()}
    except Exception:
        data = {}
    _suffix_cache["data"] = data
    _suffix_cache["ts"] = _t.time()
    return data


def _build_standard_code(bare: str, suffix: str) -> str:
    """裸码 + 市场后缀 → 标准码(统一拼接, 不再按码形硬编码)。"""
    body = bare.split(".")[0] if "." in bare else bare
    if suffix == "HK" and body.isdigit():
        body = body.zfill(5)                              # 港股补前导零
    return f"{body}.{suffix}"


@router.get("/symbol-name")
async def symbol_name(code: str = "", sub: str = ""):
    """添加资产的名称自动匹配(两级):

    ① symbol_dict 底稿: 按 ticker(裸码, 如 600111) → code(标准格式 600111.SH), 纯本地零联网;
    ② 底稿未命中(新上市标的) → 按表单所选小类(sub_assets_type)路由 instruments 源
       (assets_classify.main_instruments_source → back; tickflow=股/ETF/港美股,
       ths=期货/期权品种)获取元数据 → 立即回显, 后台写入 symbol_dict(下次起纯本地)。
    查无此标的(或源未配置)返回 name=null, 不落库。
    """
    bare = str(code or "").strip().upper()
    sub_name = str(sub or "").strip()
    if not bare:
        return {"name": None, "code": None}

    # ① 标准码: code_suffix 驱动(assets_classify.code_suffix → 市场后缀), 统一拼接
    suffixes = await _get_code_suffixes()
    suffix = suffixes.get(sub_name, "")
    if suffix:
        std = _build_standard_code(bare, suffix)
    elif "otc" in sub_name:
        std = bare + ".OF" if "." not in bare else bare.upper()   # OTC 基金 .OF 后缀(老数据/未配置 code_suffix)
    else:
        std = normalize_stock_code(bare)
    body = std.split(".")[0] if "." in std else std

    from database import get_db, save_symbol_dict
    db = await get_db()
    try:
        # 大小写不敏感: 库里存 cu / aapl / 600406.sh 都能命中(美股 ticker 尤其常见小写)
        cur = await db.execute("SELECT name, code FROM symbol_dict WHERE UPPER(ticker) = ? LIMIT 1", (body,))
        r = await cur.fetchone()
        if r is None:
            cur = await db.execute("SELECT name, code FROM symbol_dict WHERE UPPER(code) = ? LIMIT 1", (std,))
            r = await cur.fetchone()
        if r is not None:
            return {"name": r[0] if r else None, "code": r[1] if r else None}
    finally:
        await db.close()

    # ② instruments 源获取(底稿未命中); 逐源状态透传给前端(缺 key/未配置一眼可见)
    from services import instruments
    row, sources = await instruments.fetch_instrument_diag(sub_name, std)
    if not row or not row.get("name"):
        return {"name": None, "code": None,
                "sources": [{"source": n, "status": st} for n, st in sources]}
    try:
        # 落库与响应同请求内 await(单行 upsert 毫秒级; create_task 跨响应会被循环关闭取消)
        await save_symbol_dict([(
            row["code"], row["name"], row.get("ticker") or "", sub_name,
            row.get("suffix") or "", row.get("trade_amount"), row.get("margin_rate"),
            row.get("source"))])
    except Exception as e:
        print(f"[symbol-name] 底稿登记失败(不影响回显): {e}")
    return {"name": row["name"], "code": row["code"], "fetched": True,
            "sources": [{"source": n, "status": st} for n, st in sources]}


@router.get("/asset-classes")
async def asset_classes(assets_class: str = ""):
    """资产分类目录(assets_classify, 人工维护): 录入表单的下拉选项与联动匹配数据源。

    ?assets_class=stock 只取股票大类。前端用 main_assets_type × currency 匹配唯一
    sub_assets_type —— 市场归属/币种/名称都以 PG 为准, 不在前端硬编码。
    """
    from database import get_db
    db = await get_db()
    try:
        sql = ("SELECT assets_class, assets_class_name, main_assets_type, assets_type_name, "
               "sub_assets_type, sub_assets_type_name, currency, valuation_type, risk_level "
               "FROM assets_classify")
        params: list = []
        if assets_class.strip():
            sql += " WHERE assets_class = ?"
            params.append(assets_class.strip())
        sql += " ORDER BY main_assets_type, sub_assets_type"
        cur = await db.execute(sql, params)
        return {"items": [dict(r) for r in await cur.fetchall()]}
    finally:
        await db.close()


@router.get("/quote/{code}")
async def market_quote(code: str):
    """单标的报价(前端名称兜底/行情查看): PG 优先(quote_latest), 缺行冷启动联网一次。

    返回 get_realtime_quotes 形状的 quote(stock_code/stock_name/price/...);
    标的不可得时 stock_name 为 null。
    """
    from services.market_data import get_realtime_quotes, normalize_stock_code
    std = normalize_stock_code(code)
    quotes = await get_realtime_quotes([std])
    r = quotes.get(std) or (next(iter(quotes.values()), {}) if quotes else {})
    return r or {"stock_code": std, "stock_name": None}


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


@router.get("/status")
async def market_status_route():
    """顶栏三市场状态徽章: {A, HK, US} 各自 open(交易中) / closed(已收盘, 交易日但
    非连续时段) / rest(休息, 非交易日)。纯本地日历计算零联网(A股 chinese_calendar,
    港美股 exchange_calendars)。前端轮询它而不是读 WS 推送 —— price_update 只推给
    有持仓可推的用户, 没持仓时徽章会永远停在初始值。"""
    from services.market_data import markets_status
    return {"statuses": markets_status()}


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
