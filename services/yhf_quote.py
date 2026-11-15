"""yhf(Yahoo Finance API, yfapi.net)REST **报价源**: https://yfapi.net/v6/finance/quote

- 职责单一: 已录入资产的**报价更新**(case ① 手动刷新 / 持仓冷启动), 走 REST 快照接口:
    GET /v6/finance/quote?region=US&lang=en&symbols=AAPL,BABA
  认证: API Key 走 **HTTP 头 X-API-KEY**(与 futu/ths 同, 非 URL 参数)。
- 代码格式: yhf symbol 同为「ticker.suffix」形状, 但后缀是 yhf 口径(美股裸 ticker 无后缀、
  沪 SS / 深 SZ / 港 HK...) —— 各交易所的 yhf 后缀在 exchange 表 **yhf_suffix** 列人工维护,
  经 symbol_dict.exchange_mic → exchange_map.resolve_source_code(mic, "yhf") 解析; 底稿
  查不到 mic 的按标准码后缀现算(suffix_to_mic, US 跨所歧义 → None)。拼接收在本模块:
  请求前把标准码翻成 yhf symbol, 响应码反查回标准码 —— yhf symbol 绝不落库。
- 字段映射: 响应字段很多, 本源只取**报价语义**字段进规范层(price/open/high/low/
  prev_close/volume/change_pct/currency/quote_time); 盘后价/52 周/市值/PE 等 yhf 特有
  字段放 QuoteRow.info 透传到前端 —— quote_latest 落库列固定(QUOTE_COLS), 特有字段不进库。
- 所有请求 best-effort: 未配置/网络错误/部分码无数据 → 该码不出现在返回里, 不连累持仓页;
  429 进 60s 冷却(免费配额有限)。
- 访问策略(与 futu/tickflow 一致): 只有手动刷新(force)和新增持仓冷启动(quote_latest 无行)
  才联网, 其余一律读 PG(market.quote_latest)。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

import requests as _requests

from services.quote_source import QuoteRow  # 规范层模型(仅依赖 pydantic, 无循环导入)

_BASE = "https://yfapi.net"
_REGION = "US"           # v6/quote 的 region 只影响 locale 语义, 查全球码都用 US 即可
_LANG = "en"
_TIMEOUT = 10
_BATCH = 50              # 单次 GET symbols 上限(URL 长度安全, 自动分块)
_COOLDOWN_ON_429 = 60.0

_session = _requests.Session()
_session.trust_env = False

_key_cache: list = [None, 0.0]       # (key, ts)
_last_error: str = ""
_cooldown_until: float = 0.0         # 限流冷却截止


def last_error() -> str:
    return _last_error


def invalidate_key_cache():
    """设置页改了 API Key 后清 key 缓存 + 限流冷却。"""
    global _cooldown_until
    _key_cache[0], _key_cache[1] = None, 0.0
    _cooldown_until = 0.0


async def _api_key() -> str:
    """优先设置页保存的(app_config 全局键 yhf_api_key), 其次 config.env(YHF_API_KEY)。缓存 60s。"""
    if _key_cache[1] and time.time() - _key_cache[1] < 60:
        return _key_cache[0] or ""
    key = ""
    try:
        from database import get_config
        key = (await get_config("yhf_api_key") or "").strip()
    except Exception:
        pass
    if not key:
        key = (os.environ.get("YHF_API_KEY") or "").strip()   # config.env 部署配置
    _key_cache[0], _key_cache[1] = key, time.time()
    return key


# ── 代码转换(yhf 口径后缀, 绝不落库) ──────────────────────────────────────────
async def _yhf_symbol_map(codes: list[str]) -> dict[str, str]:
    """标准码 → yhf symbol(ticker + exchange.yhf_suffix 拼接)。

    拼接口径(exchange 表单列维护, exchange_map 按列名子串匹配 yhf_suffix):
      ① symbol_dict.exchange_mic → resolve_source_code(mic, "yhf")(已登记标的, 权威);
      ② 底稿无 mic/未登记 → 标准码后缀现算 mic(suffix_to_mic: SH/SZ/HK/BJ 唯一确定,
         US 跨所歧义 → None);
      ③ 兜底: .US 裸 ticker(yhf 美股无后缀, 免配置); 其余无 yhf_suffix 配置的码跳过 ——
         不猜后缀(沪 SS/深 SZ/港 HK 等是 yhf 特有口径, 猜错必 404)。
    """
    std = sorted({str(c or "").strip().upper() for c in codes} - {""})
    if not std:
        return {}
    ticker_mic: dict[str, tuple[str, str | None]] = {}       # std → (ticker, exchange_mic)
    try:
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT code, ticker, exchange_mic FROM symbol_dict WHERE UPPER(code) = ANY($1)", (std,))
            for code, ticker, mic in await cur.fetchall():
                c = str(code or "").strip().upper()
                t = str(ticker or "").strip().upper()
                if c and t:
                    ticker_mic[c] = (t, (str(mic).strip() or None) if mic else None)
        finally:
            await db.close()
    except Exception as e:
        print(f"[yhf] symbol_dict 码表查询失败(后缀现算兜底): {e}")

    from services.exchange_map import resolve_source_code, suffix_to_mic
    suf_cache: dict[str, str] = {}                           # mic → yhf_suffix

    async def _yhf_suffix(mic: str | None) -> str:
        mic = (mic or "").strip()
        if not mic:
            return ""
        if mic not in suf_cache:
            suf_cache[mic] = str(await resolve_source_code(mic, "yhf") or "").strip().upper()
        return suf_cache[mic]

    out: dict[str, str] = {}
    for c in std:
        body = c.rsplit(".", 1)[0] if "." in c else ""
        ticker, mic = ticker_mic.get(c, ("", None))
        t = ticker or body
        if not t:
            continue
        suffix = await _yhf_suffix(mic)
        if suffix:
            out[c] = f"{t}.{suffix}"
            continue
        mkt = c.rsplit(".", 1)[-1] if "." in c else ""
        if mkt == "US":
            out[c] = t                                  # yhf 美股裸 ticker, 免配置
            continue
        mic = await suffix_to_mic(mkt)                  # 未登记/无 mic → 标准码后缀现算(US 之外唯一确定的段位)
        suffix = await _yhf_suffix(mic)
        if suffix:
            out[c] = f"{t}.{suffix}"
        # 否则跳过: 不猜后缀(沪 SS/深 SZ/港 HK/日 .T 等是 yhf 特有口径, 猜错必 404 或错配)
    return out


# ── HTTP ───────────────────────────────────────────────────────────────────
def _quotes_sync(symbols: list[str], key: str) -> list[dict]:
    """批量快照(自动分块)。失败抛异常, 由调用方转"该批无数据"(源链按码兜底)。"""
    global _cooldown_until, _last_error
    if time.time() < _cooldown_until:
        raise RuntimeError("限流冷却中, 稍后再试")
    out: list[dict] = []
    for i in range(0, len(symbols), _BATCH):
        chunk = symbols[i:i + _BATCH]
        r = _session.get(f"{_BASE}/v6/finance/quote",
                         params={"region": _REGION, "lang": _LANG, "symbols": ",".join(chunk)},
                         headers={"accept": "application/json", "X-API-KEY": key},
                         timeout=_TIMEOUT)
        if r.status_code == 429:
            _cooldown_until = time.time() + _COOLDOWN_ON_429
            _last_error = "yhf 限流(429)"
            raise RuntimeError(_last_error)
        if r.status_code != 200:
            _last_error = f"HTTP {r.status_code}: {(r.text or '')[:120]}"
            raise RuntimeError(_last_error)
        qr = (r.json() or {}).get("quoteResponse") or {}
        if qr.get("error"):
            _last_error = f"quoteResponse.error: {str(qr['error'])[:120]}"
            raise RuntimeError(_last_error)
        out.extend(qr.get("result") or [])
    _last_error = ""
    return out


# ── 解析 ───────────────────────────────────────────────────────────────────
# 标准码后缀 → (market 分组, 兜底币种); 币种以响应 currency 字段为准, 这里只兜缺省。
_MARKET_BY_SUFFIX = {"SH": ("A", "CNY"), "SZ": ("A", "CNY"), "BJ": ("A", "CNY"),
                     "HK": ("HK", "HKD"), "US": ("US", "USD")}

# yhf 特有字段(info 透传位, 不进 quote_latest): camelCase → 蛇形, 报价页可能用到的口径。
_INFO_KEYS = {
    "marketState": "market_state",
    "fullExchangeName": "full_exchange_name",
    "exchangeTimezoneName": "exchange_timezone",
    "postMarketPrice": "post_market_price",
    "postMarketChange": "post_market_change",
    "postMarketChangePercent": "post_market_change_pct",
    "postMarketTime": "post_market_time",          # 秒级 ts → 北京时间(endswith("Time") 分支)
    "preMarketPrice": "pre_market_price",
    "preMarketChangePercent": "pre_market_change_pct",
    "fiftyTwoWeekHigh": "fifty_two_week_high",
    "fiftyTwoWeekLow": "fifty_two_week_low",
    "marketCap": "market_cap",
    "trailingPE": "trailing_pe",
    "forwardPE": "forward_pe",
    "epsTrailingTwelveMonths": "eps_ttm",
    "dividendYield": "dividend_yield",
    "sharesOutstanding": "shares_outstanding",
    "bookValue": "book_value",
    "averageDailyVolume3Month": "avg_volume_3m",
}


def _fnum(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts_bj(ts) -> str:
    """秒级 Unix 时间戳 → 北京时间 'YYYY-MM-DD HH:MM:SS'。"""
    if not ts:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return (datetime.fromtimestamp(int(ts), tz=ZoneInfo("Asia/Shanghai"))
                .strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        return ""


def _norm_sym(sym: str) -> str:
    """yhf symbol 匹配键: 去前导零(数字主体)。Yahoo 会把请求码归一化(00700.HK 回 0700.HK),
    反查两侧都归一化, 命中不受回显形态影响; 归一化是幂等的, 原样回显同样命中。"""
    s = (sym or "").strip().upper()
    if "." not in s:
        return s
    body, _, suf = s.rpartition(".")
    if body.isdigit():
        body = body.lstrip("0") or "0"
    return f"{body}.{suf}"


def _parse_quote(it: dict, std_code: str) -> Optional[QuoteRow]:
    """yhf quote 报文 → 规范 QuoteRow(std_code 由请求前建的映射反查, 不认 yhf 原始码)。"""
    price = _fnum(it.get("regularMarketPrice")) or _fnum(it.get("fulldayPrice"))
    if not price or not std_code:
        return None
    prev = _fnum(it.get("regularMarketPreviousClose"))
    chg = _fnum(it.get("regularMarketChangePercent"))          # 响应即百分数口径(0.39=0.39%)
    if chg is None and prev:
        chg = round((price - prev) / prev * 100, 3)
    body, _, suf = std_code.partition(".")
    market, cur_default = _MARKET_BY_SUFFIX.get(suf, (suf or "US", "USD"))
    info = {}
    for src, dst in _INFO_KEYS.items():
        v = it.get(src)
        if v is not None:
            info[dst] = _ts_bj(v) if src.endswith("Time") else v
    return QuoteRow(
        code=std_code, market=market,
        name=str(it.get("shortName") or it.get("longName") or it.get("displayName") or "").strip(),
        price=price,
        open=_fnum(it.get("regularMarketOpen")),
        high=_fnum(it.get("regularMarketDayHigh")),
        low=_fnum(it.get("regularMarketDayLow")),
        prev_close=prev,
        volume=_fnum(it.get("regularMarketVolume")) or 0,
        amount=0,                                             # 响应无成交额口径, 留 0
        change_pct=chg,
        currency=str(it.get("currency") or cur_default).strip().upper() or cur_default,
        quote_time=_ts_bj(it.get("regularMarketTime")),
        source="yhf", info=info)


# ── 源适配器(规范层, 见 services/quote_source.py) ─────────────────────────
async def snapshot_rows(codes: list[str]) -> list[QuoteRow]:
    """yhf 报价源适配器: 标准码(600406.SH / 00700.HK / AAPL.US) → 规范 QuoteRow。

    代码转换收在本模块: 请求前经 _yhf_symbol_map(exchange.yhf_suffix 拼接, .US 裸码兜底)
    翻成 yhf symbol, 响应码反查回标准码 —— yhf symbol 绝不落库。
    契约: 不抛异常 —— 未配置 key / 网络失败 / 部分码无数据, 都表现为该码不在返回里,
    派发层据此把缺码交给链上的下一个源(兜底)。
    """
    clean = []
    for c in codes:
        c = str(c or "").strip().upper()
        if not c:
            continue
        if c.startswith(("HK.", "US.")):     # 旧前缀形态(迁移前存量) → 标准形态, 同 tickflow
            c = f"{c[3:]}.{c[:2]}"
        clean.append(c)
    if not clean:
        return []
    key = await _api_key()
    if not key:
        return []                # 未配置 key: 静默让位链上下一个源(与 futu/tickflow 未配置同语义)
    sym_map = await _yhf_symbol_map(clean)
    if not sym_map:
        return []
    try:
        items = await asyncio.to_thread(_quotes_sync, list(sym_map.values()), key)
    except Exception as e:
        print(f"[yhf] snapshot 失败: {str(e)[:120]}")
        return []
    by_sym = {_norm_sym(s): c for c, s in sym_map.items()}
    out: list[QuoteRow] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            std = by_sym.get(_norm_sym(str(it.get("symbol") or "")))
            q = _parse_quote(it, std or "")
            if q:
                out.append(q)
        except Exception:
            continue
    return out


class _YhfSource:
    name = "yhf"
    asset_types = ("A", "HK", "US")     # 由 assets_classify 各小类 main/back_quote_source 指派生效

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        return await snapshot_rows(codes)


SOURCE = _YhfSource()
