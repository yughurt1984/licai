"""股票做空(参考期货模式)API 层端到端: 建空头仓 → 加空 → 部分平仓 → 多空两行共存。

直接调 api.portfolio_routes 的路由函数(不起 TestClient, 免打行情网络);
费用全部手填 0, 让盈亏断言精确。
"""
import asyncio

import pytest

from models import HoldingCreate
from api.portfolio_routes import ActionCreate


CODE = "600000.SH"   # API 内部统一走 normalize_stock_code 后的规范码


@pytest.fixture
def fake_env(monkeypatch, temp_db):
    """空库 + 假股票名(不联网)。broker 表为空 → 自动估费回退默认常量,
    但所有流水都手填 fee=0, 断言不受费率影响。"""
    import api.portfolio_routes as pr

    async def _fake_name(code):
        return "测试股"

    monkeypatch.setattr(pr, "get_stock_name", _fake_name)
    return pr


def test_short_stock_lifecycle(fake_env):
    pr = fake_env
    # 1) 建空头仓: 卖出开仓 100@10
    res = asyncio.run(pr.create_holding(HoldingCreate(
        stock_code="600000", shares=100, cost_price=10.0, direction="SHORT",
        fee=0.0, trade_date="2026-01-05")))
    assert res["message"] == "开仓成功"

    h = asyncio.run(pr.get_holding(CODE, direction="SHORT"))
    assert h["direction"] == "SHORT"
    assert h["shares"] == 100
    assert abs(h["cost_price"] - 10.0) < 1e-6
    assert h["main_assets_type"] == "common_share"  # 股票空头 ≠ 衍生品

    # 首笔 OPEN 流水带方向
    acts = asyncio.run(pr.list_actions(CODE, direction="SHORT"))
    assert len(acts) == 1 and acts[0]["action_type"] == "OPEN" and acts[0]["direction"] == "SHORT"

    # 2) 加空 100@9.5
    asyncio.run(pr.create_action(CODE, ActionCreate(
        action_type="OPEN", price=9.5, shares=100, trade_date="2026-01-08",
        fee=0.0, direction="SHORT")))
    h = asyncio.run(pr.get_holding(CODE, direction="SHORT"))
    assert h["shares"] == 200

    # 3) 平 100@8 → FIFO 吃掉 100@10, 已实现 (10−8)×100 = +200, 剩 100@9.5
    asyncio.run(pr.create_action(CODE, ActionCreate(
        action_type="CLOSE", price=8.0, shares=100, trade_date="2026-01-10",
        fee=0.0, direction="SHORT")))
    h = asyncio.run(pr.get_holding(CODE, direction="SHORT"))
    assert h["shares"] == 100
    assert abs(h["cost_price"] - 9.5) < 1e-6    # 剩余开仓均价(正数)

    acts = asyncio.run(pr.list_actions(CODE, direction="SHORT"))
    # list_actions 按 created_at 倒序(新流水在前, 前端再排); 测试按成交日排回时间序
    acts.sort(key=lambda a: (a["trade_date"] or "", a["id"] or 0))
    assert [a["action_type"] for a in acts] == ["OPEN", "OPEN", "CLOSE"]
    assert all(a["direction"] == "SHORT" for a in acts)

    # 4) 已实现盈亏路由: 该代码已实现 = +200 (多空分账, 不混)
    rep = asyncio.run(pr.realized_pnl())
    item = next(i for i in rep["items"] if i["stock_code"] == CODE)
    assert abs(item["realized_pnl"] - 200.0) < 1e-6
    assert item["still_holding"] is True

    # 5) 空头行上记 买卖(BUY/SELL) → 拒绝(开平仓语义)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(pr.create_action(CODE, ActionCreate(
            action_type="BUY", price=8.0, shares=100, trade_date="2026-01-11",
            fee=0.0, direction="SHORT")))
    assert "开仓/平仓" in ei.value.detail

    # 6) 同方向重复建仓 → 让走加仓
    with pytest.raises(HTTPException):
        asyncio.run(pr.create_holding(HoldingCreate(
            stock_code="600000", shares=100, cost_price=10.0, direction="SHORT", fee=0.0)))

    # 7) 同代码开多头行: 与空头行互不干扰(多空两本账)
    #    (并行重构后的约定: 所有股票行都落方向, 多头=LONG/1, 建仓种子统一 OPEN)
    res = asyncio.run(pr.create_holding(HoldingCreate(
        stock_code="600000", shares=100, cost_price=7.0, fee=0.0,
        trade_date="2026-02-01")))
    assert res["message"] == "开仓成功"
    long_row = asyncio.run(pr.get_holding(CODE, direction="LONG", strict=True))
    short_row = asyncio.run(pr.get_holding(CODE, direction="SHORT"))
    assert long_row["direction"] == "LONG" and long_row["shares"] == 100
    assert abs(long_row["cost_price"] - 7.0) < 1e-6
    assert short_row["shares"] == 100           # 空头账没被多头流水污染
    assert abs(short_row["cost_price"] - 9.5) < 1e-6

    # 多头行的流水隔离: list_actions 带方向 = 只看本方向流水
    long_acts = asyncio.run(pr.list_actions(CODE, direction="LONG"))
    assert [a["action_type"] for a in long_acts] == ["OPEN"]

    # 8) 已实现: 多空两本账分开后合并 (+200 空 + 0 多)
    rep = asyncio.run(pr.realized_pnl())
    item = next(i for i in rep["items"] if i["stock_code"] == CODE)
    assert abs(item["realized_pnl"] - 200.0) < 1e-6


def test_short_requires_positive_shares(fake_env):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(fake_env.create_holding(HoldingCreate(
            stock_code="000001", shares=0, cost_price=10.0, direction="SHORT", fee=0.0)))
