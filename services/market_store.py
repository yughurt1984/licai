"""market schema 行情库存取(read-through 持久层)。

设计: 行情读取永远先走远端(保证盘中新鲜度), 远端**成功**后 write-through 落库,
**失败/超时**才回退这里 —— 平时"所有数据从 PG 获取"由回退路径兜底成立, PG 里永远有
最后一次成功快照。写入方就是现有 API 请求 + 12 个后台 loop(price_monitor/curve/eod 等),
不需要独立的采集任务。

表(见 database.py SCHEMA 末尾):
  market.quote_latest  有每日报价的标的最新快照(a_stock/hk_stock/us_stock/fund/
                       commodity/crypto, 扩展 index/fx)
  market.fx_daily      历史汇率(替代 fx_last.json 的历史序列职责, 文件先保留兜底)

汇率的历史读取是同步调用链(回溯估值, 走 to_thread), 不能 await PG —— 用与
rule_forge/user_memory 相同的"内存缓存 + 后台预热"模式: 启动时 warm_fx_cache() 全量
载入 _FX_DB_CACHE, 写入时同步更新缓存, 同步读永远 O(1)。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import asyncpg

from config import config

_pool: asyncpg.Pool | None = None


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or getattr(_pool, "_closed", False):
        _pool = await asyncpg.create_pool(
            config.pg_dsn, min_size=0, max_size=5,
            server_settings={"search_path": "market,public"},
        )
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


QUOTE_COLS = ("symbol", "asset_type", "name", "price", "open", "high", "low", "prev_close",
              "volume", "amount", "change_pct", "turnover_rate", "currency",
              "quote_time", "source", "fetched_at")


async def save_quotes(rows: list[dict]) -> int:
    """write-through 最新快照。rows: store 形状 dict(见 quote_to_store_row)。缺的列存 NULL。

    best-effort: 行情落库失败不该影响取价本身。
    """
    rows = [r for r in rows or [] if r.get("symbol")]
    if not rows:
        return 0
    try:
        pool = await get_pool()
        now = _utc_now_text()
        cols = QUOTE_COLS
        ph = ",".join(f"${i+1}" for i in range(len(cols)))
        updates = ",".join(f"{c} = excluded.{c}" for c in cols[1:-1])   # symbol 是冲突目标
        sql = (f"INSERT INTO quote_latest ({','.join(cols)}) VALUES ({ph}) "
               f"ON CONFLICT (symbol) DO UPDATE SET {updates}, fetched_at = excluded.fetched_at")
        data = [tuple(r.get(c) for c in cols) for r in rows]
        for i in range(0, len(data), 500):
            await pool.executemany(sql, data[i:i + 500])
        return len(rows)
    except Exception as e:
        print(f"[market_store] save_quotes 失败(不影响行情): {e}")
        return 0


async def get_stored_quotes(symbols: list[str]) -> dict[str, dict]:
    """读最后快照 → {symbol: store 形状 row(含 fetched_at)}。"""
    if not symbols:
        return {}
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            f"SELECT {','.join(QUOTE_COLS)} FROM quote_latest WHERE symbol = ANY($1)",
            list(symbols))
        return {r["symbol"]: dict(r) for r in rows}
    except Exception as e:
        print(f"[market_store] get_stored_quotes 失败: {e}")
        return {}


async def get_stored_by_type(asset_type: str) -> list[dict]:
    """某个 asset_type 的全部快照(观测/诊断用)。"""
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            f"SELECT {','.join(QUOTE_COLS)} FROM quote_latest WHERE asset_type = $1 "
            "ORDER BY symbol", asset_type)
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[market_store] get_stored_by_type 失败: {e}")
        return []


# ---- 汇率: fx_daily + 进程内缓存 ----

_FX_DB_CACHE: dict[str, dict[str, float]] = {}     # {pair: {date: rate}}
_fx_lock = asyncio.Lock()


def fx_history_cached(pair: str) -> dict[str, float]:
    """同步读: 进程内缓存的历史汇率({date: rate})。没预热过/为空时返回 {}。"""
    return dict(_FX_DB_CACHE.get((pair or "").upper(), {}))


async def warm_fx_cache():
    """启动/定期预热: 把 fx_daily 全量载入内存。同步读路径靠它。"""
    global _FX_DB_CACHE
    async with _fx_lock:
        try:
            pool = await get_pool()
            rows = await pool.fetch("SELECT pair, date, rate FROM fx_daily ORDER BY pair, date")
            cache: dict[str, dict[str, float]] = {}
            for r in rows:
                cache.setdefault(r["pair"], {})[r["date"]] = float(r["rate"])
            _FX_DB_CACHE = cache
        except Exception as e:
            print(f"[market_store] 汇率缓存预热失败(用旧缓存/磁盘兜底): {e}")


async def save_fx_day(pair: str, day: str, rate: float, source: str = ""):
    """单日汇率 upsert(实时取到时 write-through)。同步更新内存缓存。"""
    pair = (pair or "").upper()
    if not pair or not day or not rate:
        return
    try:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO fx_daily (pair, date, rate, source) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (pair, date) DO UPDATE SET rate = excluded.rate, source = excluded.source",
            pair, day, float(rate), source)
        _FX_DB_CACHE.setdefault(pair, {})[day] = float(rate)
    except Exception as e:
        print(f"[market_store] save_fx_day 失败(不影响取价): {e}")


async def save_fx_history(pair: str, rates: dict[str, float], source: str = ""):
    """整段历史汇率回填(BOC 全量拉取成功后)。"""
    pair = (pair or "").upper()
    if not pair or not rates:
        return
    try:
        pool = await get_pool()
        data = [(pair, d, float(v), source) for d, v in rates.items() if d and v]
        if not data:
            return
        await pool.executemany(
            "INSERT INTO fx_daily (pair, date, rate, source) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (pair, date) DO UPDATE SET rate = excluded.rate, source = excluded.source",
            data)
        _FX_DB_CACHE.setdefault(pair, {}).update({d: float(v) for d, v in rates.items() if d and v})
    except Exception as e:
        print(f"[market_store] save_fx_history 失败(不影响取价): {e}")


_TASKS: set = set()


def spawn(coro) -> None:
    """fire-and-forget: 同步调用链里发起异步落库(有事件循环才发, 否则静默跳过)。

    强引用留在 _TASKS 防 GC; 异常在协程内部已各自兜住, 这里只做回收。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


