"""Massive REST **报价源**(美国期货 / 期权行情快照): https://massive.com/docs/rest/futures/overview

- 职责单一: 美国期货(CME/CBOT/COMEX/NYMEX)与美股/ETF/指数期权(OPRA)的**报价更新**
  (case ① 手动刷新 / 持仓冷启动), 走 REST 快照接口:
    * 期货:   GET /futures/v1/snapshot?ticker=<code>&apiKey=<key>
    * 期权:   GET /v3/snapshot/options/<underlying>/<contract>?apiKey=<key>
              GET /v3/quotes/<optionsTicker>?apiKey=<key>
  (注: 文档明确 "futures options coming soon", 当前不支持期货期权, 仅 equity/ETF/index 期权。)
- 认证: API Key 作为 **URL 查询参数 `apiKey`** 传递(非 HTTP 头, 与 futu/ths 不同)。
- 代码格式: 项目标准码直接透传(期货裸合约码如 EBEZ5 / CL2501; 期权 OCC 串如 AAPL250117C00200000)。
  不落库任何 massive 形态码 —— 响应解析后只回填标准码。
- 所有请求 best-effort: 未配置/网络错误/部分码无数据 → 该码不出现在返回里, 不连累持仓页。
- 访问策略: 仅手动刷新(force)与新增持仓冷启动(quote_latest 无行)才联网, 其余读 PG。

⚠️ 字段映射待定稿: Massive 文档子页(快照响应 JSON 结构)抓取不到确切字段名, 下方 `_dig`
   用多候选键做了容错解析; 拿到一份真实响应后需核对 `PRICE_KEYS` 等候选路径是否命中。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import requests as _requests

from services.quote_source import QuoteRow  # 规范层模型(仅依赖 pydantic, 无循环导入)

_BASE_DEFAULT = "https://api.massive.com"
_TIMEOUT = 10.0
_COOLDOWN_ON_429 = 60.0

_last_error: str = ""
_cooldown_until: float = 0.0


def last_error() -> str:
    return _last_error


def invalidate_ctx():
    """设置页改了 API Key 后清本模块限流冷却。"""
    global _cooldown_until
    _cooldown_until = 0.0


# ── 配置 ──────────────────────────────────────────────────────────────────────
async def _base_url() -> str:
    try:
        from database import get_config
        return (await get_config("MASSIVE_BASE_URL") or _BASE_DEFAULT).strip().rstrip("/")
    except Exception:
        return _BASE_DEFAULT


async def _api_key() -> str:
    try:
        from database import get_config
        return (await get_config("MASSIVE_API_KEY") or "").strip()
    except Exception:
        return ""


# ── 代码转换(massive 用裸合约码, 去掉项目标准码的 .市场 后缀) ──────────────────
def _to_massive_ticker(code: str) -> str:
    """标准码(ESZ5 / CL2501.US / AAPL250117C00200000) → massive 裸码(去 .US/.HK 后缀)。"""
    c = (code or "").strip().upper()
    if "." in c:
        body, mkt = c.rsplit(".", 1)
        if mkt in ("US", "HK", "SH", "SZ"):
            return body
    return c


def _is_option(code: str) -> bool:
    """粗判 OCC 期权合约串: 字母 + 6 位日期 + C/P + 8 位行权价(如 AAPL250117C00200000)。"""
    import re
    return bool(re.fullmatch(r"[A-Z]{1,6}\d{6}[CP]\d{8}", (code or "").strip().upper()))


# ── 字段容错解析(文档缺确切字段名, 多候选键兜底) ──────────────────────────────
_PRICE_KEYS = (("last", "price"), ("last_price",), ("last",), ("price",), ("close", "price"),
               ("close",), ("last_trade", "price"))
_PREV_KEYS = (("prev_close",), ("prevClose",), ("previous_close",), ("session", "prev_close"),
              ("prevClosePrice",))
_OHLC_KEYS = (("open",), ("open_price",), ("session", "open"),
              ("high",), ("high_price",), ("session", "high"),
              ("low",), ("low_price",), ("session", "low"))
_VOL_KEYS = (("volume",), ("vol",), ("session", "volume"))
_AMT_KEYS = (("turnover",), ("volume", "notional"), ("notional",), ("amount",))
_TS_KEYS = (("timestamp",), ("last_timestamp",), ("quote_timestamp",), ("sip_timestamp",))


def _dig(obj: dict, *path: str):
    """按候选路径逐层取值, 返回第一个非 None。路径元素可以是 dict 键或(对 list 取[0])。"""
    for p in path:
        cur = obj
        ok = True
        for k in p:
            if isinstance(cur, dict) and k in cur and cur[k] is not None:
                cur = cur[k]
            elif isinstance(cur, list) and cur and isinstance(cur[0], dict) and k in cur[0]:
                cur = cur[0][k]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


def _fnum(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quote_time(ts) -> str:
    """Massive 时间戳: 纳秒级 Unix 或 ISO8601 UTC → 北京时间字符串。"""
    if ts is None:
        return ""
    try:
        import datetime
        from zoneinfo import ZoneInfo
        if isinstance(ts, (int, float)):
            # 纳秒 → 秒
            s = ts / 1e9 if ts > 1e15 else ts
            dt = datetime.datetime.fromtimestamp(s, tz=ZoneInfo("UTC"))
        else:
            s = str(ts).replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
        return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


# ── 快照 ──────────────────────────────────────────────────────────────────────
def _snapshot_sync(codes: list[str], base: str, api_key: str) -> Optional[list[dict]]:
    global _last_error, _cooldown_until
    if time.time() < _cooldown_until:
        _last_error = "限流冷却中, 稍后再试"
        return None
    if not api_key:
        _last_error = "未配置 MASSIVE_API_KEY(设置页或 config.env)"
        return None
    futures, options = [], []
    for c in codes:
        (options if _is_option(c) else futures).append(c)
    out: list[dict] = []
    try:
        cli = _requests.Session()
        cli.timeout = _TIMEOUT
        # 期货: /futures/v1/snapshot?ticker=<csv>&apiKey=
        if futures:
            tmap = {_to_massive_ticker(c): c for c in futures}
            tickers = list(tmap.keys())
            r = cli.get(f"{base}/futures/v1/snapshot",
                        params={"ticker": ",".join(tickers), "apiKey": api_key})
            if r.status_code == 429:
                _cooldown_until = time.time() + _COOLDOWN_ON_429
                _last_error = "Massive 限流(429)"
                return None
            if r.status_code != 200:
                _last_error = f"HTTP {r.status_code}: {(r.text or '')[:120]}"
                return None
            j = r.json()
            for it in ((j.get("results") or j.get("snapshots") or []) if isinstance(j, dict) else []):
                mt = str(it.get("ticker") or it.get("symbol") or "").upper()
                std = tmap.get(mt)
                if not std:
                    continue
                out.append({"kind": "fut", "std": std, "raw": it})
        # 期权: /v3/snapshot/options/<underlying>/<contract>?apiKey=
        for c in options:
            t = _to_massive_ticker(c)
            # OCC: 前 1-6 字母=标的, 接着 6 位日期, C/P, 8 位行权价
            m = re.fullmatch(r"([A-Z]{1,6})(\d{6}[CP]\d{8})", t)
            if not m:
                continue
            r = cli.get(f"{base}/v3/snapshot/options/{m.group(1)}/{m.group(2)}",
                        params={"apiKey": api_key})
            if r.status_code == 429:
                _cooldown_until = time.time() + _COOLDOWN_ON_429
                _last_error = "Massive 限流(429)"
                return None
            if r.status_code != 200:
                continue
            j = r.json()
            if isinstance(j, dict) and (j.get("results") or j.get("snapshot")):
                raw = (j.get("results") or [j.get("snapshot")])[0] if j.get("results") else j.get("snapshot")
                out.append({"kind": "opt", "std": c, "raw": raw})
    except Exception as e:
        _last_error = str(e)[:160]
        return None
    _last_error = ""
    return out or None


def _row_to_quoteprice(kind: str, std: str, raw: dict) -> Optional[QuoteRow]:
    price = _fnum(_dig(raw, *_PRICE_KEYS))
    if not price or not std:
        return None
    prev = _fnum(_dig(raw, *_PREV_KEYS))
    # _OHLC_KEYS 是单键候选列表, 逐组取 open/high/low
    open_ = _fnum(_dig(raw, *_OHLC_KEYS[:1])) or _fnum(_dig(raw, *_OHLC_KEYS[1:2])) or _fnum(_dig(raw, *_OHLC_KEYS[2:3]))
    high = _fnum(_dig(raw, *_OHLC_KEYS[3:4])) or _fnum(_dig(raw, *_OHLC_KEYS[4:5])) or _fnum(_dig(raw, *_OHLC_KEYS[5:6]))
    low = _fnum(_dig(raw, *_OHLC_KEYS[6:7])) or _fnum(_dig(raw, *_OHLC_KEYS[7:8])) or _fnum(_dig(raw, *_OHLC_KEYS[8:9]))
    vol = _fnum(_dig(raw, *_VOL_KEYS)) or 0
    ts = _dig(raw, *_TS_KEYS)
    chg = (_fnum(_dig(raw, ("change_pct",), ("change_percent",), ("day_change_percent",)))
           if _dig(raw, ("change_pct",)) is not None else None)
    if chg is None and prev:
        chg = round((price - prev) / prev * 100, 3)
    return QuoteRow(
        code=std, market=("OPT" if kind == "opt" else "FUT"),
        name=str(raw.get("name") or raw.get("underlying") or "").strip(),
        price=price, open=open_, high=high, low=low, prev_close=prev,
        volume=vol or 0,
        change_pct=chg,
        currency="USD",
        quote_time=_quote_time(ts),
        source="massive",
        sub_assets_type=("us_options" if kind == "opt" else "us_futures"),
    )


async def snapshot_rows(codes: list[str]) -> list[QuoteRow]:
    """massive 报价源适配器: 标准码 → 规范 QuoteRow。

    契约: 不抛异常 —— 未配置/网络错/部分码无数据, 表现为该码不在返回里; 派发层据此把缺码
    交给链上下一个源(兜底)。US 期货/期权直接回填 sub_assets_type=us_futures/us_options,
    避免 FUT/OPT→cn_futures 误标(SUBTYPE_BY_MARKET 默认只认 CN)。
    """
    clean = [str(c or "").strip().upper() for c in codes if str(c or "").strip()]
    if not clean:
        return []
    base = await _base_url()
    api_key = await _api_key()
    items = await asyncio.to_thread(_snapshot_sync, clean, base, api_key)
    if not items:
        return []
    out: list[QuoteRow] = []
    for it in items:
        try:
            q = _row_to_quoteprice(it["kind"], it["std"], it["raw"])
            if q:
                out.append(q)
        except Exception:
            continue
    return out


class _MassiveSource:
    name = "massive"
    asset_types = ("FUT", "OPT")     # 由 assets_classify.us_futures/us_options 主源指派, 实际只服务美期/美期权

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        return await snapshot_rows(codes)


SOURCE = _MassiveSource()
