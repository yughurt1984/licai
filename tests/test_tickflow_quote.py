"""tickflow 源: 代码映射(标准码↔tickflow symbol)、响应解析 → QuoteRow、批量分块、未配置兜底。"""
import json
from datetime import datetime, timezone

from services import tickflow as T
from services.quote_source import QuoteRow


# ── 代码映射 ───────────────────────────────────────────────────────────────
def test_to_tf_symbol_golden():
    assert T.to_tf_symbol("600406.SH") == "600406.SH"      # A股格式与 tickflow 一致
    assert T.to_tf_symbol("000001.SZ") == "000001.SZ"
    assert T.to_tf_symbol("920001.BJ") == "920001.BJ"
    assert T.to_tf_symbol("HK.00700") == "00700.HK"
    assert T.to_tf_symbol("US.AAPL") == "AAPL.US"
    # 不支持的形态: 期货 thscode / 场外基金 / 裸码
    assert T.to_tf_symbol("CU2601.SHF") == ""
    assert T.to_tf_symbol("019144.OF") == ""
    assert T.to_tf_symbol("600406") == ""
    assert T.to_tf_symbol("") == ""


def test_from_tf_symbol_roundtrip():
    assert T._from_tf_symbol("700.HK") == "00700.HK"      # 港股补零到 5 位(标准码「代码.市场」)
    assert T._from_tf_symbol("00700.HK") == "00700.HK"
    assert T._from_tf_symbol("AAPL.US") == "AAPL.US"
    assert T._from_tf_symbol("AAPL.US") == "AAPL.US"
    assert T._from_tf_symbol("600406.SH") == "600406.SH"
    assert T._from_tf_symbol("CU2601.SHF") == ""           # 未知后缀不认
    # 标准码 → tickflow → 标准码 恒等
    for code in ("600406.SH", "000001.SZ", "920001.BJ", "00700.HK", "SNDK.US"):
        assert T._from_tf_symbol(T.to_tf_symbol(code)) == code


# ── 解析 ───────────────────────────────────────────────────────────────────
def _quote_payload(symbol="600406.SH", **over):
    base = {
        "symbol": symbol, "region": "CN",
        "last_price": 12.34, "prev_close": 12.00, "open": 12.10,
        "high": 12.50, "low": 12.00, "volume": 123456, "amount": 1.5e6,
        "timestamp": int(datetime(2026, 11, 5, 2, 0, 0, tzinfo=timezone.utc).timestamp() * 1000),
        "session": "regular",
        "ext": {"type": "cn_equity", "name": "格林美", "change_pct": 0.0283,
                "change_amount": 0.34, "amplitude": 0.0417, "turnover_rate": 0.015},
    }
    base.update(over)
    return base


def test_parse_quote_percent_and_time():
    q = T._parse_quote(_quote_payload())
    assert q["price"] == 12.34 and q["prev_close"] == 12.00
    assert q["change_pct"] == 2.83          # 0.0283 → 2.83%
    assert q["turnover_rate"] == 1.5        # 0.015 → 1.5%
    assert q["quote_time"] == "2026-11-05 10:00:00"   # 北京时间
    assert q["market"] == "A" and q["currency"] == "CNY"
    assert q["code"] == "600406.SH" and q["name"] == "格林美"


def test_parse_quote_change_pct_fallback():
    # ext 缺 change_pct → last/prev 反推
    p = _quote_payload()
    p["ext"] = {"type": "cn_equity", "name": "x"}
    q = T._parse_quote(p)
    assert q["change_pct"] == round((12.34 - 12.00) / 12.00 * 100, 3)


def test_parse_quote_hk_us_shapes():
    hk = T._parse_quote(_quote_payload(symbol="00700.HK", region="HK"))
    assert hk["market"] == "HK" and hk["currency"] == "HKD" and hk["code"] == "00700.HK"
    us = T._parse_quote(_quote_payload(symbol="SNDK.US", region="US"))
    assert us["market"] == "US" and us["currency"] == "USD" and us["code"] == "SNDK.US"


def test_parse_quote_zero_price_rejected():
    assert T._parse_quote(_quote_payload(last_price=0)) is None


# ── HTTP / 批量分块(打桩) ──────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""
    def json(self):
        return self._payload


