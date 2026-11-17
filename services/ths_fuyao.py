"""同花顺 fuyao 数据源 (https://fuyao.aicubes.cn): 期货/期权合约与行情。

- 鉴权: X-api-key(设置页/app_config 全局键 ths_api_key, 或 .env THS_API_KEY)。
- 合约标识: 完整同花顺代码 thscode(如 CU2601.SHF), 直接作为 holdings.stock_code。
- 行情: 日K(/prices/daily)为主 —— 末根=今天→现价取末根收盘、昨收取倒数第二根;
  末根不是今天→才调 /prices/intraday 拿盘中现价(省 API 配额)。
  30s 内存缓存 + write-through market.quote_latest(PG 优先快路径的数据源, 顺带自动登记底稿)。
- 所有请求 best-effort 包装: 上游挂了返回 None, 不连累持仓页。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional

import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter

from services.quote_source import QuoteRow  # 规范层模型(仅依赖 pydantic, 无循环导入)

BASE = "https://fuyao.aicubes.cn"
_SESSION = _requests.Session()
_SESSION.trust_env = False          # 不吃系统代理


class _DefaultCertsAdapter(_HTTPAdapter):
    """不给连接挂 certifi 大包(137张), 让 urllib3 回退 load_default_certs(Windows 证书库)。

    本机定位过: requests 的 verify=True 路径会给连接挂 certifi 包, 在部分 TUN 代理
    环境下 TLS 握手会被中途掐断(SSLEOFError); 不挂包走系统证书库则稳定通过。
    证书验证仍然开启(CERT_REQUIRED + check_hostname), 只是信任源换成系统库。
    """

    def cert_verify(self, conn, url, verify, cert):
        super().cert_verify(conn, url, verify, cert)
        if verify is True:
            conn.ca_certs = None
            conn.ca_cert_dir = None


_SESSION.mount("https://", _DefaultCertsAdapter())


# thscode 形态: 合约主体.交易所后缀(字母 2-5), 如 CU2601.SHF / 10005738.SH / 850002.TI。
# HK.00700/US.AAPL 后缀是数字或带 US./HK. 前缀, 天然不匹配。
_THS_RE = re.compile(r"^[A-Za-z0-9-]{2,32}\.[A-Z]{2,5}$")   # 主体允许连字符(期权拼码 TICKER-C/P-STRIKE)


def _currency_of_thscode(code: str) -> str:
    """同花顺 thscode 后缀 → 计价币种: .HK→HKD / .US→USD / 国内期货交易所→CNY。
    期货/期权行情不能统一标 CNY(否则 HK/US 衍生品被当人民币, 后端不折汇)。"""
    c = (code or "").upper()
    if c.endswith(".HK"):
        return "HKD"
    if c.endswith(".US"):
        return "USD"
    return "CNY"


def is_thscode(code: str) -> bool:
    c = (code or "").strip()
    if not c or c.startswith(("HK.", "US.")):
        return False
    m = _THS_RE.match(c)
    if not m:
        return False
    body, _, suffix = c.partition(".")
    suffix = suffix.upper()
    # 港/美股标准码(00700.HK / SNDK.US)与 A股/ETF(600406.SH 6位主体)都不是期货合约
    if suffix in ("HK", "US"):
        return False
    if len(body) == 6 and body.isdigit():
        return False
    return True


def ths_kind(code: str) -> str:
    """thscode → 'fut' | 'opt'。必须与 _looks_option 同口径 —— 它决定端点(/api/options
    vs /futures)、QuoteRow.market、行情入库 sub_assets_type。旧逻辑只认 .SH 开头 10/90
    的 ETF 期权, 认不出 MO2611-P-6500.CFE 这类股指/商品期权拼码 → 被误判成期货、小类落
    成 cn_futures。统一委托 _looks_option(认 -C-/-P- 拼码 + .SH/.SZ 数字 ETF 期权)。"""
    return "opt" if _looks_option(code) else "fut"


_api_key_cache: list = [None, 0.0]      # (key, ts)


async def _api_key() -> str:
    """优先设置页保存的(app_config 全局键), 其次 .env/config。缓存 60s。"""
    if time.time() - _api_key_cache[1] < 60 and _api_key_cache[0] is not None:
        return _api_key_cache[0]
    key = ""
    try:
        from database import get_config
        key = (await get_config("ths_api_key") or "").strip()
    except Exception:
        pass
    if not key:
        key = (os.environ.get("THS_API_KEY") or "").strip()   # config.env 部署配置
    _api_key_cache[0], _api_key_cache[1] = key, time.time()
    return key


def invalidate_key_cache():
    _api_key_cache[0] = None
    _api_key_cache[1] = 0.0


def _get_sync(path: str, params: dict, key: str) -> Optional[dict]:
    url = f"{BASE}{path}"
    r = _SESSION.get(url, params=params, headers={"X-api-key": key}, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {(r.text or '')[:120]}")
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"api code={d.get('code')}: {d.get('message', '')[:120]}")
    return d.get("data")


async def corp_actions_sync(thscode: str) -> Optional[list[dict]]:
    """A 股公司行为(除权除息事件, fuyao corporate-actions):
    [{ex_date, per_share_cash(税前), per_share_bonus(送股比例)}], 除权日降序。拉不到 → None。"""
    d = await _get("/api/a-share/corporate-actions/adjustment-factors", {"thscode": thscode})
    if not d:
        return None
    out = []
    for it in d.get("item") or []:
        ms = it.get("ex_date_ms")
        if not ms:
            continue
        # ex_date_ms = Asia/Shanghai 00:00 的毫秒时间戳, 直接按 +08:00 取日
        ex = datetime.datetime.fromtimestamp(int(ms) / 1000,
                                             tz=datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
        out.append({"ex_date": ex,
                    "per_share_cash": float(it.get("dividend_per_share") or 0),
                    "per_share_bonus": float(it.get("per_share_bonus") or 0)})
    return out


async def _get(path: str, params: dict) -> Optional[dict]:
    key = await _api_key()          # 每次请求先解析 key(DB 优先, config 兜底, 60s 缓存)
    return await asyncio.to_thread(_get_sync, path, params, key)


# ---- 合约目录 ----

async def contracts_list(asset_type: str = "futures", limit: int = 1000, offset: int = 0) -> list[dict]:
    """统一合约目录(/api/meta/tickers/list)。asset_type: futures | options。"""
    d = await _get("/api/meta/tickers/list",
                   {"asset_type": asset_type, "limit": max(1, min(int(limit), 1000)), "offset": max(0, int(offset))})
    return (d or {}).get("item") or []


async def contracts_search(q: str, asset_type: str = "") -> list[dict]:
    """标的检索(/api/meta/tickers/search)。q=关键词(代码/名称), 可选 asset_type 过滤。"""
    params = {"q": (q or "").strip()}
    if asset_type:
        params["asset_type"] = asset_type
    d = await _get("/api/meta/tickers/search", params)
    items = (d or {}).get("item") or (d or {}).get("items") or []
    if isinstance(items, list) and items and not isinstance(items[0], dict):
        return []       # 形状异常防御
    return items


async def contract_detail(thscode: str) -> Optional[dict]:
    d = await _get("/api/futures/contracts/detail", {"thscode": thscode})
    return d


# ---- 行情 ----

_quote_cache: dict[str, tuple[Optional[dict], float]] = {}
_QUOTE_TTL = 30
_quote_fail_cache: dict[str, tuple[float, float]] = {}   # (ts, ttl) 负缓存: 失效合约(到期/退市/代码错)不再反复打 API
_name_cache: dict[str, tuple[str, float]] = {}
_NAME_TTL = 6 * 3600


def _parse_option_code(thscode: str):
    """期权拼码 → (品种, 月份YYMM, C/P, 行权价)。MO2611-P-6500.CFE → ('MO','2611','P','6500')。
    非拼码(期货/ETF期权数字码)返回 None。"""
    body = (thscode or "").strip().upper().partition(".")[0]
    m = re.match(r"^([A-Z]+)(\d{4})-([CP])-(\d+(?:\.\d+)?)$", body)
    return (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


async def _name_of(thscode: str) -> str:
    """合约名称: 底稿(symbol_dict 精确码) → 合约详情 → 期权拼码本地构造(长缓存)。
    期权是开仓现拼的合约码(MO2611-P-6500.CFE), symbol_dict 只有品种行(MO)、没有完整合约码,
    合约详情端点又是期货的(/api/futures)查不到期权 —— 两条 lookup 都落空导致 name 为空。
    最后按拼码本地构造「品种名 月份 认沽/认购 行权价」, 品种名查 symbol_dict 的 ticker 行。"""
    hit = _name_cache.get(thscode)
    if hit and time.time() - hit[1] < _NAME_TTL:
        return hit[0]
    parts = _parse_option_code(thscode)
    vrow = None
    try:
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute("SELECT name FROM symbol_dict WHERE UPPER(code) = ? LIMIT 1", (thscode.upper(),))
            row = await cur.fetchone()
            if row and row["name"]:
                _name_cache[thscode] = (row["name"], time.time())
                return row["name"]
            # 期权拼码: 用品种行(按 ticker/code=MO)的中文名拼完整合约名。
            # 同一品种码可能在多交易所各有品种行(MO.DCE 豆粕期权 / MO.CFE 中证1000期权),
            # 按合约后缀优先匹配, 避免张冠李戴。
            if parts:
                variety, _, _, _ = parts
                _exch = thscode.upper().partition(".")[2]
                cur = await db.execute(
                    "SELECT name FROM symbol_dict "
                    "WHERE UPPER(ticker) = ? OR UPPER(code) = ? "
                    "ORDER BY (UPPER(COALESCE(suffix,'')) = ?) DESC LIMIT 1",
                    (variety, variety, _exch))
                vrow = await cur.fetchone()
        finally:
            await db.close()
    except Exception:
        pass
    # 期权拼码本地构造名: 即使底稿查不到, 也用裸品种码拼出非空名称(不依赖期货详情端点)
    if parts:
        variety, month, cp, strike = parts
        vname = vrow["name"] if vrow and vrow["name"] else variety
        cp_cn = "认沽" if cp == "P" else "认购"
        nm = f"{vname} {month} {cp_cn} {strike}"
        _name_cache[thscode] = (nm, time.time())
        return nm
    try:
        d = await contract_detail(thscode)
        nm = (d or {}).get("name") or ""
        if nm:
            _name_cache[thscode] = (nm, time.time())
        return nm
    except Exception:
        return ""


async def quote(thscode: str) -> Optional[dict]:
    """期货/期权最新价 —— 无实时快照端点, 用K线拼(端外仅 day_1; 省略 start/end 返回最近100根)。

    日K(/prices/daily)为主:
      · 末根是今天(盘中/收盘的当日bar) → 现价 = 末根收盘(盘中实时更新), 昨收 = 倒数第二根;
      · 末根不是今天 → 再问分时(/prices/intraday, 端外仅当前交易日)拿今天现价, 昨收 = 末根收盘
        (典型: 数据源日K只在收盘后才补今天bar); 分时也拿不到(休市日) → 现价 = 末根收盘, 涨跌幅
        按末根相对其前一根算(休市期间展示的是最近交易日的涨跌)。
    这样常规情况每合约只花 1 次 API 配额(频控 15/窗口), 分时仅在必要时调用。
    """
    thscode = (thscode or "").strip()
    if not is_thscode(thscode):
        return None
    hit = _quote_cache.get(thscode)
    if hit and time.time() - hit[1] < _QUOTE_TTL:
        return hit[0]
    # 负缓存: 此前查询失败(合约到期/退市/代码错)直接跳过, 不再反复打 API(频控 15/窗口)
    _fc = _quote_fail_cache.get(thscode)
    if _fc and time.time() - _fc[0] < _fc[1]:
        return None
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        kind = "options" if _looks_option(thscode) else "futures"
        # 日K不传 start/end —— fuyao 两个端点(futures/options)对窗口过滤都返回空 item
        # (实测 2026-11), 省略参数拿最近100根; 涨跌/昨收全部从这100根序列里取。
        daily = await _get(f"/api/{kind}/prices/daily", {"thscode": thscode, "time_period": "day_1"})
        klines = (daily or {}).get("item") or []
        closes = [float(k["close_price"]) for k in klines if k.get("close_price") is not None]
        if not closes:
            _quote_cache[thscode] = (None, time.time())
            return None

        _bj = _dt.now(_tz.utc) + _td(hours=8)
        today = _bj.strftime("%Y-%m-%d")
        tomorrow = (_bj + _td(days=1)).strftime("%Y-%m-%d")

        def _bar_date(k: dict) -> str:
            try:
                # bar 的 timestamp 是该交易日北京时间零点
                return (_dt.fromtimestamp(int(k["timestamp"]) / 1000, tz=_tz.utc) + _td(hours=8)).strftime("%Y-%m-%d")
            except Exception:
                return ""

        last_date = _bar_date(klines[-1]) if klines else ""
        price, quote_time = closes[-1], last_date
        got_intraday = False
        # 末根 bar 是否"当前交易时段": 今天, 或期货夜盘归属的明天(周日晚21:00开盘的夜盘
        # 记入下一交易日, bar 已是实时更新) —— 此时现价就是末根收盘, 不需要分时。
        current_bar = last_date in (today, tomorrow)
        if not current_bar:
            # 日K末根不是当前时段: 盘中 → 分时拿现价; 休市日 → 保持最近收盘。
            # 分时在休市日返回的是上一交易日的残留(末点=周五 15:00 收盘), 不是今天的实时价 ——
            # 只有末点时间是今天才采用, 否则 price==末根收盘、涨跌恒 0, 还会把"昨收=末根"用错。
            try:
                intraday = await _get(f"/api/{kind}/prices/intraday", {"thscode": thscode})
                pts = (intraday or {}).get("item") or []
                pt_time = _pts_time(pts) if pts else ""
                if pts and pts[-1].get("price") is not None and pt_time[:10] == today:
                    price = float(pts[-1]["price"])
                    quote_time = pt_time or today
                    got_intraday = True
            except Exception as e:
                print(f"[ths_fuyao] {thscode} intraday 失败(用日K收盘兜底): {str(e)[:100]}")

        if not price:
            _quote_cache[thscode] = (None, time.time())
            return None
        prev_close = _prev_close_of(closes, last_date, today, got_intraday, tomorrow)
        change_pct = round((price - prev_close) / prev_close * 100, 3) if prev_close else None
        # OHLC/量额取日K末根: 今天盘中bar(实时累计)或最近交易日bar —— 分时只替代现价
        bar = klines[-1] if klines else {}

        def _num(k):
            try:
                v = bar.get(k)
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        out = {"price": price, "prev_close": prev_close, "change_pct": change_pct,
               "name": await _name_of(thscode), "quote_time": quote_time,
               "open": _num("open_price"), "high": _num("high_price"), "low": _num("low_price"),
               "volume": _num("volume"), "amount": _num("turnover")}
        _quote_cache[thscode] = (out, time.time())
        try:
            from services import market_store
            await market_store.save_quotes([{
                "code": thscode,
                "ticker": thscode.split(".")[0],
                "sub_assets_type": "cn_futures" if kind == "futures" else "cn_options",
                "name": out.get("name") or "", "price": out["price"],
                "open": out.get("open"), "high": out.get("high"), "low": out.get("low"),
                "prev_close": out.get("prev_close"), "change_pct": out.get("change_pct"),
                "volume": out.get("volume"), "amount": out.get("amount"),
                "currency": _currency_of_thscode(thscode), "data_source": "ths",
            }])
        except Exception:
            pass
        return out
    except Exception as e:
        print(f"[ths_fuyao] quote {thscode} 失败: {str(e)[:120]}")
        # 负缓存: "Target not found"(合约到期/退市/代码错, 一去不返)缓存 24h; 其它(网络抖动)缓存 60s
        _perm = "Target not found" in str(e)
        _quote_fail_cache[thscode] = (time.time(), 24 * 3600.0 if _perm else 60.0)
        return None


def _looks_option(thscode: str) -> bool:
    """期权判定: ① 拼码形态 TICKER-C/P-STRIKE(品种月份-认购/认沽-行权价) 必为期权;
    ② 交易所后缀 .SH/.SZ 的纯数字主体多为 ETF 期权。只影响端点选择(/api/options vs futures)
    与 asset_type 标注, 不影响数值。"""
    up = thscode.upper()
    body, _, exch = up.partition(".")
    if "-C-" in body or "-P-" in body:
        return True
    if exch in ("SH", "SZ") and body.isdigit():
        return True
    return False


def _prev_close_of(closes: list[float], last_date: str, today: str, got_intraday: bool,
                   tomorrow: str = "") -> float | None:
    """昨收取值: ① 末根是当前时段 bar(今天, 或期货夜盘归属的明天) → 倒数第二根;
    ② 末根非当前时段但拿到今日分时现价 → 末根(最近交易日收盘);
    ③ 末根非当前时段且分时也没拿到(休市日) → 倒数第二根(涨跌=最近交易日相对其前一日)。
    ③ 若误取末根, 现价==昨收 → change_pct=0(期权休市日今日盈亏恒 0 的 bug)。"""
    if not closes:
        return None
    current_bar = last_date == today or (bool(tomorrow) and last_date == tomorrow)
    if len(closes) >= 2 and (current_bar or not got_intraday):
        return closes[-2]
    return closes[-1]


def _pts_time(pts: list) -> str:
    """分时末点毫秒时间戳 → 北京时间 'YYYY-MM-DD HH:MM'。"""
    try:
        from datetime import datetime, timedelta, timezone
        ts = pts[-1].get("timestamp")
        if not ts:
            return ""
        dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc) + timedelta(hours=8)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


async def a_share_snapshot(thscodes: list[str]) -> Optional[list[dict]]:
    """A股行情快照(GET /api/a-share/prices/snapshot, 支持批量逗号分隔, 不分页)。

    thscodes 为数据源标准格式(600406.SH)。返回 [{code, price, prev_close, change_pct, ...}];
    接口不返回中文名(name 由持仓行/底稿提供)。best-effort: 失败返回 None。
    """
    codes = [str(c or "").strip().upper() for c in thscodes if str(c or "").strip()]
    if not codes:
        return None
    try:
        d = await _get("/api/a-share/prices/snapshot", {"thscodes": ",".join(codes)})
        items = (d or {}).get("item") or []
        out = []
        for it in items:
            try:
                last = float(it.get("last_price") or 0)
                prev = float(it.get("prev_price") or 0)
                if not last:
                    continue
                chg = it.get("price_change_ratio_pct")
                out.append({
                    "code": str(it.get("thscode") or "").upper(),
                    "name": "",
                    "price": last,
                    "prev_close": prev or None,
                    "change_pct": float(chg) if chg is not None else (
                        round((last - prev) / prev * 100, 3) if prev else None),
                    "open": float(it.get("open_price") or 0) or None,
                    "high": float(it.get("high_price") or 0) or None,
                    "low": float(it.get("low_price") or 0) or None,
                    "volume": float(it.get("volume") or 0),
                    "turnover": float(it.get("turnover") or 0),
                    "currency": "CNY",
                })
            except Exception:
                continue
        return out or None
    except Exception as e:
        print(f"[ths_fuyao] a-share snapshot 失败: {str(e)[:120]}")
        return None


# ---- 品种目录(期货/期权 varieties, 标的信息源用) ----

_varieties_cache: dict[str, tuple[list, float]] = {}
_VARIETIES_TTL = 6 * 3600


async def varieties_list(kind: str) -> list[dict]:
    """期货/期权品种列表(GET /api/futures|options/varieties/list), 缓存 6h。

    kind: 'futures' | 'options'。item 字段: variety_code/name/exchange_code/margin_rate/
    trade_amount/contract_multiplier/tick_size...(见上游 docs futures/options-varieties)。
    best-effort: 失败返回 []。
    """
    k = "options" if kind == "options" else "futures"
    hit = _varieties_cache.get(k)
    if hit and time.time() - hit[1] < _VARIETIES_TTL:
        return hit[0]
    try:
        d = await _get(f"/api/{k}/varieties/list", {})
        items = ((d or {}).get("item")) or []
        _varieties_cache[k] = (items, time.time())
        return items
    except Exception as e:
        print(f"[ths_fuyao] {k} varieties 失败: {str(e)[:120]}")
        return []


# ---- 基金(场内 ETF/LOF 行情快照 + 场外基金净值) ----

def fund_thscode(code: str) -> str:
    """基金代码 → thscode。场外存档码带 .OF 后缀(019144.OF)原样透传给上游;
    裸 6 位码按段位映射: 5 开头(50/51/56/58/519 沪系) → .SH, 其余 → .SZ
    (场外基金代码 0/1/6 开头为主挂深市, 5 开头含 519 场外沪挂沪市)。
    带 SH/SZ 等其他后缀的原样大写。"""
    c = (code or "").strip()
    if "." in c:
        base, suf = c.split(".", 1)
        if suf.upper() == "OF":
            return f"{base}.{suf.upper()}"     # 场外开放式 → ths 认 .OF, 原样请求
        return c.upper()
    if len(c) == 6 and c.isdigit():
        return f"{c}.SH" if c.startswith("5") else f"{c}.SZ"
    return c.upper()


async def fund_snapshot(code: str) -> Optional[dict]:
    """场内基金(ETF/LOF)行情快照(单只) —— fund_snapshots 批量版薄壳, 形状不变。

    返回 {price, prev_close, change_pct, open, high, low, volume, turnover, quote_time};
    接口不返回名称(name 由资产行/天天基金兜底)。REITs 不支持(上游 code=3004)。
    """
    rows = await fund_snapshots([code])
    if not rows:
        return None
    r = rows[0]
    return {k: r.get(k) for k in ("price", "prev_close", "change_pct", "open", "high",
                                  "low", "volume", "turnover", "quote_time")}


async def fund_snapshots(codes: list[str]) -> Optional[list[dict]]:
    """场内基金(ETF/LOF)行情快照(GET /api/fund/market/snapshot, 批量逗号分隔, 上限 100/次)。

    codes 为标准码(510300.SH / 161725.SZ), 自动分块。返回
    [{code, price, prev_close, change_pct, open, high, low, volume, turnover, quote_time}];
    quote_time 取 data.timestamp(毫秒, 上游已是北京时间)秒级文本。接口不返回名称。
    best-effort: 失败返回 None。
    """
    clean: list[str] = []
    for c in codes or []:
        c = fund_thscode(str(c or "").strip().upper())
        if c and c not in clean:
            clean.append(c)
    if not clean:
        return None
    out: list[dict] = []
    try:
        for i in range(0, len(clean), 100):
            d = await _get("/api/fund/market/snapshot", {"thscode": ",".join(clean[i:i + 100])})
            ts = (d or {}).get("timestamp")
            quote_time = ""
            if ts:
                try:
                    from datetime import datetime, timedelta, timezone
                    quote_time = (datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
                                  + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            for it in (d or {}).get("item") or []:
                try:
                    last = float(it.get("last_price") or 0)
                    if not last:
                        continue
                    prev = float(it.get("prev_price") or 0)
                    chg = it.get("price_change_ratio_pct")
                    out.append({
                        "code": str(it.get("thscode") or "").upper(),
                        "price": last,
                        "prev_close": prev or None,
                        "change_pct": float(chg) if chg is not None else (
                            round((last - prev) / prev * 100, 3) if prev else None),
                        "open": float(it.get("open_price") or 0) or None,
                        "high": float(it.get("high_price") or 0) or None,
                        "low": float(it.get("low_price") or 0) or None,
                        "volume": float(it.get("volume") or 0),
                        "turnover": float(it.get("turnover") or 0),
                        "quote_time": quote_time,
                    })
                except Exception:
                    continue
        return out or None
    except Exception as e:
        print(f"[ths_fuyao] fund snapshot 失败: {str(e)[:120]}")
        return None


async def fund_nav(code: str) -> Optional[dict]:
    """场外基金单位净值(GET /api/fund/performance/nav, nav_type=unit)。

    不传 range 只回最新一条 → 取不到涨跌; 用 range=week 拿近一周序列,
    末两根算日涨跌幅(净值 T+1 口径)。返回 {nav, prev_nav, nav_date(YYYY-MM-DD), change_pct}。
    """
    try:
        d = await _get("/api/fund/performance/nav",
                       {"thscode": fund_thscode(code), "nav_type": "unit", "range": "week"})
        items = (d or {}).get("item") or []
        if not items:
            return None
        last = items[-1]
        nav = float(last.get("unit_nav") or 0)
        if not nav:
            return None
        prev = float(items[-2].get("unit_nav") or 0) if len(items) >= 2 else 0.0
        nav_date = ""
        nd = last.get("nav_date")
        if nd:
            try:
                from datetime import datetime, timedelta, timezone
                nav_date = (datetime.fromtimestamp(int(nd) / 1000, tz=timezone.utc)
                            + timedelta(hours=8)).strftime("%Y-%m-%d")
            except Exception:
                pass
        return {
            "nav": round(nav, 4),
            "prev_nav": round(prev, 4) or None,
            "nav_date": nav_date,
            "change_pct": round((nav - prev) / prev * 100, 3) if prev else None,
        }
    except Exception as e:
        print(f"[ths_fuyao] fund nav {code} 失败: {str(e)[:120]}")
        return None


# ── 源适配器(规范层, 见 services/quote_source.py) ──────────────────────
# 场内基金(ETF/LOF)判定: 6 位主体 + 深 1[56]xxxx(159 ETF/16x LOF) + 沪 5[0-8]xxxx
# (50x/51x/56x/58x) —— 与 market_data._is_etf_lof 同规则(就地实现, 免循环导入)。
# 这类码上游不走 A股快照端点(/api/a-share/prices/snapshot 对基金返回 3001 Target not
# found), 走基金快照端点(/api/fund/market/snapshot, 见 docs fund-market)。
_ETF_LOF_RE = re.compile(r"^(1[56]|5[0-8])\d{4}$")


def is_etf_lof(code: str) -> bool:
    body = (code or "").strip().upper().split(".")[0]
    return bool(len(body) == 6 and body.isdigit() and _ETF_LOF_RE.match(body))


async def snapshot_rows(codes: list[str]) -> list[QuoteRow]:
    """fuyao 源适配器: 标准码 → 规范 QuoteRow(翻译在此完成, 不再散在派发层)。

    四路分流: 期货/期权(thscode)逐只走 quote()(fuyao 无批量快照接口); 场外基金(.OF)
    逐只走 fund_nav(/api/fund/performance/nav, 净值 T+1); 场内基金(ETF/LOF)走批量
    fund_snapshots; 其余 A股走批量 a_share_snapshot。
    契约: 不抛异常 —— 失败/无数据的码不出现在返回里, 派发层据此按码兜底。
    """
    fut_opt: list[str] = []
    otc_fund: list[str] = []
    etf_lof: list[str] = []
    ashare: list[str] = []
    for c in codes:
        c = str(c or "").strip().upper()
        if not c:
            continue
        if is_thscode(c):
            fut_opt.append(c)
        elif c.endswith(".OF"):
            otc_fund.append(c)      # 场外基金没有盘中价, 快照端点也不收 .OF 码
        elif is_etf_lof(c):
            etf_lof.append(c)
        else:
            ashare.append(c)

    async def _one_ths(code: str) -> Optional["QuoteRow"]:
        try:
            q = await quote(code)
            if not q or not q.get("price"):
                return None
            return QuoteRow(
                code=code, market="FUT" if ths_kind(code) == "fut" else "OPT",
                name=q.get("name") or "", price=float(q["price"]),
                prev_close=q.get("prev_close"), change_pct=q.get("change_pct"),
                currency=_currency_of_thscode(code), quote_time=q.get("quote_time") or "", source="ths")
        except Exception as e:
            print(f"[ths_fuyao] {code} quote 失败: {str(e)[:120]}")
            return None

    async def _one_of(code: str) -> Optional["QuoteRow"]:
        # 场外基金: 单位净值即价格(nav=price), 昨净值即昨收; quote_time 用净值日期
        try:
            d = await fund_nav(code)
            if not d or not d.get("nav"):
                return None
            return QuoteRow(
                code=code, market="OF", price=float(d["nav"]),
                prev_close=d.get("prev_nav"), change_pct=d.get("change_pct"),
                currency="CNY", quote_time=d.get("nav_date") or "", source="ths")
        except Exception as e:
            print(f"[ths_fuyao] {code} fund nav 失败: {str(e)[:120]}")
            return None

    out: list[QuoteRow] = []
    ths_rows = await asyncio.gather(*(_one_ths(c) for c in fut_opt))
    out.extend(r for r in ths_rows if r)
    of_rows = await asyncio.gather(*(_one_of(c) for c in otc_fund))
    out.extend(r for r in of_rows if r)

    if etf_lof:
        try:
            for it in await fund_snapshots(etf_lof) or []:
                try:
                    if not it.get("price"):
                        continue
                    out.append(QuoteRow(
                        code=str(it.get("code") or "").upper(), market="A",
                        name=it.get("name") or "", price=float(it["price"]),
                        open=it.get("open"), high=it.get("high"), low=it.get("low"),
                        prev_close=it.get("prev_close"),
                        volume=it.get("volume") or 0, amount=it.get("turnover") or 0,
                        change_pct=it.get("change_pct"), currency="CNY",
                        quote_time=it.get("quote_time") or "", source="ths"))
                except Exception:
                    continue
        except Exception as e:
            print(f"[ths_fuyao] fund snapshot 批量失败: {str(e)[:120]}")

    if ashare:
        try:
            for it in await a_share_snapshot(ashare) or []:
                try:
                    if not it.get("price"):
                        continue
                    out.append(QuoteRow(
                        code=str(it.get("code") or "").upper(), market="A",
                        name=it.get("name") or "", price=float(it["price"]),
                        open=it.get("open"), high=it.get("high"), low=it.get("low"),
                        prev_close=it.get("prev_close"),
                        volume=it.get("volume") or 0, amount=it.get("turnover") or 0,
                        change_pct=it.get("change_pct"), currency="CNY", source="ths"))
                except Exception:
                    continue
        except Exception as e:
            print(f"[ths_fuyao] A股 snapshot 失败: {str(e)[:120]}")
    return out


class _FuyaoSource:
    name = "ths"        # 统一源名(与 assets_classify.data_source / 日志一致; 历史别名 fuyao 在注册表保留)
    asset_types = ("A", "FUT", "OPT")

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        return await snapshot_rows(codes)


SOURCE = _FuyaoSource()
