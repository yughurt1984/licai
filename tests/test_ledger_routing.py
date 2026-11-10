"""分类路由(assets_classify.valuation_type)测试: quote/nav → 持仓账本, yield/manual → 场外账本。

估值类型决定账本: 场内 ETF/LOF/REITs(quote=市价)与场外开放式/私募(nav=净值, 净值=价格)
都入 holdings/position_actions; 固定收益(yield)与手动估值(manual)入场外账本。
目录数据双端校验: create_holding 拒 yield/manual, /api/assets 拒 quote/nav —— 规则来自数据,
不按大类写死(储蓄国债/理财等未来类别标好 valuation_type 即自动生效)。
"""
import asyncio

import pytest
from fastapi import HTTPException

from api.portfolio_routes import HoldingCreate, create_holding
from api.assets_routes import AssetCreate, create_asset


@pytest.fixture
def catalog(temp_db, monkeypatch):
    """空库 + 分类目录最小集(quote/nav/yield 各一) + 免联网的股票名。"""
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
                "assets_type_name, sub_assets_type, sub_assets_type_name, currency, valuation_type) VALUES "
                "('fund', '基金', 'etf_fund', 'ETF', 'cn_etf_fund', '中国ETF', 'CNY', 'quote'),"
                "('fund', '基金', 'otc_fund', '场外基金', 'cn_otc_fund', '中国场外基金', 'CNY', 'nav'),"
                "('cash_equal', '现金等价物', 'cash_mgmt', '现金管理', 'cn_cash_mgmt', '中国现金管理', 'CNY', 'yield') "
                "ON CONFLICT (sub_assets_type) DO NOTHING")
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(_seed())
    return pr


def test_quote_rejected_from_external(catalog):
    """场内 ETF(quote) 走场外录入(external) → 拒绝(应记入持仓账本)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(create_asset(AssetCreate(
            asset_type="FUND", code="510300.SH", name="沪深300ETF",
            cost_amount=3900.0, shares=1000, sub_assets_type="cn_etf_fund")))
    assert "持仓账本" in ei.value.detail


def test_nav_rejected_from_external(catalog):
    """场外基金(nav) 走场外录入(external) → 拒绝(净值=价格, 归持仓账本)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(create_asset(AssetCreate(
            asset_type="FUND", code="019144.OF", name="测试场外基金",
            cost_amount=10000.0, shares=10000.0, sub_assets_type="cn_otc_fund")))
    assert "持仓账本" in ei.value.detail


def test_yield_rejected_from_holdings(catalog):
    """现金管理(yield) 走持仓录入 → 拒绝(应记入场外账本)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(create_holding(HoldingCreate(
            stock_code="000198.OF", stock_name="测试货基", shares=1000, cost_price=1.0,
            sub_assets_type="cn_cash_mgmt")))
    assert "场外账本" in ei.value.detail


def test_quote_fund_goes_to_holdings(catalog):
    """场内 ETF(quote) 经持仓录入 → holdings 行 + OPEN 流水(口径同股票)。"""
    res = asyncio.run(create_holding(HoldingCreate(
        stock_code="510300", stock_name="沪深300ETF", shares=1000, cost_price=3.9,
        sub_assets_type="cn_etf_fund", trade_date="2026-06-01")))
    assert res["message"] == "开仓成功"

    from database import get_holding, get_position_actions
    h = asyncio.run(get_holding("510300.SH"))
    assert h is not None
    assert h["main_assets_type"] == "etf_fund"            # 引擎列钉住目录中类: etf_fund
    assert h["sub_assets_type"] == "cn_etf_fund"
    assert h["shares"] == 1000
    assert h["direction"] == "LONG"
    acts = asyncio.run(get_position_actions("510300.SH"))
    assert len(acts) == 1 and acts[0]["action_type"] == "OPEN"


def test_nav_fund_goes_to_holdings(catalog):
    """场外开放式基金(nav, 净值=价格) 经持仓录入 → holdings 行 + OPEN 流水。"""
    res = asyncio.run(create_holding(HoldingCreate(
        stock_code="019144.OF", stock_name="测试场外基金", shares=10000, cost_price=1.0,
        sub_assets_type="cn_otc_fund", trade_date="2026-06-01")))
    assert res["message"] == "开仓成功"

    from database import get_holding, get_position_actions
    h = asyncio.run(get_holding("019144.OF"))
    assert h is not None
    assert h["main_assets_type"] == "otc_fund"
    assert h["sub_assets_type"] == "cn_otc_fund"
    assert h["shares"] == 10000
    acts = asyncio.run(get_position_actions("019144.OF"))
    assert len(acts) == 1 and acts[0]["action_type"] == "OPEN"


def test_yield_fund_goes_to_external(catalog):
    """现金管理(yield) → external_assets 行(小类落库) + 流水。"""
    res = asyncio.run(create_asset(AssetCreate(
        asset_type="FUND", code="000198.OF", name="测试货基",
        cost_amount=10000.0, shares=10000.0, sub_assets_type="cn_cash_mgmt",
        start_date="2026-06-01")))
    assert res["message"] in ("added", "merged")

    from database import list_external_assets, list_external_actions
    rows = [a for a in asyncio.run(list_external_assets()) if a["code"] == "000198.OF"]
    assert len(rows) == 1
    assert rows[0]["sub_assets_type"] == "cn_cash_mgmt"
    assert rows[0]["asset_type"] == "FUND"
    acts = asyncio.run(list_external_actions(rows[0]["id"]))
    assert acts and acts[0]["action_type"] in ("BUY", "DEPOSIT")


def test_no_sub_skips_routing_check(temp_db):
    """不传小类的老调用方(CRYPTO/BOT/CASH 手填流程)不受路由校验影响。"""
    res = asyncio.run(create_asset(AssetCreate(
        asset_type="CASH", code="cash001", name="测试现金",
        cost_amount=5000.0, annual_yield_rate=0.015)))
    assert res["message"] in ("added", "merged")


def test_wealth_rejected_from_external(temp_db):
    """理财(WEALTH)已退出场外账本: 固定收益走 /api/fix-income (fix_income_assets)。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(create_asset(AssetCreate(
            asset_type="WEALTH", code="lc001", name="测试理财",
            cost_amount=50000.0, annual_yield_rate=0.025)))
    assert "fix-income" in ei.value.detail
