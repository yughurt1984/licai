"""数据源路由: assets_classify.data_source(人工维护)按小类指派行情源。

钉住四件事: data_source 读取(含空值过滤/小写归一)、code→小类的双形态探码(底稿 00700.HK ↔
内部 HK.00700)、指派源优先 + 未命中落标准链、兜底链不重试该码已试过的源。
"""
import asyncio

import pytest

from services.quote_source import QuoteRow

# temp_db 由 tests/conftest.py 提供(指向每次重建的 PG 测试库)

_SUBS = [
    ("stock", "股票", "common_share", "普通股", "hk_comm_stock", "港股", "tickflow"),
    ("stock", "股票", "common_share", "普通股", "us_comm_stock", "美股", "FUTU"),   # 大写→归一小写
    ("stock", "股票", "common_share", "普通股", "a_comm_stock", "A股", "ths"),
    ("stock", "股票", "common_share", "普通股", "hk_etf_fund", "香港ETF", "futu"),
    ("stock", "股票", "common_share", "普通股", "hk_mm_fund", "香港货币基金", "yhf"),   # 未注册源
    ("fund", "基金", "etf_fund", "场内基金", "cn_etf_fund", "中国ETF", ""),            # 空 → 不指派
]


async def _seed_classify():
    from tests.conftest import insert_rows
    await insert_rows("assets_classify", [
        {"assets_class": c, "assets_class_name": cn, "main_assets_type": m,
         "assets_type_name": mn, "sub_assets_type": s, "sub_assets_type_name": sn,
         "data_source": ds}
        for c, cn, m, mn, s, sn, ds in _SUBS])


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


def _wire(monkeypatch, registry_sources, chains):
    """把假源接进 market_data 的注册表与品种链(monkeypatch 自动还原, 不泄漏给别的测试)。
    registry_sources: 注册表里的源(按 name 去重后一个); chains: {品种分组: (源,...)}。"""
    from services import market_data as md
    monkeypatch.setattr(md, "_source_registry", lambda: {s.name: s for s in registry_sources})
    monkeypatch.setattr(md, "_quote_chains", lambda: chains)
    return md


@pytest.fixture(autouse=True)
def _fresh_route_cache():
    """每个测试前清路由缓存(模块级 TTL 缓存会跨测试泄漏)。"""
    from services.market_data import invalidate_data_source_cache
    invalidate_data_source_cache()
    yield


def test_classify_sources_reads_and_normalizes(temp_db):
    asyncio.run(_seed_classify())
    from services.market_data import _classify_sources

    src_map = asyncio.run(_classify_sources())
    assert src_map["hk_comm_stock"] == "tickflow"
    assert src_map["us_comm_stock"] == "futu"          # 大写归一
    assert src_map["a_comm_stock"] == "ths"
    assert "cn_etf_fund" not in src_map                # 空串不指派


def test_classify_sources_serves_cache_without_db(temp_db):
    """TTL 内直接吃缓存, 库挂了也不影响(回退上一次结果)。"""
    from services import market_data as md

    cached = {"hk_comm_stock": "tickflow"}
    md._cls_source_cache = [cached, md.time.time() + 3600]     # 未过期
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
    assert "999999.SH" not in out                      # 查不到 → 不指派, 落标准链

    # 已存行情行的小类优先(冷启动新码底稿还没有时用它兜底)
    out2 = asyncio.run(_code_sub_map(["00700.HK"], stored={"00700.HK": {"sub_assets_type": "hk_etf_fund"}}))
    assert out2["00700.HK"] == "hk_etf_fund"


