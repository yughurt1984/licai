"""派息全自动落账(三市场统一) + 数据源工具测试(网络全 mock)。"""
import asyncio
import json

import pytest

import services.dividends as dv
from services.market_fees import order_fee
from services.position_ledger import attach_auto_fees

def _a(t, price, shares, day, **kw):
    return {"action_type": t, "price": price, "shares": shares, "trade_date": day, **kw}


def _async_events(events):
    """monkeypatch 用: _events_for 的假实现(同步数据 → async 形状)。"""
    async def _f(code):
        return events
    return _f


# ---------- 数据源 ----------

def test_yahoo_amounts_unadjusted_by_later_splits():
    # NVDA: 2024-03 每股 0.04, Yahoo 按 2024-06 的 1 拆 10 复权成 0.004
    divs = {"1709649000": {"amount": 0.004, "date": 1709649000},       # 2024-03-05
            "1726147800": {"amount": 0.01, "date": 1726147800}}        # 2024-09-12(拆股之后)
    splits = {"1718026200": {"date": 1718026200, "numerator": 10.0, "denominator": 1.0}}
    ev = dv.yahoo_unadjust(divs, splits, gmtoffset=-14400)
    assert [(e["ex_date"], e["per_share"], e["pay_date"]) for e in ev] == \
        [("2024-03-05", 0.04, None), ("2024-09-12", 0.01, None)]


def test_us_fallback_nasdaq_then_yahoo(monkeypatch):
    """US 回退源: Nasdaq 主, 失败回退 Yahoo; 都挂 → None。"""
    dv._fcache.clear()
    monkeypatch.setattr(dv, "_us_nasdaq_sync", lambda s: [{"ex_date": "2026-09-15", "pay_date": "2026-10-15",
                                                          "per_share": 0.53, "source": "nasdaq"}])
    assert dv._us_fallback_sync("KO.US")[0]["source"] == "nasdaq"
    monkeypatch.setattr(dv, "_us_nasdaq_sync", lambda s: None)
    monkeypatch.setattr(dv, "_us_yahoo_sync", lambda s: [{"ex_date": "2026-09-15", "pay_date": None,
                                                         "per_share": 0.53, "source": "yahoo"}])
    assert dv._us_fallback_sync("KO.US")[0]["source"] == "yahoo"
    monkeypatch.setattr(dv, "_us_yahoo_sync", lambda s: (_ for _ in ()).throw(OSError("down")))
    assert dv._us_fallback_sync("KO.US") is None


def test_a_share_events_from_fuyao(monkeypatch):
    """A 股适配器: fuyao 除权除息行 → 统一事件(CNY, 无派息日, 只留现金分红)。"""
    async def fake_corp(thscode):
        assert thscode == "600519.SH"
        return [{"ex_date": "2025-12-19", "per_share_cash": 23.957, "per_share_bonus": 0.0},
                {"ex_date": "2025-06-20", "per_share_cash": 0.0, "per_share_bonus": 0.1}]  # 纯送股 → 滤掉
    from services import ths_fuyao
    monkeypatch.setattr(ths_fuyao, "corp_actions_sync", fake_corp)
    dv._fcache.clear()
    ev = asyncio.run(dv._events_for("600519.SH"))
    assert ev == [{"ex_date": "2025-12-19", "pay_date": None, "per_share": 23.957,
                   "currency": "CNY", "declared": "每股派23.957元(税前)", "source": "fuyao-ths"}]
    # 拉不到 → None
    async def fake_none(thscode):
        return None
    monkeypatch.setattr(ths_fuyao, "corp_actions_sync", fake_none)
    dv._fcache.clear()
    assert asyncio.run(dv._events_for("600519.SH")) is None


