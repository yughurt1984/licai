"""WebSocket endpoint for real-time price push and alerts."""
from __future__ import annotations
import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.market_data import (get_realtime_quotes, is_trading_day_active, markets_open,
                                  split_stock_code, quote_auto_fetch)
from services import feishu_notify
from services.context import run_as_user, current_user_id
from config import config

router = APIRouter()

# 连接注册表按用户分桶: 推送只发本人连接, 不再全量广播(多用户隔离)。
_clients: dict[int, set[WebSocket]] = {}
_WS_AUTH_FAIL = 4401      # 自定义 close code: 会话无效(前端据此停重连)


async def broadcast(message: dict, user_id: int | None = None):
    """user_id=None 全用户广播(plan_alert 等系统事件); 指定则只发该用户的连接。"""
    targets = []
    if user_id is None:
        for conns in _clients.values():
            targets.extend(conns)
    else:
        targets = list(_clients.get(user_id, ()))
    dead = set()
    data = json.dumps(message, ensure_ascii=False, default=str)
    for ws in targets:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    for d in dead:
        for conns in _clients.values():
            conns.discard(d)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # WebSocket 不受 CORS 约束, 也不走 http 中间件。三道检查: 本机 Host/Origin + 会话 cookie
    # (浏览器同源握手自动带上)。会话无效关连接(4401), 前端据此停止重连。
    from services.origin_guard import ws_allowed
    from services import auth
    from services.context import set_current_user
    if not ws_allowed(ws.headers.get("host", ""), ws.headers.get("origin")):
        await ws.close(code=1008)
        return
    token = _cookie_of(ws, auth.COOKIE_NAME)
    session = await auth.resolve_session(token or "")
    if not session:
        await ws.close(code=_WS_AUTH_FAIL)
        return
    uid = session["user_id"]
    set_current_user(uid)
    await ws.accept()
    _clients.setdefault(uid, set()).add(ws)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                try:
                    await ws.send_text(json.dumps({"type": "heartbeat"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        _clients.get(uid, set()).discard(ws)
        if uid in _clients and not _clients[uid]:
            _clients.pop(uid, None)


def _cookie_of(ws: WebSocket, name: str) -> str:
    raw = ws.headers.get("cookie") or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _push_interval(opened: dict, holdings: list[dict]) -> int:
    """持仓所在的任一市场正在交易 → 快刷新; 否则慢刷新。A 股不论持仓与否都按 A 股时段快刷(沿用原口径)。"""
    held = {split_stock_code(h.get("stock_code") or "")[0] for h in holdings or []}
    if opened.get("A") or any(opened.get(m) for m in held & {"HK", "US"}):
        return config.refresh_interval
    return config.idle_interval


async def price_monitor_loop():
    """Background task: push prices every cycle, recompute suggestions every 5min.

    多用户: 逐用户取各自持仓的行情、只推给本人的连接(以前是全表持仓广播给所有连接)。
    """
    from database import list_user_ids
    while True:
        try:
            if not _clients:
                await asyncio.sleep(5)
                continue

            opened = markets_open()
            any_pushed = False
            for uid in await list_user_ids():
                conns = _clients.get(uid)
                if not conns:
                    continue
                with run_as_user(uid):
                    from database import get_all_holdings
                    holdings = await get_all_holdings()
                    if not holdings:
                        continue
                    codes = [h["stock_code"] for h in holdings]
                    # 自动联网模式: 监听循环自己是刷新器, force=True 直连远端保证 5s 推流是实盘价;
                    # 纯 PG 模式(quote_auto_fetch=False): 只推库里的最新快照, 不联网。
                    quotes = await get_realtime_quotes(codes, force=quote_auto_fetch())
                    if not quotes:
                        continue
                    interval = _push_interval(opened, holdings)
                    await broadcast({
                        "type": "price_update",
                        "data": quotes,
                        "market_open": is_trading_day_active(),    # A 股口径(含午休), 前端沿用
                        "markets_open": opened,                    # {A, HK, US} 各自是否在连续交易时段
                    }, user_id=uid)
                    any_pushed = True
            await asyncio.sleep(config.refresh_interval if any_pushed else config.idle_interval)
        except Exception as e:
            print(f"[monitor] Error: {e}")
            await asyncio.sleep(10)



# --- Daily database backup ---
_backup_done_date: str = ""

async def backup_loop():
    """每天 20:00 CST 用 pg_dump 备份 ledger + market 两个 schema, 保留最近 30 份。

    SQLite 时代是 shutil.copy2 整个文件; PG 时代换 pg_dump 自定义格式(-Fc, 可
    pg_restore 精确还原)。找不到 pg_dump 就记日志跳过 —— 备份缺一天不该连累行情。
    """
    global _backup_done_date
    import subprocess
    import shutil as _shutil
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)

    def _find_pg_dump() -> str | None:
        found = _shutil.which("pg_dump")
        if found:
            return found
        for base in (r"D:\Program Files\PostgreSQL", r"C:\Program Files\PostgreSQL"):
            p = Path(base)
            if not p.is_dir():
                continue
            for v in sorted(p.iterdir(), reverse=True):
                cand = v / "bin" / "pg_dump.exe"
                if cand.exists():
                    return str(cand)
        return None

    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            cst_now = utc_now + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            hour = cst_now.hour

            if hour == 20 and today != _backup_done_date:
                pg_dump = _find_pg_dump()
                if pg_dump:
                    dst = backup_dir / f"licai_{today}.dump"
                    r = await asyncio.to_thread(
                        subprocess.run,
                        [pg_dump, "--dbname", config.pg_dsn, "--file", str(dst),
                         "--format", "custom", "--schema", "ledger", "--schema", "market"],
                        capture_output=True, text=True, timeout=300)
                    if r.returncode == 0:
                        _backup_done_date = today
                        print(f"[backup] Database backed up to {dst}")
                    else:
                        print(f"[backup] pg_dump 失败: {(r.stderr or '')[:200]}")
                else:
                    print("[backup] 找不到 pg_dump, 今天没备份 (把它加进 PATH 即可)")

                # Keep only last 30 backups
                backups = sorted(backup_dir.glob("licai_*.dump")) + sorted(backup_dir.glob("portfolio_*.db"))
                for old in backups[:-30]:
                    old.unlink()

            await asyncio.sleep(300)  # check every 5 minutes
        except Exception as e:
            print(f"[backup] Error: {e}")
            await asyncio.sleep(300)


# --- Morning briefing daily loop ---
_briefing_done: dict[int, str] = {}   # {uid: 最后生成日}

async def briefing_loop():
    """Generate LLM briefing for each holding around 9:00 CST on weekdays.

    Once per day. Runs asynchronously while market opens at 9:30 so user
    sees it before placing orders.
    """
    from datetime import datetime, timezone, timedelta
    from services.morning_briefing import generate_all_briefings
    from database import list_user_ids
    from services.context import run_as_user

    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute

            # Window: weekdays 8:55 ~ 9:10 CST, once per day, per user
            if cst_now.weekday() < 5 and 535 <= t <= 550:
                for uid in await list_user_ids():
                    if _briefing_done.get(uid) == today:
                        continue
                    with run_as_user(uid):
                        await feishu_notify.refresh_config()
                        print(f"[briefing] Generating morning briefings for {today} (user {uid})")
                        try:
                            results = await generate_all_briefings()
                            _briefing_done[uid] = today
                            print(f"[briefing] Done: {len(results)} briefings saved")
                            # Push a one-line summary to feishu (signal 模型: 客观信息倾向, 非操作建议)
                            if feishu_notify.is_enabled() and results:
                                lines = [f"{today} 早盘简报"]
                                for b in results:
                                    sig = b.get("signal", "中性")
                                    lines.append(
                                        f"【{b.get('stock_name')}】{sig} — {b.get('summary', '')}"
                                    )
                                await feishu_notify.send_text("\n".join(lines))
                        except Exception as e:
                            print(f"[briefing] Generation failed: {e}")

            await asyncio.sleep(60)
        except Exception as e:
            print(f"[briefing] Loop error: {e}")
            await asyncio.sleep(120)
