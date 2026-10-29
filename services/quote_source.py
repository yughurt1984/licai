"""行情数据源的规范层(ccxt 式 canonical model + 适配器协议)。

三个源(ths_fuyao / futu / sina legacy)对"同一件事"的说法完全不一样 —— 字段名
(last_price vs 第4个逗号字段)、代码格式(600406.SH vs sh600406 vs HK.00700)、
返回形式(JSON/GBK文本/DataFrame)各不相同。本模块定义唯一对内的统一契约:

- QuoteRow: 规范行情行。各源原始返回由适配器翻译成这一种形状后, 才允许进入
  market_data 的派发/落库/输出链。code 恒为项目标准格式。
- QuoteSource: 源适配器协议(typing.Protocol, 结构化鸭子类型, 适配器无需继承)。
  snapshot() 的契约是**绝不抛异常** —— 任何失败(网络/未配置/某码无数据)都表现为
  返回列表里没有该码, 派发层据此把缺码交给链上的下一个源(按码兜底)。
- info: ccxt 式原始报文透传位。各源特有字段(新浪的 session/ext_hours、盘后价等)
  放这里, 规范模型不试图吞掉所有源的特殊字段。

实现方: services/ths_fuyao.py(fuyao, A/FUT/OPT)、services/futu_quote.py(futu, HK/US)、
services/market_data.py 内的 sina legacy 适配器(只兜底股票)。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

# 内部品种分组(market) → market.quote_latest 的 asset_type
ATYPE_BY_MARKET = {"A": "a_stock", "HK": "hk_stock", "US": "us_stock", "FUT": "fut", "OPT": "opt"}


class QuoteRow(BaseModel):
    """一条规范化行情。"""
    code: str                                   # 标准格式: 600406.SH / HK.00700 / CU2601.SHF
    market: str                                 # A / HK / US / FUT / OPT
    name: str = ""
    price: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    volume: float = 0
    amount: float = 0
    change_pct: float | None = None
    currency: str = "CNY"
    quote_time: str = ""
    source: str = ""                            # fuyao / futu / sina
    info: dict = Field(default_factory=dict)    # 源特有字段透传(不进规范语义)

    def to_store_row(self) -> dict:
        """QuoteRow → market.quote_latest 行(market_store.save_quotes 的入参形状)。"""
        return {
            "symbol": self.code,
            "asset_type": ATYPE_BY_MARKET.get(self.market, self.market.lower()),
            "name": self.name,
            "price": self.price,
            "open": self.open, "high": self.high, "low": self.low,
            "prev_close": self.prev_close,
            "volume": self.volume, "amount": self.amount,
            "change_pct": self.change_pct,
            "currency": self.currency,
            "quote_time": self.quote_time,
            "source": self.source or "remote",
        }


@runtime_checkable
class QuoteSource(Protocol):
    """源适配器协议。"""
    name: str
    asset_types: tuple[str, ...]        # 声明支持的品种分组(A/HK/US/FUT/OPT)

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        """批量取行情, 内部完成原始形状 → QuoteRow 翻译。

        契约: 不抛异常。失败/未配置/部分码无数据 → 该码不出现在返回列表里,
        派发层(market_data)据此把缺码交给源链的下一个源。
        """
        ...
