"""股票派息全自动落账(三市场一套代码)。

设计定稿(用户拍板):
  · 分红全部进已实现(DIVIDEND 流水 → income_realized), **不触碰成本**(无摊薄, 三市场统一);
  · 全自动落账: 数据源事件 × 持仓区间 → 直接写 DIVIDEND 流水, 零人工确认;
  · fee 默认 0(不建税模型), 金额/费在流水编辑里手动改; A 股源金额含税、US 为宣派额, 差异手改兜底;
  · 空头持仓跳过(借券分红是支出, 手记); 送配股不在范围内(BONUS 手记);
  · 去重: (code, ex_date 窗口匹配)已有 DIVIDEND 流水跳过 —— 手记/自动/重跑都不会双记;
    不设抑制名单(用户只会改金额不会删整条)。

数据源适配层(可插拔, 每市场一个采集器, 输出统一事件形状):
  · US: Nasdaq dividends(不复权, 带派息日) → 回退 Yahoo chart events(复权值按拆股还原)
  · A 股: fuyao corporate-actions(税前现金分红; 送股比例暂不用)
  · 港/美: futu corporate-actions/dividends(US 拉不到时回退 Nasdaq/Yahoo)
统一事件: {ex_date, pay_date?, per_share, currency, declared?, source}
"""
from __future__ import annotations
import asyncio
import datetime as _dt
import time as _t
import re as _re

_fcache: dict = {}            # code -> (events, ts)
_F_TTL = 12 * 3600


def _d10(v) -> str | None:
    s = str(v or "")[:10].replace("/", "-")
    return s if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


# ── US 数据源: Nasdaq 主 + Yahoo 回退 ────────────────────────────────────────

