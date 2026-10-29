"""新浪行情源(sina_quote): legacy 免费源, 在源链里只兜底股票(A/HK/US)。

- 与 ths_fuyao / futu_quote 同构: 对外暴露 SOURCE 适配器(services/quote_source.QuoteSource 协议),
  翻译(字段位置/代码格式/GBK编码)全部收敛在本文件内。
- 不受 ths/futu 频控策略约束(公开接口, 无配额), 但同样不主动联网 —— 只有源链派发
  (手动刷新/冷启动兜底)才会调到。
- 新浪没有期货/期权行情, 所以 FUT/OPT 的源链里没有它(见 market_data._quote_chains)。
- 历史K线(money.finance.sina.com.cn)不属于本渠道, 仍在 market_data。
"""
from __future__ import annotations

import asyncio
import re

import requests as _requests

from services.quote_source import QuoteRow

_SESSION_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def _sina_symbol(stock_code: str) -> str:
    """Convert stock code to Sina symbol format (sh/sz/bj prefix)."""
    from services.market_data import _is_bse, split_stock_code   # 延迟导入避免循环
    stock_code = split_stock_code(stock_code)[1]
    # 北交所(bj): 8/4/920 — 必须先判, 否则 920xxx 会被下面的 9→sh 规则吃掉
    if _is_bse(stock_code):
        return f"bj{stock_code}"
    # Shanghai: 6 (主板), 9 (B股), 5 (ETF/封基/可转债)
    # Shenzhen: 0/2/3 (主板/中小板/创业板), 1 (ETF/可转债, e.g. 159xxx)
    if stock_code[:1] in ("6", "9", "5"):
        return f"sh{stock_code}"
    return f"sz{stock_code}"


def _fetch_sina_quotes(stock_codes: list[str]) -> dict:
    """Fetch real-time quotes from Sina Finance API (hq.sinajs.cn).
    This API is stable, fast, and doesn't require auth.
    """
    from services.market_data import is_a_share, normalize_stock_code   # 延迟导入避免循环
    if not stock_codes:
        return {}

    stock_codes = [normalize_stock_code(c) for c in stock_codes if is_a_share(c)]
    if not stock_codes:
        return {}
    symbols = [_sina_symbol(c) for c in stock_codes]
    # 响应里的 sh/sz 前缀码 → 标准代码(600111.SH)。key 必须与调用方(holdings/流水)的
    # 代码格式一致 —— 曾经直接用裸码做 key, 快照落库/调用方查表全部错位。
    by_sina = dict(zip(symbols, stock_codes))
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"

    resp = _requests.get(url, headers=_SESSION_HEADERS, timeout=10)
    resp.encoding = "gbk"
    text = resp.text

    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Format: var hq_str_sh601168="西部矿业,8.14,8.54,8.13,...";
        match = re.match(r'var hq_str_(\w+)="(.*)";', line)
        if not match:
            continue

        symbol = match.group(1)
        data_str = match.group(2)
        if not data_str:
            continue

        fields = data_str.split(",")
        if len(fields) < 32:
            continue

        # 标准代码(600111.SH); 映射不到(理应不会)退回裸码兜底
        code = by_sina.get(symbol) or symbol[2:]

        try:
            name = fields[0]
            open_price = float(fields[1]) if fields[1] else 0
            prev_close = float(fields[2]) if fields[2] else 0
            price = float(fields[3]) if fields[3] else 0
            high = float(fields[4]) if fields[4] else 0
            low = float(fields[5]) if fields[5] else 0
            volume = float(fields[8]) if fields[8] else 0  # shares
            amount = float(fields[9]) if fields[9] else 0  # RMB

            change_pct = 0
            if prev_close > 0 and price > 0:
                change_pct = round((price - prev_close) / prev_close * 100, 2)

            amplitude = 0
            if prev_close > 0 and high > 0 and low > 0:
                amplitude = round((high - low) / prev_close * 100, 2)

            result[code] = {
                "stock_code": code,
                "stock_name": name,
                "market": "A",
                "currency": "CNY",
                "fx_rate": 1.0,
                "price": price,
                "open": open_price,
                "high": high,
                "low": low,
                "prev_close": prev_close,
                "volume": volume,
                "amount": amount,
                "change_pct": change_pct,
                "amplitude": amplitude,
                "turnover_rate": 0,  # Sina doesn't provide this directly
            }
        except (ValueError, IndexError):
            continue

    return result


