"""TickFlow 数据源(https://docs.tickflow.org/zh-Hans): A股/ETF/港/美股实时行情 + REITs。

- 接口: GET /v1/quotes?symbols=600000.SH,00700.HK,AAPL.US(header x-api-key), 支持批量逗号分隔。
- 代码映射: 项目标准码(600406.SH / HK.00700 / US.AAPL) ↔ tickflow symbol(600406.SH / 00700.HK / AAPL.US)
  —— A股段位完全一致原样透传, 港美前缀/后缀互换。
- key/host: 优先设置页(app_config 全局键 tickflow_api_key / tickflow_api_host),
  其次 .env(TICKFLOW_API_KEY / TICKFLOW_API_HOST 或 TICKFLOW_BASE_URL) → config.py。
  免费 host(free-api.tickflow.org)无实时行情, 默认打付费 api.tickflow.org。
- 源适配器: QuoteSource 协议(见 services/quote_source.py), 契约=绝不抛异常 ——
  未配置 key / 网络失败 / 部分码无数据都表现为返回列表里没有该码, 派发层据此按码兜底。
"""
from __future__ import annotations
import asyncio
import os
import time
from typing import Optional

import requests as _requests
import config as _config

from services.quote_source import QuoteRow  # 规范层模型(仅依赖 pydantic, 无循环导入)

BASE = "https://api.tickflow.org"
_TIMEOUT = 10
_BATCH = 100             # 单次 GET symbols 上限(URL 长度安全)
_FREE_HOST_MARK = "free-api"   # 免费服务只给日K, 不当实时源用(打 quotes 必失败, 交给上层兜底)

_session = _requests.Session()
_session.trust_env = False

_key_cache: list = [None, 0.0]      # (key, ts)
_host_cache: list = [None, 0.0]     # (host, ts)


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


async def _host() -> str:
    """服务地址: app_config(tickflow_api_host) → .env(TICKFLOW_API_HOST/TICKFLOW_BASE_URL) → config → 默认。缓存 60s。"""
    if time.time() - _host_cache[1] < 60 and _host_cache[0] is not None:
        return _host_cache[0]
    host = ""
    try:
        from database import get_config
        host = (await get_config("tickflow_api_host") or "").strip()
    except Exception:
        pass
    if not host:
        host = (os.environ.get("TICKFLOW_API_HOST", "")
                or os.environ.get("TICKFLOW_BASE_URL", "")
                or getattr(_config, "tickflow_api_host", "") or "").strip()
    host = (host or BASE).rstrip("/")
    _host_cache[0], _host_cache[1] = host, time.time()
    return host


def invalidate_key_cache():
    _key_cache[0] = None
    _key_cache[1] = 0.0
    _host_cache[0] = None
    _host_cache[1] = 0.0


# ── 代码映射 ───────────────────────────────────────────────────────────────
def to_tf_symbol(code: str) -> str:
    """项目标准码 → tickflow symbol。tickflow 与标准码同构「代码.市场」: A股(600406.SH)、
    港股(00700.HK)、美股(SNDK.US)原样透传; 旧前缀形态(HK.00700/US.AAPL, 迁移前存量)
    翻成标准形态; 不支持的形态(期货 thscode/.OF 基金等)返回空串。"""
    c = (code or "").strip().upper()
    if not c:
        return ""
    # 旧形态 → 标准形态(tickflow 与标准码同构)
    if c.startswith("HK."):
        body = c[3:]
        return f"{body}.HK" if body else ""
    if c.startswith("US."):
        body = c[3:]
        return f"{body}.US" if body else ""
    if "." in c:
        body, suf = c.split(".", 1)
        if suf in ("HK", "US") and body:
            return c                                # 标准形态: 00700.HK / SNDK.US
        # A股/ETF/北交: 6位数字主体 + SH/SZ/BJ 后缀 —— 与 tickflow 格式一致
        if len(body) == 6 and body.isdigit() and suf in ("SH", "SZ", "BJ"):
            return c
        return ""           # 期货 thscode(CU2601.SHF)/基金(.OF)等不归 tickflow
    return ""               # 裸码不带市场后缀, 无法确定市场