def _us_nasdaq_sync(sym: str) -> list[dict] | None:
    """Nasdaq 分红历史(不复权, 带派息日)。非 Nasdaq 上市票接口返回 rows=null → None 交给回退源。"""
    import requests
    r = requests.get(f"https://api.nasdaq.com/api/quote/{sym}/dividends", params={"assetclass": "stocks"},
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
    rows = ((((r.json() or {}).get("data") or {}).get("dividends") or {}).get("rows"))
    if rows is None:
        return None

    def _md(s):
        try:
            return _dt.datetime.strptime(str(s), "%m/%d/%Y").date().isoformat()
        except ValueError:
            return None
    out = []
    for x in rows:
        if str(x.get("type") or "").lower() != "cash":
            continue
        ex = _md(x.get("exOrEffDate"))
        try:
            amt = float(str(x.get("amount") or "").replace("$", "").replace(",", ""))
        except ValueError:
            continue
        if ex and amt > 0:
            out.append({"ex_date": ex, "pay_date": _md(x.get("paymentDate")), "per_share": amt,
                        "currency": str(x.get("currency") or "USD"), "declared": f"${amt}", "estimated": False,
                        "source": "nasdaq"})
    return out


def yahoo_unadjust(divs: dict, splits: dict | None, gmtoffset: int = 0) -> list[dict]:
    """Yahoo events → 事件列表。Yahoo 的分红金额按之后的拆股复权过: 原始 = 复权值 × 除息日之后各次拆股比例之积。"""
    sp = [(int(s.get("date") or k), float(s.get("numerator") or 0) / float(s.get("denominator") or 1))
          for k, s in (splits or {}).items() if float(s.get("numerator") or 0) > 0]
    out = []
    for k, v in (divs or {}).items():
        ts = int(v.get("date") or k)
        f = 1.0
        for st, ratio in sp:
            if st > ts:
                f *= ratio
        amt = float(v.get("amount") or 0) * f
        if amt > 0:
            ex = _dt.datetime.utcfromtimestamp(ts + gmtoffset).date().isoformat()
            out.append({"ex_date": ex, "pay_date": None, "per_share": round(amt, 6), "currency": "USD",
                        "declared": f"${round(amt, 6)}", "estimated": False, "source": "yahoo"})
    return sorted(out, key=lambda e: e["ex_date"])


def _us_yahoo_sync(sym: str) -> list[dict]:
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": "10y", "interval": "1mo", "events": "div,splits"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    res = ((r.json() or {}).get("chart") or {}).get("result") or []
    if not res:
        return []
    ev = res[0].get("events") or {}
    return yahoo_unadjust(ev.get("dividends"), ev.get("splits"), int((res[0].get("meta") or {}).get("gmtoffset") or 0))


# ── 适配层: 每市场一个事件源, 输出统一事件形状(可插拔) ────────────────────────
#   A 股(SH/SZ/BJ): fuyao corporate-actions(除权除息, 现金分红税前; 送股比例暂不用)
#   港/美(HK/US):   futu corporate-actions/dividends(金额不复权, 自带币种/派息日)
#   回退:           US 拉不到 futu 时回退 Nasdaq/Yahoo 内置源
_SOURCED_SUFFIX = (".SH", ".SZ", ".BJ", ".HK", ".US")


def _us_fallback_sync(code: str) -> list[dict] | None:
    """US 内置回退源: Nasdaq 主 + Yahoo 回退(复权还原)。"""
    c = code.upper()
    sym = c[:-3]
    try:
        ev = _us_nasdaq_sync(sym)
    except Exception:
        ev = None
    if ev is not None:
        return ev
    try:
        return _us_yahoo_sync(sym.replace(".", "-"))     # Yahoo 写 BRK-B
    except Exception:
        return None


async def _events_for(code: str) -> list[dict] | None:
    """某票的现金分红事件(统一形状, 除净日升序)。无源市场/拉不到 → None(不缓存, 下次重试)。"""
    c = str(code or "").upper()
    cached = _fcache.get(c)
    if cached and _t.time() - cached[1] < _F_TTL:
        return cached[0]
    try:
        if c.endswith((".SH", ".SZ", ".BJ")):
            from services import ths_fuyao
            rows = await ths_fuyao.corp_actions_sync(c)
            if rows is None:
                return None
            ev = [{"ex_date": r["ex_date"], "pay_date": None, "per_share": r["per_share_cash"],
                   "currency": "CNY", "declared": f"每股派{r['per_share_cash']}元(税前)",
                   "source": "fuyao-ths"}
                  for r in rows if r["per_share_cash"] > 0]      # v1 只做现金派息; 送股比例暂不用
        elif c.endswith((".HK", ".US")):
            from services import futu_quote
            ev = await futu_quote.dividends_sync(futu_quote._to_futu_code(c))
            if ev is None and c.endswith(".US"):
                ev = await asyncio.to_thread(_us_fallback_sync, c)   # futu 拉不到 → 内置回退(仅 US)
        else:
            return None
    except Exception:
        return None
    ev = sorted(ev, key=lambda e: e["ex_date"])
    _fcache[c] = (ev, _t.time())
    return ev


def market_today(code: str) -> str:
    """交易所当地今天: 美股按美东、港股按香港时间。"""
    from zoneinfo import ZoneInfo
    tz = "America/New_York" if str(code).upper().endswith(".US") else "Asia/Hong_Kong"
    return _dt.datetime.now(ZoneInfo(tz)).date().isoformat()


# ── 共享件: 事件 × 流水的配对工具 ────────────────────────────────────────────

def shares_before(actions: list, day: str, fractional: bool = False) -> float:
    """除净日前一个交易日收盘时的持股: 回放 trade_date < day 的买卖/送股/拆合股流水。
    fractional=True(美股碎股)时保留 4 位小数, 合股零头也留着; 否则取整(零头折现金)。
    注: 建仓种子已统一为 OPEN/CLOSE(+direction), 与 BUY/SELL 同为增持/减持口径。"""
    from services.position_ledger import ACQUIRE, RELEASE, SPLIT, split_ratio
    acq, rel = ACQUIRE | {"OPEN"}, RELEASE | {"CLOSE"}
    sh = 0.0
    for a in sorted(actions, key=lambda a: (str(a.get("trade_date") or a.get("created_at") or "")[:10], a.get("id") or 0)):
        d = str(a.get("trade_date") or a.get("created_at") or "")[:10]
        if not d or d >= day:
            continue
        t = (a.get("action_type") or "").upper()
        if t in acq:
            sh += float(a.get("shares") or 0)
        elif t in rel:
            sh = max(0.0, sh - float(a.get("shares") or 0))
        elif t == SPLIT:
            sh = sh * (split_ratio(a) or 1)
            if not fractional:
                sh = float(int(sh + 1e-6))
    return round(sh, 4) if fractional else int(sh + 1e-6)


def _merge_same_day(events: list[dict]) -> list[dict]:
    """同一除净日的多项派息(如年度 + 特别)合成一笔: 券商流水和用户手记通常按日一笔。"""
    by: dict = {}
    for e in events:
        k = e["ex_date"]
        if k not in by:
            by[k] = dict(e, parts=[e])
            continue
        m = by[k]
        m["parts"].append(e)
        if m.get("per_share") is not None and e.get("per_share") is not None:
            m["per_share"] = round(m["per_share"] + e["per_share"], 6)
        else:
            m["per_share"] = None
        m["declared"] = " + ".join(p["declared"] for p in m["parts"])
        m["estimated"] = m.get("estimated") or e.get("estimated")
        m["pay_date"] = max(filter(None, [m.get("pay_date"), e.get("pay_date")]), default=None)
    return [by[k] for k in sorted(by)]


def _recorded_ex_dates(events: list[dict], actions: list) -> set:
    """已落账的分红: 每条 DIVIDEND 流水认领日期最近的一个事件(窗口: 除净日前 5 天 ~ 派息日后 45 天)。
    手记/自动写过的都算 —— 这是自动落账的去重键, 重跑不会双记。"""
    taken = set()
    for a in actions:
        if (a.get("action_type") or "").upper() != "DIVIDEND":
            continue
        d = str(a.get("trade_date") or a.get("created_at") or "")[:10]
        try:
            dd = _dt.date.fromisoformat(d)
        except ValueError:
            continue
        best, gap = None, None
        for e in events:
            ex = _dt.date.fromisoformat(e["ex_date"])
            anchor = _dt.date.fromisoformat(e["pay_date"]) if e.get("pay_date") else ex
            if not (ex - _dt.timedelta(days=5) <= dd <= anchor + _dt.timedelta(days=45)):
                continue
            g = abs((dd - anchor).days)
            if gap is None or g < gap:
                best, gap = e["ex_date"], g
        if best:
            taken.add(best)
    return taken


# ── 全自动落账管线 ────────────────────────────────────────────────────────────

async def sync_dividends(only_code: str | None = None) -> dict:
    """三市场统一: 分红事件自动落账为 DIVIDEND 流水(幂等, 可重复跑)。

    只处理 多头(LONG) 的股票持仓; A(SH/SZ/BJ)/HK/US 各有适配器(见 _events_for)。
    返回 {written: [...], skipped: n, errors: [...]}。
    """
    from database import get_all_holdings, get_position_actions, add_position_action
    from services.market_data import quote_auto_fetch, code_fx_on
    if not quote_auto_fetch():
        return {"written": [], "skipped": 0,
                "errors": ["纯PG模式(quote_auto_fetch=false): 不拉分红数据源, 分红请手记 DIVIDEND 流水"]}
    holdings = [h for h in await get_all_holdings()
                if (h.get("asset_kind") or "stock") == "stock"
                and (h.get("direction") or "LONG") != "SHORT"          # 空头: 借券分红是支出, 跳过(手记)
                and str(h.get("stock_code") or "").upper().endswith(_SOURCED_SUFFIX)]
    if only_code:
        holdings = [h for h in holdings if h["stock_code"].upper() == only_code.upper()]
    written: list[dict] = []
    skipped = 0
    errors: list[str] = []

    for h in holdings:
        code = h["stock_code"]
        hdir = (h.get("direction") or "LONG").upper()
        # 只看本方向账本的流水(去重匹配与持股回放都用它; 旧无方向流水视同 LONG)
        acts = [a for a in await get_position_actions(code, limit=500)
                if (a.get("direction") or "LONG").upper() == hdir]
        if not acts:
            continue
        ev = await _events_for(code)
        if ev is None:
            errors.append(f"{code}: 分红数据源不可达")
            continue
        evs = _merge_same_day(ev)
        recorded = _recorded_ex_dates(evs, acts)
        today = market_today(code)
        for e in evs:
            ex = e["ex_date"]
            if ex > today or ex in recorded:
                skipped += 1
                continue
            sh = shares_before(acts, ex, fractional=code.endswith(".US"))
            if sh <= 1e-9:
                skipped += 1
                continue
            ps = e.get("per_share")
            if not ps or ps <= 0:
                errors.append(f"{code} {ex}: 事件缺每股金额(可能需手工按结单记)")
                continue
            pay = e.get("pay_date") or ex
            fx = None
            if code.endswith((".HK", ".US")):
                fx = await asyncio.to_thread(code_fx_on, code, pay)
            await add_position_action(
                code, "DIVIDEND", ps, sh,
                trade_date=pay,
                note=f"分红自动同步 ({e.get('source') or 'source'}: 除净 {ex})",
                fee=None,                      # 不建税模型: 金额/费可在流水编辑里手改
                fx_rate=fx,
                direction=hdir,
            )
            written.append({"stock_code": code, "ex_date": ex, "shares": sh,
                            "per_share": ps, "amount": round(ps * sh, 2), "trade_date": pay})
    return {"written": written, "skipped": skipped, "errors": errors}
