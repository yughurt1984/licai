"""Pydantic models for API request/response."""
from pydantic import BaseModel
from typing import Optional


class HoldingCreate(BaseModel):
    stock_code: str
    stock_name: str = ""
    shares: float
    cost_price: float
    trade_date: Optional[str] = None  # 首次买入日期 YYYY-MM-DD; 不填默认 today
    trade_time: Optional[str] = None  # 成交时刻 HH:MM (可选, 供分时图打点)
    broker: Optional[str] = None
    fx_rate: Optional[float] = None   # 港美股成交日汇率(每 1 原币折人民币), 按结单填; 不填按成交日自动取
    fee: Optional[float] = None       # 手续费(CNY); 不填由 estimate_trade_fee 按费率自动算
    asset_kind: Optional[str] = None  # stock(默认) | fut(期货) | opt(期权) — 期货/期权走开仓语义
    direction: Optional[str] = None   # 期货/期权: LONG(多) | SHORT(空)
    contract_multiplier: Optional[float] = None  # 每手乘数(沪铜 5 / 期权合约单位 10000)


class HoldingUpdate(BaseModel):
    stock_name: Optional[str] = None
    shares: Optional[float] = None
    cost_price: Optional[float] = None
    broker: Optional[str] = None
    last_price: Optional[float] = None   # 期权手填最新价(浮动盈亏用它)


class HoldingResponse(BaseModel):
    stock_code: str
    stock_name: str
    market: str = "A"
    currency: str = "CNY"
    shares: float
    cost_price: float
    current_price: Optional[float] = None
    fx_rate: float = 1.0
    fx_time: Optional[str] = None
    fx_source: Optional[str] = None
    price_change_pct: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    original_cost_value: Optional[float] = None
    original_market_value: Optional[float] = None
    cost_value: Optional[float] = None
    market_value: Optional[float] = None
    fx_pnl: Optional[float] = None      # 港美股: 浮动盈亏里本金的汇兑部分(原币成本×现汇率 − 按成交日汇率的人民币成本)
    sector: Optional[str] = None  # 顶级行业, e.g. "有色金属" / "汽车" — 用于前端分组
    broker: Optional[str] = None
    asset_kind: Optional[str] = None              # stock | fut | opt
    direction: Optional[str] = None               # LONG | SHORT (期货/期权)
    contract_multiplier: Optional[float] = None   # 每手乘数
    margin_value: Optional[float] = None          # 期货: 保证金占用 = 名义市值×品种保证金率(symbol_dict), 总资产的资产贡献口径; 期权/股票为 None
    div_per_share: Optional[float] = None  # 持有期累计每股现金分红(已摊进 cost_price, 对齐券商摊薄成本)
    today_valid: Optional[bool] = None     # 港美股: 今日涨跌是否属于「今天」(港股看港交所交易日, 美股看那场收盘距今); A 股为 None, 前端按 A 股交易日


class QuoteData(BaseModel):
    stock_code: str
    stock_name: str
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    amount: float
    change_pct: float
    amplitude: float
    turnover_rate: float


class AlertEvent(BaseModel):
    stock_code: str
    stock_name: str
    alert_type: str
    price: float
    message: str
    zone: Optional[list[float]] = None
