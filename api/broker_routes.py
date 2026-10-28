# api/broker_routes.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    list_brokers, add_broker, update_broker, delete_broker, get_default_broker,
)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])

BROKER_TYPES = ["A股", "港股", "美股", "期货", "Crypto"]
BROKER_UNITS = ["笔", "股", "张", "%"]


class BrokerCreate(BaseModel):
    name: str
    type: str = "A股"
    stock_rate: float
    stock_unit: str = "笔"
    stock_min: float = 0.0
    etf_rate: Optional[float] = None    # 基金费率, 空 = 同股票费率
    etf_unit: str = "笔"
    etf_min: Optional[float] = None
    opt_rate: float = 0.0               # 期权费(金额, 按 opt_unit 计)
    opt_unit: str = "张"
    opt_min: float = 0.0                # 最低期权费
    platform_fee: float = 0.0
    platform_min: float = 0.0           # 最低平台费


class BrokerUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    stock_rate: Optional[float] = None
    stock_unit: Optional[str] = None
    stock_min: Optional[float] = None
    etf_rate: Optional[float] = None
    etf_unit: Optional[str] = None
    etf_min: Optional[float] = None
    opt_rate: Optional[float] = None
    opt_unit: Optional[str] = None
    opt_min: Optional[float] = None
    platform_fee: Optional[float] = None
    platform_min: Optional[float] = None
    is_default: Optional[bool] = None


@router.get("")
async def get_brokers():
    return await list_brokers()


@router.post("")
async def create_broker(data: BrokerCreate):
    if data.type not in BROKER_TYPES:
        raise HTTPException(400, f"券商类型必须是 {'/'.join(BROKER_TYPES)}")
    for u in (data.stock_unit, data.etf_unit, data.opt_unit):
        if u not in BROKER_UNITS:
            raise HTTPException(400, f"计价单位必须是 {'/'.join(BROKER_UNITS)}")
    bid = await add_broker(data.name, data.stock_rate, data.stock_min, data.etf_rate, data.etf_min,
                           opt_rate=data.opt_rate, opt_min=data.opt_min,
                           platform_fee=data.platform_fee, platform_min=data.platform_min,
                           stock_unit=data.stock_unit, etf_unit=data.etf_unit, opt_unit=data.opt_unit,
                           btype=data.type)
    return {"id": bid, "message": "created"}


@router.put("/{broker_id}")
async def modify_broker(broker_id: int, data: BrokerUpdate):
    payload = data.model_dump(exclude_unset=True)
    if "type" in payload and payload["type"] not in BROKER_TYPES:
        raise HTTPException(400, f"券商类型必须是 {'/'.join(BROKER_TYPES)}")
    for k in ("stock_unit", "etf_unit", "opt_unit"):
        if k in payload and payload[k] not in BROKER_UNITS:
            raise HTTPException(400, f"计价单位必须是 {'/'.join(BROKER_UNITS)}")
    if "is_default" in payload:
        if payload["is_default"]:
            payload["is_default"] = 1
        else:
            payload.pop("is_default")   # 不允许直接取消默认(会变成0个默认); 改默认请对另一家设 true
    if not payload:
        raise HTTPException(400, "无可改字段")
    await update_broker(broker_id, **payload)
    return {"message": "updated"}


@router.delete("/{broker_id}")
async def remove_broker(broker_id: int):
    rows = await list_brokers()
    target = next((b for b in rows if b["id"] == broker_id), None)
    if not target:
        raise HTTPException(404, "券商不存在")
    if target["is_default"]:
        raise HTTPException(400, "默认券商不可删, 请先把别的设为默认")
    await delete_broker(broker_id)
    return {"message": "deleted"}
