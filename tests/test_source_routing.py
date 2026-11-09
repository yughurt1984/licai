"""报价路由: assets_classify 报价双源(main → back) + sina 全局兜底(选A, 仅股/ETF/港美股)。

钉住: 双源读取与归一、链拼装(未配置的 sub 只剩 sina; 期货/期权不加 sina)、main 命中即停、
main 未命中落 back、全落空才到 sina、未知源名告警跳过。
"""
import asyncio

import pytest

from services.quote_source import QuoteRow

# temp_db 由 tests/conftest.py 提供(指向每次重建的 PG 测试库)

_SUBS = [
    ("hk_comm_stock", "tickflow", ""),          # main 命中即停
    ("us_comm_stock", "TICKFLOW", "futu"),      # 大写归一; main 未命中 → back
    ("cn_futures", "ths", ""),                  # 期货: 无 sina 兜底
    ("hk_mm_fund", "yhf", ""),                  # 未知源: 告警跳过 → sina
    ("cn_etf_fund", "", ""),                    # 双源都没配: 链只剩 sina
]


async def _seed_classify():
    from tests.conftest import insert_rows
    await insert_rows("assets_classify", [
        {"assets_class": "stock", "assets_class_name": "股票", "main_assets_type": "common_share",
         "assets_type_name": "普通股", "sub_assets_type": sub,
         "sub_assets_type_name": sub, "main_quote_source": main, "back_quote_source": back}
        for sub, main, back in _SUBS])


async def _seed_dict(rows):
    from tests.conftest import insert_rows
    await insert_rows("symbol_dict", [
        {"code": code, "name": name, "ticker": ticker, "sub_assets_type": sub}
        for code, name, ticker, sub in rows])


class FakeSource:
    """记录调用的假源适配器。"""
    def __init__(self, name, rows=(), fail=False):
        self.name = name
        self._rows = list(rows)
        self._fail = fail
        self.calls = []

    async def snapshot(self, codes):
        self.calls.append(list(codes))
        if self._fail:
            raise RuntimeError(f"{self.name} 挂了")
        return [r for r in self._rows if r.code in codes]


def _wire(monkeypatch, sources):
    """把假源接进 market_data 注册表(monkeypatch 自动还原, 不泄漏给别的测试)。"""
    from services import market_data as md
    monkeypatch.setattr(md, "_source_registry", lambda: {s.name: s for s in sources})
    return md


@pytest.fixture(autouse=True)
def _fresh_route_cache():
    from services.market_data import invalidate_data_source_cache
    invalidate_data_source_cache()
    yield


def test_classify_sources_reads_pairs(temp_db):
    asyncio.run(_seed_classify())
    from services.market_data import _classify_sources

    src_map = asyncio.run(_classify_sources())
    assert src_map["hk_comm_stock"] == ("tickflow", "")
    assert src_map["us_comm_stock"] == ("tickflow", "futu")     # 大写归一
    assert "cn_etf_fund" not in src_map               # 双源都没配不进表(链只剩 sina)


def test_classify_sources_serves_cache_without_db(temp_db):
    from services import market_data as md

    cached = {"hk_comm_stock": ("tickflow", "")}
    md._cls_source_cache = [cached, md.time.time() + 3600]       # 未过期
    assert asyncio.run(md._classify_sources()) is cached


def test_code_sub_map_standard_probes(temp_db):
    """全库统一「代码.市场」后, 内部码与底稿同构, 单形态直探。"""
    asyncio.run(_seed_dict([
        ("00700.HK", "腾讯控股", "00700", "hk_comm_stock"),
        ("SNDK.US", "闪迪", "SNDK", "us_comm_stock"),
    ]))
    from services.market_data import _code_sub_map

    out = asyncio.run(_code_sub_map(["00700.HK", "SNDK.US", "999999.SH"]))
    assert out["00700.HK"] == "hk_comm_stock"
    assert out["SNDK.US"] == "us_comm_stock"
    assert "999999.SH" not in out                      # 查不到 → 不指派, 落 sina 兜底

    out2 = asyncio.run(_code_sub_map(["00700.HK"], stored={"00700.HK": {"sub_assets_type": "hk_etf_fund"}}))
    assert out2["00700.HK"] == "hk_etf_fund"           # 已存行情行的小类优先