def test_fetch_group_rows_pinned_source_first(temp_db, monkeypatch):
    """hk_comm_stock→tickflow: 指派源先拿, 拿到后兜底链一个都不调。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("00700.HK", "腾讯控股", "00700", "hk_comm_stock")]))
    md = _wire(monkeypatch, [
        FakeSource("tickflow", [QuoteRow(code="00700.HK", market="HK", price=500.0, source="tickflow")]),
        FakeSource("futu"), FakeSource("sina"),
    ], {"HK": (FakeSource("futu"), FakeSource("sina"))})
    tf = md._source_registry()["tickflow"]
    futu, sina = md._quote_chains()["HK"]

    rows = asyncio.run(md._fetch_group_rows("HK", ["00700.HK"]))
    assert tf.calls == [["00700.HK"]]                  # 指派源被调
    assert futu.calls == [] and sina.calls == []       # 兜底链没被调
    assert [r.code for r in rows] == ["00700.HK"] and rows[0].source == "tickflow"


def test_fetch_group_rows_pinned_miss_falls_to_chain_without_retry(temp_db, monkeypatch):
    """指派源没拿到 → 落标准链; 链上跳过已试过的指派源(不重试), 其他源按序喂缺码。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([("00700.HK", "腾讯控股", "00700", "hk_comm_stock")]))
    tf = FakeSource("tickflow")                        # 指派源(注册表里唯一): 空返回
    chain_tf = FakeSource("tickflow")                  # 链上同名源 —— 应被跳过(同一源不打两遍)
    sina = FakeSource("sina", [QuoteRow(code="00700.HK", market="HK", price=480.0, source="sina")])
    _wire(monkeypatch, [tf, sina], {"HK": (chain_tf, sina)})

    from services import market_data as md
    rows = asyncio.run(md._fetch_group_rows("HK", ["00700.HK"]))
    assert tf.calls == [["00700.HK"]]
    assert chain_tf.calls == []                        # 指派源失败后, 链上同名源不重试
    assert sina.calls == [["00700.HK"]]
    assert rows[0].source == "sina"


def test_fetch_group_rows_unknown_source_degrades_to_chain(temp_db, monkeypatch):
    """data_source 指了未注册的源(yhf) → 告警一次, 码照常走标准链, 不影响其他码。"""
    asyncio.run(_seed_classify())
    asyncio.run(_seed_dict([
        ("02828.HK", "盈富基金-货币", "02828", "hk_mm_fund"),
        ("09988.HK", "阿里", "09988", "hk_comm_stock"),
    ]))
    tf = FakeSource("tickflow", [QuoteRow(code="09988.HK", market="HK", price=90.0, source="tickflow")])
    sina = FakeSource("sina", [QuoteRow(code="02828.HK", market="HK", price=25.0, source="sina")])
    md = _wire(monkeypatch, [tf, sina], {"HK": (sina,)})
    md._unknown_src_warned.clear()

    rows = asyncio.run(md._fetch_group_rows("HK", ["02828.HK", "09988.HK"]))
    assert "yhf" in md._unknown_src_warned             # 未知源告警过一次
    assert [r.code for r in rows] == ["09988.HK", "02828.HK"]   # 各走各的路, 互不拖累
    assert tf.calls and sina.calls


def test_fetch_group_rows_stored_supplies_sub_type(temp_db, monkeypatch):
    """quote_latest 已存行的小类也能驱动指派(底稿还没有的冷启动码)。"""
    asyncio.run(_seed_classify())
    tf = FakeSource("tickflow", [QuoteRow(code="03690.HK", market="HK", price=10.0, source="tickflow")])
    sina = FakeSource("sina")
    md = _wire(monkeypatch, [tf, sina], {"HK": (sina,)})

    rows = asyncio.run(md._fetch_group_rows(
        "HK", ["03690.HK"], stored={"03690.HK": {"sub_assets_type": "hk_comm_stock"}}))
    assert tf.calls == [["03690.HK"]] and sina.calls == []
    assert rows[0].source == "tickflow"


def test_invalidate_data_source_cache(temp_db):
    asyncio.run(_seed_classify())
    from services import market_data as md

    first = asyncio.run(md._classify_sources())
    assert first["hk_comm_stock"] == "tickflow"
    # TTL 内缓存生效
    md._cls_source_cache[1] = md.time.time() + 3600
    assert asyncio.run(md._classify_sources()) is first
    # 主动失效后重读
    md.invalidate_data_source_cache()
    assert md._cls_source_cache[0] is None
    assert asyncio.run(md._classify_sources())["hk_comm_stock"] == "tickflow"
