"""Feishu (飞书) webhook notification service.

多用户: webhook/静音是**个人配置**(app_config 的 PER_USER_KEYS), 按 current_user_id
解析。{uid: 配置} 内存缓存由 refresh_config() 维护 —— 后台循环在 run_as_user(uid) 里
逐用户刷新, 设置页保存后由路由刷新。没刷新过 = 未配置 = 不推送(安全默认)。
"""
from __future__ import annotations
import asyncio
import json
import requests as _requests

from services.context import current_user_id

# {uid: {"url": str, "muted": bool}}
_configs: dict[int, dict] = {}


def _cfg() -> dict:
    return _configs.get(current_user_id()) or {"url": "", "muted": True}


async def refresh_config():
    """从 app_config 读当前用户的 webhook/静音 进缓存。循环逐用户开头、设置页读/写后调用。"""
    from database import get_config
    uid = current_user_id()
    url = (await get_config("feishu_webhook_url") or "").strip()
    muted = (await get_config("feishu_muted")) == "1"
    _configs[uid] = {"url": url, "muted": muted}


def configure(webhook_url: str):
    """写缓存(不落库 —— 落库由 settings 路由的 set_config 负责)。"""
    _configs.setdefault(current_user_id(), {"url": "", "muted": False})["url"] = (webhook_url or "").strip()


def set_muted(muted: bool):
    _configs.setdefault(current_user_id(), {"url": "", "muted": False})["muted"] = bool(muted)


def is_muted() -> bool:
    return _cfg()["muted"]


def is_enabled() -> bool:
    """既配过 webhook 又没被静音才推送."""
    cfg = _cfg()
    return bool(cfg["url"]) and not cfg["muted"]


def _send_sync(payload: dict) -> bool:
    if not is_enabled():
        return False
    try:
        resp = _requests.post(
            _cfg()["url"],
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        data = resp.json()
        if data.get("code") != 0 and data.get("StatusCode") != 0:
            print(f"[feishu] Send failed: {data}")
            return False
        return True
    except Exception as e:
        print(f"[feishu] Error: {e}")
        return False


async def send_text(text: str) -> bool:
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    return await asyncio.to_thread(_send_sync, payload)


async def send_alert_card(
    stock_code: str,
    stock_name: str,
    alert_type: str,
    price: float,
    message: str,
    buy_zone: str = "",
    sell_zone: str = "",
) -> bool:
    """Send a rich card message for T-trade alerts."""
    is_buy = "BUY" in alert_type
    color = "green" if is_buy else "red"
    title = f"{'买入' if is_buy else '卖出'}提醒 {stock_name}({stock_code})"

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**当前价:** {price:.2f}\n{message}",
            },
        },
    ]

    if buy_zone:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**买入区间:** {buy_zone}",
            },
        })

    if sell_zone:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**卖出区间:** {sell_zone}",
            },
        })

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }
    return await asyncio.to_thread(_send_sync, payload)


async def send_test() -> bool:
    """Send a test message to verify webhook is working."""
    return await send_text("理财助手已连接，飞书推送测试成功！")
