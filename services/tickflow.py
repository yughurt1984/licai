"""TickFlow 数据源(REITs 实时行情)。

文档: https://docs.tickflow.org/zh-hans/api-reference/实时行情/查询实时行情
GET https://api.tickflow.org/v1/quotes?symbols=508056.SH, header x-api-key。
REITs 代码段位: 沪 508xxx → .SH, 深 180xxx → .SZ。
key: 优先设置页(app_config 全局键 tickflow_api_key), 其次 config(.env TICKFLOW_API_KEY)。
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional

import requests as _requests
import config as _config

BASE = "https://api.tickflow.org"
_TIMEOUT = 10

_session = _requests.Session()
_session.trust_env = False

_key_cache: list = [None, 0.0]      # (key, ts)


async def _api_key() -> str:
    """优先设置页保存的(app_config 全局键), 其次 .env/config。缓存 60s。"""
    if time.time() - _key_cache[1] < 60 and _key_cache[0] is not None:
        return _key_cache[0]
    key = ""
    try:
        from database import get_config
        key = (await get_config("tickflow_api_key") or "").strip()
    except Exception:
        pass
    if not key:
        key = (getattr(_config, "tickflow_api_key", "") or "").strip()
    _key_cache[0], _key_cache[1] = key, time.time()
    return key


def invalidate_key_cache():
    _key_cache[0] = None
    _key_cache[1] = 0.0


def reits_symbol(code: str) -> str:
    """REITs 6位代码 → tickflow symbol: 508 开头(沪) → .SH, 其余(180 深系) → .SZ。"""
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        return f"{c}.SH" if c.startswith("508") else f"{c}.SZ"
    return c.upper()


def _get_sync(path: str, params: dict, key: str) -> Optional[dict]:
    r = _session.get(f"{BASE}{path}", params=params,
                     headers={"x-api-key": key}, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {(r.text or '')[:120]}")
    return r.json()


async def _get(path: str, params: dict) -> Optional[dict]:
    key = await _api_key()
    if not key:
        raise RuntimeError("tickflow_api_key 未配置(设置页或 TICKFLOW_API_KEY)")
    return await asyncio.to_thread(_get_sync, path, params, key)


async def reits_quote(code: str) -> Optional[dict]:
    """REITs 实时行情。返回 {price, prev_close, change_pct, open, high, low, volume, amount,
    name, quote_time}; ext.change_pct 是小数(0.01=1%)要 ×100, 缺失时按 last/prev 反推。"""
    sym = reits_symbol(code)
    try:
        d = await _get("/v1/quotes", {"symbols": sym})
        rows = (d or {}).get("data") or []
        it = next((x for x in rows if str(x.get("symbol") or "").upper() == sym.upper()), None)
        if not it:
            return None
        last = float(it.get("last_price") or 0)
        if not last:
            return None
        prev = float(it.get("prev_close") or 0)
        ext = it.get("ext") or {}
        chg = ext.get("change_pct")
        if chg is not None:
            chg = round(float(chg) * 100, 3)
        elif prev:
            chg = round((last - prev) / prev * 100, 3)
        ts = it.get("timestamp")
        quote_time = ""
        if ts:
            try:
                from datetime import datetime, timedelta, timezone
                quote_time = (datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                              + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        return {
            "price": last,
            "prev_close": prev or None,
            "change_pct": chg,
            "open": float(it.get("open") or 0) or None,
            "high": float(it.get("high") or 0) or None,
            "low": float(it.get("low") or 0) or None,
            "volume": float(it.get("volume") or 0),
            "amount": float(it.get("amount") or 0),
            "name": ext.get("name") or "",
            "quote_time": quote_time,
        }
    except Exception as e:
        print(f"[tickflow] reits quote {code} 失败: {str(e)[:120]}")
        return None