def test_futu_dividends_adapter(monkeypatch):
    """富途分红适配器: 只留 Implementation, 日期斜杠转 ISO, 币种/方案透传。"""
    from services import futu_quote

    class R:
        status_code = 200
        def __init__(self, j):
            self._j = j
        def json(self):
            return self._j

    def fake_get(url, headers=None, timeout=None):
        assert "HK.00700/corporate-actions/dividends" in url
        return R({"ret_code": 0, "data": {"dividend_list": [
            {"statement": "Cash Dividend: 5.30000 HKD Per Share", "dividend_per_share": 5.3,
             "currency": "HKD", "process": "Implementation", "ex_date": "2026/05/15",
             "dividend_payable_date": "2026/06/01"},
            {"statement": "Plan", "dividend_per_share": 9.9, "currency": "HKD",
             "process": "Plan", "ex_date": "2026/09/01", "dividend_payable_date": None},
        ]}})

    monkeypatch.setattr(futu_quote._session, "get", fake_get)
    monkeypatch.setattr(futu_quote, "futu_config", lambda: asyncio.sleep(0, result={}))
    ev = asyncio.run(futu_quote.dividends_sync("HK.00700"))
    assert ev == [{"ex_date": "2026-05-15", "pay_date": "2026-06-01", "per_share": 5.3,
                   "currency": "HKD", "declared": "Cash Dividend: 5.30000 HKD Per Share",
                   "source": "futu"}]


def test_dispatch_routes_by_market(monkeypatch):
    """分发: A→fuyao, 港美→futu; futu 挂了 US 回退内置; 未知后缀 → None。"""
    async def fake_a(thscode):
        return [{"ex_date": "2025-12-19", "per_share_cash": 23.957, "per_share_bonus": 0.0}]
    async def fake_futu(futu_code):
        assert futu_code in ("HK.00700", "US.AAPL")
        return [{"ex_date": "2026-05-15", "pay_date": None, "per_share": 5.3,
                 "currency": "HKD", "declared": "x", "source": "futu"}]
    from services import ths_fuyao
    monkeypatch.setattr(ths_fuyao, "corp_actions_sync", fake_a)
    # dividends.py 把适配器导入成了模块级名字(futu_dividends_sync), patch 点在 dv 上
    monkeypatch.setattr(dv, "futu_dividends_sync", fake_futu)
    dv._fcache.clear()
    assert asyncio.run(dv._events_for("600519.SH"))[0]["source"] == "fuyao-ths"
    assert asyncio.run(dv._events_for("00700.HK"))[0]["source"] == "futu"
    assert asyncio.run(dv._events_for("AAPL.US"))[0]["source"] == "futu"
    dv._fcache.clear()
    assert asyncio.run(dv._events_for("XX.ZZ")) is None        # 未知后缀


# ---------- 持股/去重工具 ----------

def test_shares_before_ex_date_strict_and_splits():
    acts = [_a("BUY", 100, 100, "2026-01-05"), _a("SELL", 110, 40, "2026-03-01"),
            _a("SPLIT", 4, 0, "2026-04-01"), _a("BUY", 30, 10, "2026-05-15")]
    assert dv.shares_before(acts, "2026-03-01") == 100               # 除净日当天卖出仍拿分红
    assert dv.shares_before(acts, "2026-03-02") == 60
    assert dv.shares_before(acts, "2026-05-15") == 240               # 除净日当天买入拿不到
    assert dv.shares_before(acts, "2026-05-16") == 250


def test_one_dividend_row_claims_only_nearest_event():
    """去重键: 手记的 DIVIDEND 流水按日期窗口认领最近事件, 不让别的事件双记。"""
    acts = [_a("BUY", 50, 100, "2025-01-02"), _a("DIVIDEND", 0.78, 100, "2026-06-26")]
    ev = [{"ex_date": "2026-05-14", "pay_date": "2026-06-26", "per_share": 0.78, "currency": "HKD",
           "declared": "x", "source": "t"},
          {"ex_date": "2026-08-13", "pay_date": "2026-09-25", "per_share": 0.78, "currency": "HKD",
           "declared": "x", "source": "t"}]
    evs = dv._merge_same_day(ev)
    recorded = dv._recorded_ex_dates(evs, acts)
    assert recorded == {"2026-05-14"}


# ---------- 全自动落账管线 ----------

@pytest.fixture
def fresh_db(temp_db):
    import database as db
    return db