def _from_tf_symbol(sym: str) -> str:
    """tickflow symbol → 项目标准码。tickflow 与标准码同构「代码.市场」, 原样透传
    (港股主体补前导零); 未知后缀返回空串。"""
    s = (sym or "").strip().upper()
    body, _, suf = s.partition(".")
    if not body or not suf:
        return ""
    if suf == "HK":
        return f"{body.zfill(5)}.HK"
    if suf == "US":
        return f"{body}.US"
    if suf in ("SH", "SZ", "BJ") and len(body) == 6 and body.isdigit():
        return s
    return ""


_MARKET_BY_SUFFIX = {"SH": ("A", "CNY"), "SZ": ("A", "CNY"), "BJ": ("A", "CNY"),
                     "HK": ("HK", "HKD"), "US": ("US", "USD")}


# ── HTTP ───────────────────────────────────────────────────────────────────
def _get_sync(path: str, params: dict, key: str, host: str) -> Optional[dict]:
    headers = {"x-api-key": key} if key else {}
    r = _session.get(f"{host}{path}", params=params, headers=headers, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {(r.text or '')[:120]}")
    return r.json()


async def _get(path: str, params: dict) -> Optional[dict]:
    host = await _host()
    key = await _api_key()
    if not key and _FREE_HOST_MARK not in host:
        raise RuntimeError("tickflow_api_key 未配置(设置页或 TICKFLOW_API_KEY)")
    return await asyncio.to_thread(_get_sync, path, params, key, host)


def _quotes_sync(symbols: list[str], key: str, host: str) -> list[dict]:
    """批量实时行情(自动分块 GET /v1/quotes)。失败抛异常, 由调用方决定兜底方式。"""
    out: list[dict] = []
    for i in range(0, len(symbols), _BATCH):
        chunk = symbols[i:i + _BATCH]
        d = _get_sync("/v1/quotes", {"symbols": ",".join(chunk)}, key, host) or {}
        out.extend(d.get("data") or [])
    return out


# ── 解析 ───────────────────────────────────────────────────────────────────
def _quote_time_bj(ts_ms) -> str:
    """毫秒时间戳 → 北京时间 'YYYY-MM-DD HH:MM:SS'。"""
    if not ts_ms:
        return ""
    try:
        from datetime import datetime, timedelta, timezone
        return (datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
                + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _parse_quote(it: dict) -> Optional[dict]:
    """tickflow Quote 报文 → 统一 quote dict。

    ext.change_pct / ext.turnover_rate 是小数(0.01 = 1%), 统一 ×100 成百分数口径。
    """
    sym = str(it.get("symbol") or "").upper()
    if not sym:
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
    tr = ext.get("turnover_rate")
    tr = round(float(tr) * 100, 3) if tr is not None else None
    body, _, suf = sym.partition(".")
    market, currency = _MARKET_BY_SUFFIX.get(suf, ("A", "CNY"))
    return {
        "symbol": sym,
        "code": _from_tf_symbol(sym),
        "market": market,
        "currency": currency,
        "price": last,
        "prev_close": prev or None,
        "change_pct": chg,
        "open": float(it.get("open") or 0) or None,
        "high": float(it.get("high") or 0) or None,
        "low": float(it.get("low") or 0) or None,
        "volume": float(it.get("volume") or 0),
        "amount": float(it.get("amount") or 0),
        "name": ext.get("name") or "",
        "quote_time": _quote_time_bj(it.get("timestamp")),
        "session": it.get("session") or "",
        "turnover_rate": tr,
    }


async def quotes(symbols: list[str]) -> list[dict]:
    """批量实时行情(交易时段实时更新)。返回解析后的 quote dict 列表; 网络/鉴权失败抛异常。"""
    syms = [str(s or "").strip().upper() for s in symbols if str(s or "").strip()]
    if not syms:
        return []
    host = await _host()
    key = await _api_key()
    if not key and _FREE_HOST_MARK not in host:
        raise RuntimeError("tickflow_api_key 未配置(设置页或 TICKFLOW_API_KEY)")
    items = await asyncio.to_thread(_quotes_sync, syms, key, host)
    out = []
    for it in items:
        try:
            q = _parse_quote(it)
            if q:
                out.append(q)
        except Exception:
            continue
    return out


async def reits_quote(code: str) -> Optional[dict]:
    """REITs 实时行情。返回 {price, prev_close, change_pct, open, high, low, volume, amount,
    name, quote_time}; 失败返回 None(不抛, 与基金报价链的 best-effort 约定一致)。"""
    sym = reits_symbol(code)
    try:
        for q in await quotes([sym]):
            if q["symbol"] == sym.upper():
                return {"price": q["price"], "prev_close": q["prev_close"],
                        "change_pct": q["change_pct"], "open": q["open"], "high": q["high"],
                        "low": q["low"], "volume": q["volume"], "amount": q["amount"],
                        "name": q["name"], "quote_time": q["quote_time"]}
        return None
    except Exception as e:
        print(f"[tickflow] reits quote {code} 失败: {str(e)[:120]}")
        return None


def reits_symbol(code: str) -> str:
    """REITs 6位代码 → tickflow symbol: 508 开头(沪) → .SH, 其余(180 深系) → .SZ。"""
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        return f"{c}.SH" if c.startswith("508") else f"{c}.SZ"
    return c.upper()


# ── 源适配器(规范层, 见 services/quote_source.py) ─────────────────────────
async def snapshot_rows(codes: list[str]) -> list[QuoteRow]:
    """tickflow 源适配器: 标准码(A/HK/US) → 规范 QuoteRow。

    契约: 不抛异常 —— 未配置 key / 网络失败 / 部分码无数据, 都表现为该码不在返回里,
    派发层据此把缺码交给链上的下一个源(sina legacy)。
    """
    pairs: list[tuple[str, str]] = []       # (内部标准码, tickflow symbol)
    seen: set[str] = set()
    for c in codes:
        c = str(c or "").strip().upper()
        if not c or c in seen:
            continue
        seen.add(c)
        sym = to_tf_symbol(c)
        if sym:
            pairs.append((c, sym))
    if not pairs:
        return []
    host = await _host()
    key = await _api_key()
    if not key and _FREE_HOST_MARK not in host:
        return []            # 未配置 key: 静默让位链上下一个源(与 futu 未配置同语义)
    try:
        items = await asyncio.to_thread(_quotes_sync, [s for _, s in pairs], key, host)
    except Exception as e:
        print(f"[tickflow] snapshot 失败: {str(e)[:120]}")
        return []
    by_sym = {s: c for c, s in pairs}
    out: list[QuoteRow] = []
    for it in items:
        try:
            q = _parse_quote(it)
            code = by_sym.get((q or {}).get("symbol") or "")
            if not q or not code:
                continue
            info: dict = {}
            if q.get("turnover_rate") is not None:
                info["turnover_rate"] = q["turnover_rate"]
            if q.get("session"):
                info["session"] = q["session"]
            out.append(QuoteRow(
                code=code, market=q["market"], name=q["name"] or "", price=float(q["price"]),
                open=q.get("open"), high=q.get("high"), low=q.get("low"),
                prev_close=q.get("prev_close"),
                volume=q.get("volume") or 0, amount=q.get("amount") or 0,
                change_pct=q.get("change_pct"), currency=q["currency"],
                quote_time=q.get("quote_time") or "", source="tickflow", info=info))
        except Exception:
            continue
    return out


class _TickFlowSource:
    name = "tickflow"
    asset_types = ("A", "HK", "US")

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        return await snapshot_rows(codes)


SOURCE = _TickFlowSource()
