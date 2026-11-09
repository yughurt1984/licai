"""ths(fuyao) 源适配器: 三路分流(期货/期权逐只、场内基金批量、A股批量) + ETF 判定。

背景: 场内基金(cn_etf_fund)过去被喂进 A股快照端点(/api/a-share/prices/snapshot),
上游返回 3001 Target not found —— 现在 ETF/LOF 码走基金快照端点(/api/fund/market/snapshot,
支持 100 个批量)。这里用打桩的 _get 钉住分流与解析, 不打真网。
"""
import asyncio

from services import ths_fuyao as T


class _FakeGet:
    """记录 (path, params) 调用并按路径返回假响应。"""
    def __init__(self, responses):
        self.responses = responses          # {path: data}
        self.calls = []

    async def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        d = self.responses[path]
        if isinstance(d, Exception):
            raise d
        return d


_A_SHARE = {"item": [{"thscode": "600406.SH", "last_price": 8.5, "prev_price": 8.4,
                      "price_change_ratio_pct": 1.19, "open_price": 8.4, "high_price": 8.6,
                      "low_price": 8.3, "volume": 100, "turnover": 850}]}
_FUND = {"item": [{"thscode": "513310.SH", "last_price": 3.787, "prev_price": 3.922,
                   "price_change_ratio_pct": -3.44, "open_price": 3.975, "high_price": 4.08,
                   "low_price": 3.759, "volume": 100, "turnover": 378.7}],
         "timestamp": 1793930400000}


def test_is_etf_lof_shapes():
    assert T.is_etf_lof("513310.SH") and T.is_etf_lof("159915.SZ") and T.is_etf_lof("510300.SH")
    assert not T.is_etf_lof("600406.SH") and not T.is_etf_lof("CU2601.SHF") and not T.is_etf_lof("00700.HK")


def test_snapshot_rows_routes_etf_to_fund_endpoint(monkeypatch):
    """ETF 码必须走 /api/fund/market/snapshot(批量), 不能混进 A股快照端点。"""
    fake = _FakeGet({"/api/fund/market/snapshot": _FUND, "/api/a-share/prices/snapshot": _A_SHARE})
    monkeypatch.setattr(T, "_get", fake)
    rows = asyncio.run(T.snapshot_rows(["513310.SH", "600406.SH"]))
    paths = [p for p, _ in fake.calls]
    assert "/api/fund/market/snapshot" in paths and "/api/a-share/prices/snapshot" in paths
    assert all("513310" not in (params.get("thscodes") or "") for p, params in fake.calls
               if p == "/api/a-share/prices/snapshot")            # ETF 不进 A股端点
    by_code = {r.code: r for r in rows}
    assert by_code["513310.SH"].price == 3.787 and by_code["513310.SH"].source == "ths"
    assert by_code["600406.SH"].price == 8.5


def test_fund_snapshots_batch_parse(monkeypatch):
    """批量解析: code/quote_time(ms→北京时间文本)/涨跌幅字段齐全。"""
    fake = _FakeGet({"/api/fund/market/snapshot": _FUND})
    monkeypatch.setattr(T, "_get", fake)
    rows = asyncio.run(T.fund_snapshots(["513310.SH", "159915.SZ"]))   # 159915 上游无报价会被省略
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "513310.SH" and r["price"] == 3.787 and r["prev_close"] == 3.922
    assert r["quote_time"] == "2026-11-06 10:00:00"    # ms → 北京时间(见 _FUND.timestamp)


def test_fund_snapshot_single_wrapper(monkeypatch):
    """单只薄壳与 external_assets 的消费形状兼容。"""
    fake = _FakeGet({"/api/fund/market/snapshot": _FUND})
    monkeypatch.setattr(T, "_get", fake)
    q = asyncio.run(T.fund_snapshot("513310.SH"))
    assert q["price"] == 3.787 and "code" not in q and "quote_time" in q