def _seed_us_holding(db, direction="LONG", shares=10, fx=7.1):
    asyncio.run(db.add_holding("AAPL.US", "Apple", 0, 0, direction=direction))
    asyncio.run(db.add_position_action("AAPL.US", "OPEN", 200.0, shares,
                                       trade_date="2026-01-05", fx_rate=fx, direction=direction))
    return {"ex_date": "2026-02-10", "pay_date": "2026-02-25", "per_share": 0.25,
            "currency": "USD", "declared": "$0.25", "source": "nasdaq"}


def test_sync_writes_dividend_once(fresh_db, monkeypatch):
    """US 多头持仓 + 数据源事件 → 自动写 DIVIDEND(除权前持股, 到账日记账, 按到账日汇率, fee 空)。"""
    db = fresh_db
    ev = _seed_us_holding(db)
    monkeypatch.setattr(dv, "_events_for", _async_events([ev]))
    monkeypatch.setattr("services.market_data.code_fx_on", lambda code, day: 7.12)
    monkeypatch.setattr("services.market_data.quote_auto_fetch", lambda: True)

    r = asyncio.run(dv.sync_dividends())
    assert len(r["written"]) == 1
    w = r["written"][0]
    assert (w["stock_code"], w["shares"], w["per_share"], w["trade_date"]) == \
        ("AAPL.US", 10, 0.25, "2026-02-25")

    acts = [a for a in asyncio.run(db.get_position_actions("AAPL.US")) if a["action_type"] == "DIVIDEND"]
    assert len(acts) == 1
    d = acts[0]
    assert d["price"] == 0.25 and d["shares"] == 10
    assert d["trade_date"] == "2026-02-25" and float(d["fx_rate"]) == 7.12
    assert d["fee"] is None and d["direction"] == "LONG"
    assert "自动同步" in d["note"]

    # 幂等: 重跑不双记
    r2 = asyncio.run(dv.sync_dividends())
    assert r2["written"] == []
    assert len([a for a in asyncio.run(db.get_position_actions("AAPL.US"))
                if a["action_type"] == "DIVIDEND"]) == 1


def test_sync_dedup_respects_manual_records(fresh_db, monkeypatch):
    """手记过的分红(按到账日窗口认领事件)不双记; 窗口外的其他事件照写。"""
    db = fresh_db
    ev = _seed_us_holding(db)
    # 手记一笔: 到账日 2026-02-27(事件 pay 02-25 → 窗口内, 认领这个事件)
    asyncio.run(db.add_position_action("AAPL.US", "DIVIDEND", 0.25, 10,
                                       trade_date="2026-02-27", fx_rate=7.12, direction="LONG"))
    monkeypatch.setattr(dv, "_events_for", _async_events([ev]))
    monkeypatch.setattr("services.market_data.code_fx_on", lambda code, day: 7.12)
    monkeypatch.setattr("services.market_data.quote_auto_fetch", lambda: True)

    r = asyncio.run(dv.sync_dividends())
    assert r["written"] == []
    assert len([a for a in asyncio.run(db.get_position_actions("AAPL.US"))
                if a["action_type"] == "DIVIDEND"]) == 1       # 只有手记那条


def test_sync_skips_short_and_unsourced(fresh_db, monkeypatch):
    """空头持仓跳过(借券分红是支出); A 股无适配器跳过(等用户数据源)。"""
    db = fresh_db
    ev = _seed_us_holding(db, direction="SHORT")
    asyncio.run(db.add_holding("600519.SH", "贵州茅台", 0, 0, direction="LONG"))
    asyncio.run(db.add_position_action("600519.SH", "OPEN", 1500.0, 100,
                                       trade_date="2026-01-05", direction="LONG"))
    async def _f(code):
        return [ev] if code.endswith(".US") else None
    monkeypatch.setattr(dv, "_events_for", _f)
    monkeypatch.setattr("services.market_data.code_fx_on", lambda code, day: 7.12)
    monkeypatch.setattr("services.market_data.quote_auto_fetch", lambda: True)

    r = asyncio.run(dv.sync_dividends())
    assert r["written"] == []
    assert not [a for a in asyncio.run(db.get_position_actions("AAPL.US"))
                if a["action_type"] == "DIVIDEND"]
    assert not [a for a in asyncio.run(db.get_position_actions("600519.SH"))
                if a["action_type"] == "DIVIDEND"]