async def _fetch_remote_quotes(codes: list[str]) -> dict:
    """远端抓一批股票报价(A股批量新浪, 港美股逐只), 附汇率。全挂返回 {}; 不缓存不落库。"""
    from services.market_data import get_fx_info, split_stock_code   # 延迟导入避免循环
    a_codes = [c for c in codes if split_stock_code(c)[0] == "A"]
    hk_codes = [c for c in codes if split_stock_code(c)[0] == "HK"]
    us_codes = [c for c in codes if split_stock_code(c)[0] == "US"]

    result = {}
    if a_codes:
        result.update(await asyncio.to_thread(_fetch_sina_quotes, a_codes))

    async def fetch_one(code: str):
        market, symbol = split_stock_code(code)
        if market == "HK":
            q = await asyncio.to_thread(_fetch_hk_stock_quote, symbol)
        elif market == "US":
            q = await asyncio.to_thread(_fetch_us_stock_quote, symbol)
        else:
            q = None
        if not q:
            return None
        fx = get_fx_info(q.get("currency", "CNY"), allow_network=True)
        return code, {
            "stock_code": code,
            "fx_rate": fx["rate"],
            "fx_time": fx.get("time", ""),
            "fx_source": fx.get("source", ""),
            **q,
        }

    overseas = await asyncio.gather(
        *(fetch_one(c) for c in [*hk_codes, *us_codes]),
        return_exceptions=True,
    )
    for item in overseas:
        if isinstance(item, Exception) or not item:
            continue
        code, quote = item
        result[code] = quote
    return result


def _parse_sina_hk(code: str, text: str) -> dict | None:
    """解析新浪港股行(rt_hk / hk 两种前缀字段顺序相同)。
    字段: [0]英文名 [1]中文名 [2]今开 [3]昨收 [4]最高 [5]最低 [6]现价 [7]涨跌额 [8]涨跌% [9]买一 [10]卖一
          [11]成交额(港元) [12]成交量(股) ... [17]日期 YYYY/MM/DD [18]时刻 HH:MM(:SS)
    以 腾讯 00700 实测核过: 现价 423.8 − 昨收 411.4 = 涨跌额 12.4。"""
    m = re.search(r'var hq_str_(?:rt_)?hk\w+="(.*)";', text or "")
    if not m or not m.group(1):
        return None
    f = m.group(1).split(",")
    if len(f) < 13:
        return None

    def _num(i):
        try:
            return float(f[i]) if f[i] else 0.0
        except (ValueError, IndexError):
            return 0.0

    open_, prev, high, low, last = _num(2), _num(3), _num(4), _num(5), _num(6)
    if last <= 0:
        return None
    try:
        change_pct = round(float(f[8]), 2)
    except (ValueError, IndexError):
        change_pct = round((last - prev) / prev * 100, 2) if prev > 0 else 0
    out = {
        "stock_name": f[1] or f[0] or code,
        "price": last,
        "open": open_,
        "high": high,
        "low": low,
        "prev_close": prev,
        "volume": _num(12),
        "amount": _num(11),              # 港元
        "change_pct": change_pct,
        "amplitude": round((high - low) / prev * 100, 2) if prev > 0 and high and low else 0,
        "turnover_rate": 0,
        "market": "HK",
        "currency": "HKD",
    }
    if len(f) > 18 and f[17]:
        out["quote_time"] = f"{f[17].replace('/', '-')} {f[18]}".strip()   # 港交所当地时间(=北京时间)
    return out


def _fetch_hk_stock_quote(code: str) -> dict | None:
    """港股个股行情. code = '00700' (5位带前导0)。

    走新浪 rt_hk<CODE>(实时); 同源的 hk<CODE> 是 15 分钟延时行 —— 2026-10-09 14:45 实测
    hk00700 时刻停在 14:22, rt_hk00700 是 14:45:13, 腾讯 r_hk00700 是 14:45:09(无前缀
    hk00700 也延时)。rt_ 取不到时退回延时行, 时刻照实带在 quote_time 里。"""
    for prefix in ("rt_hk", "hk"):
        try:
            resp = _requests.get(f"https://hq.sinajs.cn/list={prefix}{code}",
                                 headers=_SESSION_HEADERS, timeout=5)
            resp.encoding = "gbk"
            q = _parse_sina_hk(code, resp.text.strip())
            if q:
                if prefix == "hk":
                    q["delayed"] = True
                return q
        except Exception as e:
            print(f"[hk-stock] {prefix}{code} failed: {e}")
    return None


def _us_session_of(clock: str) -> str:
    """把新浪给的交易所本地时刻(如 "Aug 19 08:41AM EDT")归到哪个时段。

    时刻本身已经是交易所本地时间, 不用做时区换算, 只看墙上钟点:
      04:00-09:29 盘前 / 09:30-15:59 盘中 / 16:00-19:59 盘后 / 其余 休市
    """
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", clock or "", re.I)
    if not m:
        return ""
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if h == 12:
        h = 0
    if ap == "PM":
        h += 12
    t = h * 60 + mi
    if 4 * 60 <= t < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= t < 16 * 60:
        return "regular"
    if 16 * 60 <= t < 20 * 60:
        return "post"
    return "closed"