# ---- 观测 ----

async def db_status() -> dict:
    """行情库覆盖度/新鲜度(给 /api/market/db-status 用)。"""
    pool = await get_pool()
    by_type = await pool.fetch(
        "SELECT asset_type, COUNT(*) n, MAX(fetched_at) latest, MIN(fetched_at) oldest "
        "FROM quote_latest GROUP BY asset_type ORDER BY asset_type")
    stale = await pool.fetch(
        "SELECT symbol, asset_type, fetched_at FROM quote_latest "
        "WHERE fetched_at < to_char(now() AT TIME ZONE 'utc' - interval '3 days', 'YYYY-MM-DD HH24:MI:SS') "
        "ORDER BY fetched_at LIMIT 50")
    fx = await pool.fetch(
        "SELECT pair, COUNT(*) n, MIN(date) first, MAX(date) last FROM fx_daily "
        "GROUP BY pair ORDER BY pair")
    return {
        "quote_by_type": [{"asset_type": r["asset_type"], "symbols": r["n"],
                           "latest_fetched_at": r["latest"], "oldest_fetched_at": r["oldest"]}
                          for r in by_type],
        "stale_symbols": [{"symbol": r["symbol"], "asset_type": r["asset_type"],
                           "fetched_at": r["fetched_at"]} for r in stale],
        "fx_daily": [{"pair": r["pair"], "days": r["n"], "first": r["first"], "last": r["last"]}
                     for r in fx],
    }