def test_sync_skips_future_and_post_sale_events(fresh_db, monkeypatch):
    """除净未到 → 跳过; 除权时已清仓(除净前无持股) → 跳过。"""
    db = fresh_db
    asyncio.run(db.add_holding("AAPL.US", "Apple", 0, 0, direction="LONG"))
    # 1-05 买 10 → 1-20 全平: 2 月除净时已无持股
    asyncio.run(db.add_position_action("AAPL.US", "OPEN", 200.0, 10,
                                       trade_date="2026-01-05", direction="LONG"))
    asyncio.run(db.add_position_action("AAPL.US", "CLOSE", 210.0, 10,
                                       trade_date="2026-01-20", direction="LONG"))
    events = [{"ex_date": "2026-02-10", "pay_date": "2026-02-25", "per_share": 0.25,
               "currency": "USD", "declared": "$0.25", "source": "nasdaq"},
              {"ex_date": "2027-02-10", "pay_date": "2027-02-25", "per_share": 0.3,
               "currency": "USD", "declared": "$0.3", "source": "nasdaq"}]     # 除净未到
    monkeypatch.setattr(dv, "_events_for", _async_events(events))
    monkeypatch.setattr("services.market_data.code_fx_on", lambda code, day: 7.12)
    monkeypatch.setattr("services.market_data.quote_auto_fetch", lambda: True)

    r = asyncio.run(dv.sync_dividends())
    assert r["written"] == [] and r["skipped"] >= 2


# ---------- 汇率工具(派息按到账日取价) ----------

def test_fx_rate_on_dates(monkeypatch):
    import services.market_data as md
    hist = {"2026-09-30": 7.10, "2026-10-08": 7.12}
    monkeypatch.setattr(md, "fx_history", lambda c: hist if c == "USD" else {})
    monkeypatch.setattr(md, "get_fx_rate", lambda c: 7.30)
    assert md.fx_rate_on("USD", "2026-10-05") == 7.10           # 国庆假期 → 前一个工作日
    assert md.fx_rate_on("USD", "2026-10-08") == 7.12
    assert md.fx_rate_on("USD", "2099-01-01") == 7.30           # 今天及以后 = 实时
    assert md.fx_rate_on("HKD", "2026-10-08") == 7.30           # 历史取不到 → 实时兜底
    assert md.code_fx_on("600000.SH", "2026-10-08") is None
    assert md.code_fx_on("AAPL.US", "2026-10-08") == 7.12


# ---------- 费用: 持仓券商、按成交日的规费 ----------

def test_attach_auto_fees_uses_holding_broker_schedule():
    ovr = json.dumps({"某券商": {"US": {"commission_per_share": 0.0, "commission_min": 0.0,
                                         "platform_per_share": 0.0, "platform_min": 9.0}}})
    acts = [{"stock_code": "US.AAPL", "action_type": "BUY", "price": 100.0, "shares": 10,
             "trade_date": "2026-06-01", "trade_time": "22:00"}]
    asyncio.run(attach_auto_fees(acts, "US.AAPL", "某券商", resolve=lambda n, m=None: (None, None), fee_overrides=ovr))
    assert acts[0]["_auto_fee"] == 9.0
    # 本笔自己写了券商(盈立)就按本笔
    acts2 = [dict(acts[0], broker="盈立证券")]
    asyncio.run(attach_auto_fees(acts2, "US.AAPL", "某券商", resolve=lambda n, m=None: (None, None), fee_overrides=ovr))
    assert acts2[0]["_auto_fee"] == order_fee("US", "buy", 1000.0, 10, "2026-06-01")
    # 两个券商同一分钟的单不合并成一张委托
    acts3 = [dict(acts[0]), dict(acts[0], broker="盈立证券")]
    asyncio.run(attach_auto_fees(acts3, "US.AAPL", "某券商", resolve=lambda n, m=None: (None, None), fee_overrides=ovr))
    assert acts3[0]["_auto_fee"] == 9.0 and acts3[1]["_auto_fee"] == acts2[0]["_auto_fee"]