def _fetch_us_stock_quote(symbol: str) -> dict | None:
    """美股个股实时 via Sina (gb_<lowercase>)。

    字段位置在 mrvl/aapl/nvda/ko/sndl 五只上逐一核过, 并拿 Yahoo(含 includePrePost)
    交叉验证:
      [1] 最新成交价(正式时段; 盘外时=上一时段收盘)  [2] 对应涨跌%    [5][6][7] 开/高/低
      [10] 成交量(股)   [21] 盘前或盘后价   [22] 盘外相对 [1] 的涨跌%
      [24] [21] 那个价的时刻   [25] [1] 那个价的时刻   [26] 昨收(真值)
    实测 [21] 与 Yahoo 盘前末点吻合(AAPL 309.80/309.8, NVDA 220.6757/220.68,
    KO 88.7147/88.7147)。

    price 保持"正式时段成交价"语义不变(持仓估值按它算, 别让盘前几十分钟的跳动改写
    组合市值), 盘外价另开 ext_hours 一块给出, 并带上两个时刻 —— 否则亚洲白天看到的
    "现在 -7.82%" 其实是上一个交易日的事, 模型无从分辨。
    """
    try:
        url = f"https://hq.sinajs.cn/list=gb_{symbol.lower()}"
        resp = _requests.get(url, headers=_SESSION_HEADERS, timeout=5)
        resp.encoding = "gbk"
        text = resp.text.strip()
        m = re.match(r'var hq_str_gb_\w+="(.*)";', text)
        if not m or not m.group(1):
            return None
        f = m.group(1).split(",")
        if len(f) < 5:
            return None

        def _f(i):
            try:
                v = float(f[i])
                return v if v > 0 else None
            except (ValueError, IndexError):
                return None

        last = float(f[1]) if f[1] else 0
        change_pct = float(f[2]) if f[2] else 0
        # 昨收优先取源里的真值 [26]; 缺了才用 last/(1+pct) 反推(反推会被 pct 的两位
        # 小数放大, MRVL 实测反推 234.3241 vs 真值 234.33)
        prev = _f(26)
        if not prev:
            prev = round(last / (1 + change_pct / 100), 4) if last > 0 and change_pct != 0 else last
        high, low = _f(6), _f(7)
        out = {
            "stock_name": f[0] or symbol.upper(),
            "price": last,
            "open": _f(5) or 0,
            "high": high or 0,
            "low": low or 0,
            "prev_close": prev,
            "volume": _f(10) or 0,
            "amount": 0,            # 新浪美股行只给成交量, 没有成交额
            "change_pct": round(change_pct, 2),
            "amplitude": round((high - low) / prev * 100, 2) if (high and low and prev) else 0,
            "turnover_rate": 0,
            "market": "US",
            "currency": "USD",
        }
        if len(f) > 25 and f[25]:
            out["price_as_of"] = f[25].strip()          # 如 "Aug 18 04:00PM EDT"

        # 盘外(盘前/盘后)。盘中时 [21] 跟着最新价走、[22] 约等于 0, 这时不单列, 免得
        # 多出一块和 price 重复的信息让模型犯迷糊。
        ext_px, ext_clock = _f(21), (f[24].strip() if len(f) > 24 else "")
        sess = _us_session_of(ext_clock)
        out["session"] = sess or ""
        if ext_px and sess in ("pre", "post"):
            try:
                ext_pct = round(float(f[22]), 2)
            except (ValueError, IndexError):
                ext_pct = round((ext_px - last) / last * 100, 2) if last else 0
            out["ext_hours"] = {
                "label": "盘前" if sess == "pre" else "盘后",
                "price": ext_px,
                "change_pct": ext_pct,       # 相对 price(上一时段收盘)
                "as_of": ext_clock,
            }
        return out
    except Exception as e:
        print(f"[us-stock] {symbol} failed: {e}")
        return None


# ── 源适配器(规范层, 见 services/quote_source.py) ──────────────────────
class SinaSource:
    """legacy 免费源(新浪等)适配器: 只兜底股票(A/HK/US), 不受 ths/futu 频控策略约束。"""

    name = "sina"
    asset_types = ("A", "HK", "US")

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        try:
            result = await _fetch_remote_quotes(codes)
        except Exception as e:
            print(f"[sina_quote] 兜底抓取失败: {e}")
            return []
        # 源特有字段(盘外价/时段等)走 info 透传, 不进规范语义
        extra_keys = ("session", "ext_hours", "delayed", "price_as_of", "amplitude")
        rows: list[QuoteRow] = []
        for code, q in result.items():
            try:
                if not q.get("price"):
                    continue
                rows.append(QuoteRow(
                    code=code, market=q.get("market") or "A",
                    name=q.get("stock_name") or "", price=float(q["price"]),
                    open=q.get("open"), high=q.get("high"), low=q.get("low"),
                    prev_close=q.get("prev_close"),
                    volume=q.get("volume") or 0, amount=q.get("amount") or 0,
                    change_pct=q.get("change_pct"),
                    currency=q.get("currency") or "CNY",
                    quote_time=str(q.get("quote_time") or ""), source="sina",
                    info={k: q[k] for k in extra_keys if q.get(k) not in (None, "")}))
            except Exception:
                continue
        return rows


SOURCE = SinaSource()
