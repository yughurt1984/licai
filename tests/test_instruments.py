"""标的信息源: symbol_dict 未命中的 ticker 按小类路由 instruments 源(tickflow/ths varieties)。

钉住: 双源读取、tickflow 解析(缺名=未命中)、ths 品种/合约两级、负缓存(错码不反复打 API)、
symbol-name 端点(命中回显+落库 / 未命中不落库)。
"""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _fresh_cache():
    from services.instruments import invalidate_instruments_cache
    invalidate_instruments_cache()
    yield


async def _seed_classify(main="tickflow", back=""):
    from tests.conftest import insert_rows
    await insert_rows("assets_classify", [
        {"assets_class": "stock", "assets_class_name": "股票", "main_assets_type": "common_share",
         "assets_type_name": "普通股", "sub_assets_type": "hk_comm_stock",
         "sub_assets_type_name": "港股", "main_instruments_source": main,
         "back_instruments_source": back}])


def test_tf_fetch_parses_and_requires_name(monkeypatch):
    from services import instruments as I, tickflow as T

    async def fake_key():
        return "test-key"
    async def fake_host():
        return "https://api.tickflow.org"
    monkeypatch.setattr(T, "_api_key", fake_key)
    monkeypatch.setattr(T, "_host", fake_host)

    async def fake_instruments(symbols):
        return [{"code": "00700.HK", "exchange": "HK", "ticker": "00700",
                 "region": "HK", "type": "stock", "name": "腾讯控股", "lot_size": 100}]
    monkeypatch.setattr(T, "instruments", fake_instruments)
    row = asyncio.run(I._tf_fetch("hk_comm_stock", "00700.HK"))
    assert row["name"] == "腾讯控股" and row["ticker"] == "00700" and row["suffix"] == "HK"

    async def fake_no_name(symbols):
        return [{"code": "00700.HK", "exchange": "HK", "ticker": "00700", "name": ""}]
    monkeypatch.setattr(T, "instruments", fake_no_name)
    assert asyncio.run(I._tf_fetch("hk_comm_stock", "00700.HK")) is None   # 缺名 = 未命中


def test_ths_fetch_variety_and_contract(monkeypatch):
    from services import instruments as I, ths_fuyao as T

    _VARIETIES = [{"variety_code": "CU", "name": "沪铜", "exchange_code": "SHFE",
                   "trade_amount": 5, "contract_multiplier": 5}]
    async def fake_varieties(kind):
        return _VARIETIES
    async def fake_search(q, asset_type=""):
        return [{"thscode": "CU2601.SHF", "name": "沪铜2601"}]
    monkeypatch.setattr(T, "varieties_list", fake_varieties)
    monkeypatch.setattr(T, "contracts_search", fake_search)

    v = asyncio.run(I._ths_fetch("cn_futures", "CU"))                 # 品种码 → 品种行
    assert v["code"] == "CU" and v["name"] == "沪铜" and v["trade_amount"] == 5

    c = asyncio.run(I._ths_fetch("cn_futures", "CU2601.SHF"))         # 合约码 → 检索名
    assert c["code"] == "CU2601.SHF" and c["name"] == "沪铜2601" and c["ticker"] == "CU2601"


def test_fetch_routes_main_then_back(monkeypatch, temp_db):
    asyncio.run(_seed_classify(main="tickflow", back="ths"))
    from services import instruments as I

    calls = []
    async def fake_tf(sub, code):
        calls.append("tf")
        return None                                                   # main 未命中
    async def fake_ths(sub, code):
        calls.append("ths")
        return {"code": code, "name": "兜底来的", "ticker": code, "suffix": "",
                "sub_assets_type": sub}
    monkeypatch.setattr(I, "_tf_fetch", fake_tf)
    monkeypatch.setattr(I, "_ths_fetch", fake_ths)

    row = asyncio.run(I.fetch_instrument("hk_comm_stock", "00700.HK"))
    assert calls == ["tf", "ths"] and row["name"] == "兜底来的"