def test_quotes_sync_batches_over_100(monkeypatch):
    calls = []
    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        syms = params["symbols"].split(",")
        return _FakeResp({"data": [{"symbol": s, "last_price": 1.0, "prev_close": 1.0,
                                    "timestamp": 0, "ext": {}} for s in syms]})
    monkeypatch.setattr(T, "_session", type("S", (), {"get": staticmethod(fake_get)}))
    syms = [f"6000{i:02d}.SH" for i in range(250)]
    out = T._quotes_sync(syms, "k", "https://x")
    assert len(out) == 250 and len(calls) == 3
    assert len(calls[0][1]["symbols"].split(",")) == 100
    assert calls[0][2]["x-api-key"] == "k"
    assert calls[0][0].startswith("https://x/v1/quotes")


def test_quotes_sync_http_error_raises(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp({}, status=401)
    monkeypatch.setattr(T, "_session", type("S", (), {"get": staticmethod(fake_get)}))
    try:
        T._quotes_sync(["600406.SH"], "bad", "https://x")
        assert False, "应当抛出"
    except RuntimeError as e:
        assert "401" in str(e)


# ── 源适配器(协议契约) ─────────────────────────────────────────────────────
def _patch_async_cfg(monkeypatch, key, host="https://api.tickflow.org"):
    async def fake_key():
        return key
    async def fake_host():
        return host
    monkeypatch.setattr(T, "_api_key", fake_key)
    monkeypatch.setattr(T, "_host", fake_host)


def test_source_adapter_never_raises(monkeypatch):
    """协议契约: 未配置 key / 网络失败表现为空列表, 绝不抛。"""
    import asyncio
    # 未配置 key + 默认付费 host → 静默空返回, 不发请求
    _patch_async_cfg(monkeypatch, key="")
    monkeypatch.setattr(T, "_session", type("S", (), {"get": staticmethod(
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应发请求")))}))
    assert asyncio.run(T.SOURCE.snapshot(["600406.SH", "CU2601.SHF"])) == []

    # 网络失败 → 空列表
    _patch_async_cfg(monkeypatch, key="k")
    def boom(*a, **k):
        raise ConnectionError("refused")
    monkeypatch.setattr(T, "_session", type("S", (), {"get": staticmethod(boom)}))
    assert asyncio.run(T.SOURCE.snapshot(["600406.SH"])) == []


def test_source_adapter_translates_rows(monkeypatch):
    """快照 → QuoteRow: 码转回标准格式, 市场分组/币种/扩展字段齐。"""
    import asyncio
    _patch_async_cfg(monkeypatch, key="k")
    data = {"data": [_quote_payload(), _quote_payload(symbol="00700.HK", region="HK",
                                                      ext={"type": "hk_equity", "name": "腾讯控股",
                                                           "change_pct": 0.01, "turnover_rate": 0.002})]}
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp(data)
    monkeypatch.setattr(T, "_session", type("S", (), {"get": staticmethod(fake_get)}))
    rows = asyncio.run(T.SOURCE.snapshot(["600406.SH", "00700.HK", "CU2601.SHF"]))
    assert [r.code for r in rows] == ["600406.SH", "00700.HK"]     # 期货码被过滤, 不在返回里
    a = rows[0]
    assert isinstance(a, QuoteRow) and a.market == "A" and a.price == 12.34
    assert a.currency == "CNY" and a.source == "tickflow" and a.name == "格林美"
    assert a.info.get("turnover_rate") == 1.5 and a.info.get("session") == "regular"
    hk = rows[1]
    assert hk.market == "HK" and hk.currency == "HKD" and hk.price == 12.34


def test_reits_quote_uses_quotes(monkeypatch):
    """REITs 报价复用批量 quotes(单只), 返回基金报价形状。"""
    import asyncio
    async def fake_quotes(symbols):
        assert symbols == ["508056.SH"]
        return [{"symbol": "508056.SH", "code": "508056.SH", "market": "A", "currency": "CNY",
                 "price": 3.45, "prev_close": 3.40, "change_pct": 1.47, "open": 3.41,
                 "high": 3.47, "low": 3.40, "volume": 9e5, "amount": 3.1e6,
                 "name": "红土创新盐田港REIT", "quote_time": "2026-11-05 10:00:00",
                 "session": "regular", "turnover_rate": 0.5}]
    monkeypatch.setattr(T, "quotes", fake_quotes)
    q = asyncio.run(T.reits_quote("508056"))
    assert q["price"] == 3.45 and q["name"] == "红土创新盐田港REIT"
    assert q["change_pct"] == 1.47 and q["quote_time"] == "2026-11-05 10:00:00"
