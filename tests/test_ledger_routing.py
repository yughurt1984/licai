"""分类路由(assets_classify.trade)测试: trade=t → 持仓账本, trade=f → 场外账本。

基金页的具体落地: 场内 ETF/LOF/REITs(trade=t) 入 holdings/position_actions(口径同股票);
场外开放式/货基/私募(trade=f) 入 external_assets/external_asset_actions(申购口径)。
目录数据双端校验: create_holding 拒 trade=f, /api/assets 拒 trade=t —— 规则来自数据,
不按大类写死(债券/逆回购等未来类别标好 trade 即自动生效)。
"""
import asyncio

import pytest
from fastapi import HTTPException

from api.portfolio_routes import HoldingCreate, create_holding
from api.assets_routes import AssetCreate, create_asset


@pytest.fixture
def catalog(temp_db, monkeypatch):
    """空库 + 分类目录最小集(场内/场外各一) + 免联网的股票名。"""
    import api.portfolio_routes as pr

    async def _fake_name(code):
        return "测试基金"

    monkeypatch.setattr(pr, "get_stock_name", _fake_name)

    async def _seed():
        from db import get_conn
        conn = await get_conn()
        try:
            await conn.execute(
                "INSERT INTO assets_classify (assets_class, assets_class_name, main_assets_type, "
                "assets_type_name, sub_assets_type, sub_assets_type_name, currency, trade) VALUES "
                "('fund', '基金', 'etf_fund', 'ETF', 'cn_etf_fund', '中国ETF', 'CNY', true),"
                "('fund', '基金', 'otc_fund', '场外基金', 'cn_otc_fund', '中国场外基金', 'CNY', false) "
                "ON CONFLICT (sub_assets_type) DO NOTHING")
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(_seed())
    return pr


def test_trade_f_rejected_from_holdings(catalog):
    """trade=f 的场外基金走持仓录入 → 拒绝(应记入场外账本)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(create_holding(HoldingCreate(
            stock_code="019144.OF", stock_name="测试场外基金", shares=1000, cost_price=1.0,
            sub_assets_type="cn_otc_fund")))
    assert "非交易资产" in ei.value.detail


def test_trade_t_fund_goes_to_holdings(catalog):
    """场内 ETF(trade=t) 经持仓录入 → holdings 行 + OPEN 流水(口径同股票)。"""
    res = asyncio.run(create_holding(HoldingCreate(
        stock_code="510300", stock_name="沪深300ETF", shares=1000, cost_price=3.9,
        sub_assets_type="cn_etf_fund", trade_date="2026-06-01")))
    assert res["message"] == "开仓成功"

    from database import get_holding, get_position_actions
    h = asyncio.run(get_holding("510300.SH"))
    assert h is not None
    assert h["asset_kind"] == "stock"            # 引擎列由目录推导: etf_fund → stock
    assert h["sub_assets_type"] == "cn_etf_fund"
    assert h["shares"] == 1000
    assert h["direction"] == "LONG"
    acts = asyncio.run(get_position_actions("510300.SH"))
    assert len(acts) == 1 and acts[0]["action_type"] == "OPEN"


def test_trade_t_rejected_from_external(catalog):
    """场内 ETF 走场外录入(external) → 拒绝(应记入持仓账本)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(create_asset(AssetCreate(
            asset_type="FUND", code="510300.SH", name="沪深300ETF",
            cost_amount=3900.0, shares=1000, sub_assets_type="cn_etf_fund")))
    assert "交易资产" in ei.value.detail


def test_trade_f_fund_goes_to_external(catalog):
    """场外开放式基金(trade=f) → external_assets 行(小类落库) + 申购流水。"""
    res = asyncio.run(create_asset(AssetCreate(
        asset_type="FUND", code="019144.OF", name="测试场外基金",
        cost_amount=10000.0, shares=10000.0, sub_assets_type="cn_otc_fund",
        start_date="2026-06-01")))
    assert res["message"] in ("added", "merged")

    from database import list_external_assets, list_external_actions
    rows = [a for a in asyncio.run(list_external_assets()) if a["code"] == "019144.OF"]
    assert len(rows) == 1
    assert rows[0]["sub_assets_type"] == "cn_otc_fund"
    assert rows[0]["asset_type"] == "FUND"
    acts = asyncio.run(list_external_actions(rows[0]["id"]))
    assert acts and acts[0]["action_type"] in ("BUY", "DEPOSIT")


def test_no_sub_skips_routing_check(temp_db):
    """不传小类的老调用方(CRYPTO/BOT/WEALTH/CASH 手填流程)不受路由校验影响。"""
    res = asyncio.run(create_asset(AssetCreate(
        asset_type="WEALTH", code="lc001", name="测试理财",
        cost_amount=50000.0, annual_yield_rate=0.025)))
    assert res["message"] in ("added", "merged")