def test_fetch_main_hit_stops(temp_db, monkeypatch):
    """main(tickflow) 拿到 → back/sina 一个都不调。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("00700.HK", "腾讯控股", "00700", "hk_comm_stock")]))
    tf = FakeSource("tickflow", [QuoteRow(code="00700.HK", market="HK", price=500.0, source="tickflow")])
    sina = FakeSource("sina")
    md = _wire(monkeypatch, [tf, sina])

    rows = asyncio.run(md._fetch_group_rows("HK", ["00700.HK"]))
    assert tf.calls == [["00700.HK"]]
    assert sina.calls == []
    assert [r.code for r in rows] == ["00700.HK"] and rows[0].source == "tickflow"


def test_fetch_main_miss_goes_back(temp_db, monkeypatch):
    """main 未命中 → back 接手; back 拿到 → sina 不调。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("SNDK.US", "闪迪", "SNDK", "us_comm_stock")]))
    tf = FakeSource("tickflow")                        # main: 空返回
    futu = FakeSource("futu", [QuoteRow(code="SNDK.US", market="US", price=800.0, source="futu")])
    sina = FakeSource("sina")
    md = _wire(monkeypatch, [tf, futu, sina])

    rows = asyncio.run(md._fetch_group_rows("US", ["SNDK.US"]))
    assert tf.calls and futu.calls == [["SNDK.US"]] and sina.calls == []
    assert rows[0].source == "futu"


def test_fetch_all_miss_falls_to_sina(temp_db, monkeypatch):
    """main+back 都没拿到 → sina 全局兜底(选A)。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("SNDK.US", "闪迪", "SNDK", "us_comm_stock")]))   # main=tickflow, back=futu
    tf = FakeSource("tickflow")
    futu = FakeSource("futu")
    sina = FakeSource("sina", [QuoteRow(code="SNDK.US", market="US", price=480.0, source="sina")])
    md = _wire(monkeypatch, [tf, futu, sina])

    rows = asyncio.run(md._fetch_group_rows("US", ["SNDK.US"]))
    assert tf.calls and futu.calls and sina.calls == [["SNDK.US"]]
    assert rows[0].source == "sina"


def test_fetch_futures_no_sina_level(temp_db, monkeypatch):
    """期货码: cn_futures main=ths('fuyao' 适配器) → ths 空 → 链尽(无 sina 级), 空返回不报错。"""
    asyncio.run(_seed_classify())
    ths = FakeSource("ths")                            # 注册表按源名取(生产里 ths 别名 → fuyao)
    sina = FakeSource("sina")
    md = _wire(monkeypatch, [ths, sina])

    rows = asyncio.run(md._fetch_group_rows("FUT", ["CU2601.SHF"]))
    assert ths.calls == [["CU2601.SHF"]]
    assert sina.calls == []                            # 期货无 sina 兜底级
    assert rows == []


def test_fetch_unknown_source_warns_and_skips(temp_db, monkeypatch):
    """指派了未注册的源(yhf) → 告警一次, 该级跳过, 码继续走链上剩余源(sina)。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("02828.HK", "盈富基金-货币", "02828", "hk_mm_fund")]))
    sina = FakeSource("sina", [QuoteRow(code="02828.HK", market="HK", price=25.0, source="sina")])
    md = _wire(monkeypatch, [sina])
    md._unknown_src_warned.clear()

    rows = asyncio.run(md._fetch_group_rows("HK", ["02828.HK"]))
    assert "yhf" in md._unknown_src_warned
    assert rows[0].source == "sina"


def test_fetch_unconfigured_sub_uses_sina_only(temp_db, monkeypatch):
    """双源都没配的小类(cn_etf_fund) → 链只剩 sina, 照常取价。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("510300.SH", "沪深300ETF", "510300", "cn_etf_fund")]))
    sina = FakeSource("sina", [QuoteRow(code="510300.SH", market="A", price=4.0, source="sina")])
    md = _wire(monkeypatch, [sina])

    rows = asyncio.run(md._fetch_group_rows("A", ["510300.SH"]))
    assert sina.calls == [["510300.SH"]] and rows[0].source == "sina"


def test_invalidate_data_source_cache(temp_db):
    asyncio.run(_seed_classify())
    from services import market_data as md

    first = asyncio.run(md._classify_sources())
    assert first["hk_comm_stock"] == ("tickflow", "")
    md._cls_source_cache[1] = md.time.time() + 3600
    assert asyncio.run(md._classify_sources()) is first
    md.invalidate_data_source_cache()
    assert md._cls_source_cache[0] is None
    assert asyncio.run(md._classify_sources())["hk_comm_stock"] == ("tickflow", "")
