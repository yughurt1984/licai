"""富途 OpenAPI 行情快照(港/美股): https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html

- 走本地 OpenD 网关(默认 127.0.0.1:11111), 需先启动 FutuOpenD 并登录; 依赖 pip install futu-api。
- 代码格式与本项目持仓一致: HK.00700 / US.AAPL, 直接透传。
- get_market_snapshot 支持批量(单次上限 400), 涨跌幅自算(现价 vs 昨收)。
- 所有请求 best-effort: OpenD 没跑/SDK 未装返回 None, 不连累持仓页。
- 访问策略(与 ths/fuyao 一致): 只有手动刷新(force)和新增持仓冷启动(quote_latest 无行)才联网,
  其余一律读 PG(market.quote_latest)。每日维护后续开发。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from config import config
from services.quote_source import QuoteRow  # 规范层模型(仅依赖 pydantic, 无循环导入)

_ctx: Optional[object] = None          # OpenQuoteContext 单例
_ctx_host: tuple = ()                  # (host, port) 变了就重建
_last_error: str = ""


def _resolve_conn() -> tuple[str, int]:
    """网关地址回退: .env/config(设置页的 app_config 由 async 版 futu_config 优先)。"""
    host = (getattr(config, "futu_host", "") or "127.0.0.1").strip()
    try:
        port = int(getattr(config, "futu_port", 0) or 11111)
    except Exception:
        port = 11111
    return host, port


def _get_ctx():
    """惰性建 OpenQuoteContext(单例); 失败抛异常由调用方接住。"""
    global _ctx, _ctx_host, _last_error
    try:
        from futu import OpenQuoteContext
    except ImportError:
        _last_error = "futu-api 未安装: pip install futu-api"
        raise
    host, port = _resolve_conn()
    if _ctx is None or _ctx_host != (host, port):
        if _ctx is not None:
            try:
                _ctx.close()
            except Exception:
                pass
        _ctx = OpenQuoteContext(host=host, port=port)
        _ctx_host = (host, port)
    return _ctx


async def futu_config() -> tuple[str, int]:
    """网关配置(异步): 优先 app_config(futu_host/futu_port), 其次 config/.env。"""
    try:
        from database import get_config
        host = ((await get_config("futu_host")) or "").strip() or (getattr(config, "futu_host", "") or "127.0.0.1").strip()
        port_raw = ((await get_config("futu_port")) or "").strip()
        port = int(port_raw) if port_raw else int(getattr(config, "futu_port", 0) or 11111)
        return host, port
    except Exception:
        return _resolve_conn()


def invalidate_ctx():
    """设置页改了网关地址后重建连接。"""
    global _ctx, _ctx_host
    if _ctx is not None:
        try:
            _ctx.close()
        except Exception:
            pass
    _ctx, _ctx_host = None, ()


def _snapshot_sync(codes: list[str], host: str, port: int) -> Optional[list[dict]]:
    global _ctx, _ctx_host, _last_error
    try:
        from futu import OpenQuoteContext
    except ImportError:
        _last_error = "futu-api 未安装: pip install futu-api"
        return None
    try:
        if _ctx is None or _ctx_host != (host, port):
            if _ctx is not None:
                try:
                    _ctx.close()
                except Exception:
                    pass
            _ctx = OpenQuoteContext(host=host, port=port)
            _ctx_host = (host, port)
        ret, df = _ctx.get_market_snapshot(codes)
        if ret != 0:
            _last_error = str(df)
            return None
        if df is None or df.empty:
            _last_error = "empty snapshot"
            return None
        out = []
        for _, r in df.iterrows():
            try:
                last = float(r.get("last_price") or 0)
                prev = float(r.get("prev_close_price") or 0)
                if not last:
                    continue
                out.append({
                    "code": str(r.get("code") or "").upper(),
                    "name": str(r.get("name") or ""),
                    "price": last,
                    "prev_close": prev or None,
                    "change_pct": round((last - prev) / prev * 100, 3) if prev else None,
                    "open": float(r.get("open_price") or 0) or None,
                    "high": float(r.get("high_price") or 0) or None,
                    "low": float(r.get("low_price") or 0) or None,
                    "volume": float(r.get("volume") or 0),
                    "turnover": float(r.get("turnover") or 0),
                    "update_time": str(r.get("update_time") or ""),
                    "currency": "HKD" if str(r.get("code", "")).startswith("HK.") else "USD",
                })
            except Exception:
                continue
        _last_error = ""
        return out or None
    except Exception as e:
        _last_error = str(e)[:160]
        return None


def last_error() -> str:
    return _last_error


async def snapshot(codes: list[str]) -> Optional[list[dict]]:
    """批量快照(港/美股, 单次≤400 只) → [{code,name,price,prev_close,change_pct,...}] 或 None。"""
    codes = [str(c or "").strip().upper() for c in codes if str(c or "").strip()]
    if not codes:
        return None
    host, port = await futu_config()
    return await asyncio.to_thread(_snapshot_sync, codes, host, port)


# ── 源适配器(规范层, 见 services/quote_source.py) ──────────────────────
async def snapshot_rows(codes: list[str]) -> list[QuoteRow]:
    """futu 源适配器: 标准码(HK.00700/US.AAPL) → 规范 QuoteRow。

    契约: 不抛异常 —— OpenD 没跑/SDK 未装/部分码无数据, 都表现为该码不在返回里,
    派发层据此把缺码交给链上的下一个源(新浪 legacy)。
    """
    items = await snapshot(codes)       # 内部已吞异常, 失败返回 None
    if not items:
        return []
    out: list[QuoteRow] = []
    for it in items:
        try:
            code = str(it.get("code") or "").upper()
            if not code or not it.get("price"):
                continue
            out.append(QuoteRow(
                code=code, market="HK" if code.startswith("HK.") else "US",
                name=it.get("name") or "", price=float(it["price"]),
                open=it.get("open"), high=it.get("high"), low=it.get("low"),
                prev_close=it.get("prev_close"),
                volume=it.get("volume") or 0, amount=it.get("turnover") or 0,
                change_pct=it.get("change_pct"),
                currency=it.get("currency") or ("HKD" if code.startswith("HK.") else "USD"),
                quote_time=it.get("update_time") or "", source="futu"))
        except Exception:
            continue
    return out


class _FutuSource:
    name = "futu"
    asset_types = ("HK", "US")

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        return await snapshot_rows(codes)


SOURCE = _FutuSource()