def test_futu_fetch_basicinfo_mapping(monkeypatch):
    """futu 源(stock-basicinfo): 按用户 5 条映射规则落底稿字段。"""
    from services import instruments as I

    _BASIC = [{"code": "HK.06186", "name": "CHINA FEIHE", "sc_name": "中国飞鹤",
               "ticker": "06186", "exchange_raw": "SEHK", "lot_size": 1000,
               "stock_type": "STOCK", "suspension": False}]

    async def fake_basicinfo(codes):
        assert codes == ["HK.06186"]
        return _BASIC
    monkeypatch.setattr(I, "_futu_basicinfo", fake_basicinfo)

    row = asyncio.run(I._futu_fetch("hk_comm_stock", "06186.HK"))
    assert row == {"code": "06186.HK", "name": "中国飞鹤", "ticker": "06186",
                   "suffix": "HK", "sub_assets_type": "hk_comm_stock", "source": "futu"}

    # basicinfo 无此码 → 未命中
    async def fake_empty(codes):
        return []
    monkeypatch.setattr(I, "_futu_basicinfo", fake_empty)
    assert asyncio.run(I._futu_fetch("hk_comm_stock", "06186.HK")) is None
    # 非 HK/US 码不支持
    assert asyncio.run(I._futu_fetch("cn_futures", "CU2601.SHF")) is None


def test_negative_cache_blocks_repeat_calls(monkeypatch, temp_db):
    asyncio.run(_seed_classify())
    from services import instruments as I

    n = {"count": 0}
    async def fake_tf(sub, code):
        n["count"] += 1
        return None
    monkeypatch.setattr(I, "_tf_fetch", fake_tf)
    monkeypatch.setattr(I, "_ths_fetch", lambda *a: None)

    assert asyncio.run(I.fetch_instrument("hk_comm_stock", "99999.HK")) is None
    assert asyncio.run(I.fetch_instrument("hk_comm_stock", "99999.HK")) is None
    assert n["count"] == 1                                            # 负缓存: 第二次不打源
    I.invalidate_instruments_cache()
    assert asyncio.run(I.fetch_instrument("hk_comm_stock", "99999.HK")) is None
    assert n["count"] == 2                                            # 失效位清掉负缓存


async def _seed_dict(rows):
    from tests.conftest import insert_rows
    await insert_rows("symbol_dict", [
        {"code": code, "name": name, "ticker": ticker, "sub_assets_type": sub}
        for code, name, ticker, sub in rows])


def test_symbol_name_endpoint_miss_then_hit_writes_dict(temp_db, monkeypatch):
    """端点两级: 底稿 miss → instruments 命中 → 回显 + 落库; 查无 → null 且不落库。"""
    from api.market_routes import symbol_name
    from services import instruments as I

    async def fake_tf(sub, code):
        if code in ("00700.HK", "99999.HK"):      # 99999: 模拟"查无此标的"
            return None
        return {"code": code, "name": "新上市标的", "ticker": code.split(".")[0],
                "suffix": "HK", "sub_assets_type": sub, "source": "tickflow"}
    monkeypatch.setattr(I, "_tf_fetch", fake_tf)
    monkeypatch.setattr(I, "_ths_fetch", lambda *a: None)

    asyncio.run(_seed_classify())                      # instruments 源配置(表单所选小类)
    asyncio.run(_seed_dict([("00700.HK", "腾讯控股", "00700", "hk_comm_stock")]))   # 底稿命中路径
    miss = asyncio.run(symbol_name(code="0700.HK", sub="hk_comm_stock"))
    assert miss == {"name": "腾讯控股", "code": "00700.HK"}          # 底稿命中, 不走 instruments

    hit = asyncio.run(symbol_name(code="06187.HK", sub="hk_comm_stock"))   # 真查无此标的
    assert hit["name"] == "新上市标的" and hit["fetched"] is True
    assert [x["source"] for x in hit["sources"]] == ["tickflow"]           # main 命中即停

    # 后台落库是 create_task: 在同一循环里让出一下再查
    async def _wait_and_read():
        await asyncio.sleep(0.05)
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT name, sub_assets_type, data_source FROM symbol_dict WHERE code = '06187.HK'")
            return await cur.fetchone()
        finally:
            await db.close()
    row = asyncio.run(_wait_and_read())
    assert row is not None and row[0] == "新上市标的" and row[1] == "hk_comm_stock"
    assert row[2] == "tickflow"                                      # data_source 按源落名
