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


# --- 收盘持仓小结(纯数据, 非 LLM) ---
_eod_done_date: str = ""

async def eod_summary_loop():
    """交易日 15:10~15:40 窗口推一次收盘小结: 持仓涨跌归因 + 事件(上榜/涨跌停/预告披露) + 大盘。"""
    global _eod_done_date
    from datetime import datetime, timezone, timedelta
    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute
            if 910 <= t <= 940 and today != _eod_done_date:
                from services.market_data import _is_a_share_trading_day
                if _is_a_share_trading_day(cst_now.date()):
                    # 收盘小结是个人数据(读各自持仓/自选), 逐用户推送; 下面 5 个市场级
                    # 入档任务(板块份额/涨停档案/归类/补日线)与用户无关, 只跑一次。
                    from database import list_user_ids
                    from services.context import run_as_user
                    for uid in await list_user_ids():
                        with run_as_user(uid):
                            await feishu_notify.refresh_config()
                            from services.eod_summary import push_eod_summary
                            r = await push_eod_summary()
                            print(f"[eod] 收盘小结 {today} user={uid} pushed={r.get('pushed')}")
                    try:
                        from services.sector_share import archive_today
                        n = await archive_today()
                        print(f"[eod] 板块份额入档 {n} 行")
                    except Exception as e:
                        print(f"[eod] 板块份额入档失败: {e}")
                    try:
                        # 逐只涨停档案(封单额/首封时刻)。封单额是盘口快照, 盘中取到的会变,
                        # 只有收盘后这一份才是定格值 —— 所以必须挂在收盘窗口里, 不能盘中攒。
                        from services.limit_up_pool import sync_today
                        lu = await sync_today()
                        print(f"[eod] 涨停档案 {lu.get('date')} {lu.get('n')} 只 (源={lu.get('source')})")
                    except Exception as e:
                        print(f"[eod] 涨停档案落库失败: {e}")
                    try:
                        # 归类层。必须排在涨停档案之后 —— 板/进/题 三个轴都是从那份档案回放的。
                        # 钱轴(全市场成交额榜)只有"现在"能取到, 所以也只能在这儿攒。
                        from services.stock_tags import sync_today
                        tg = await sync_today()
                        print(f"[eod] 归类层 {tg.get('date')} {tg.get('rows')} 行 "
                              f"(涨停 {tg.get('涨停')} / 钱轴 {tg.get('钱轴')})")
                    except Exception as e:
                        print(f"[eod] 归类层失败: {e}")
                    try:
                        # 给新上榜的涨停股补本地日线 —— 分池兑现率的次日行情全靠它, 没有的话
                        # 覆盖率会一路掉回闸门以下。单次限量, 剩余量照实打印, 不做无声截断。
                        from services.limit_up_pool import warm_pool_klines
                        wk = await warm_pool_klines()
                        if wk.get("missing"):
                            print(f"[eod] 涨停股日线补齐 {wk['filled']}/{wk['missing']} "
                                  f"(失败 {wk['failed']}, 还剩 {wk['remaining']} 只下次补)")
                    except Exception as e:
                        print(f"[eod] 涨停股日线补齐失败: {e}")
                    try:
                        # 榜单里没日线的票补一批: 概念线的资金曲线要靠它, 涨幅榜天天换新面孔
                        from services.concept_trend import warm_cache
                        w = await warm_cache()
                        print(f"[eod] 榜单日线补齐 {w.get('fetched')}/{w.get('missing')} 只")
                    except Exception as e:
                        print(f"[eod] 榜单日线补齐失败: {e}")
                _eod_done_date = today
            await asyncio.sleep(120)
        except Exception as e:
            print(f"[eod] Loop error: {e}")
            await asyncio.sleep(300)


_dca_done: dict[int, str] = {}     # {uid: 最后触发日}
_dca_settle: dict[int, str] = {}   # {uid: 已跑过自动结算的日期}
_dca_catchup: set[int] = set()     # 已补过隔夜积压的用户

async def dca_loop():
    """每天最多跑一次定投扫描.

    策略: 当天还没跑过 (today != _dca_done_date) 就立即跑, 不再卡时间窗口
    避免漏触发 (server 中午才开机也能补)。fire_due_dcas 自身扫所有 next_due<=today
    所以多日漏跑也能一次补齐.

    触发之后还要结算: 定投份额靠 T+1 净值确认, 以前只有手动「一键确认」接口,
    出门几天回来就是一堆 pending, 成本口径一直飘。这里每晚 19:00 后自动跑一次,
    另外进程启动时若发现隔夜积压立即补一次(出差回来打开就是干净的)。
    settle_pending 扫的是全部 pending 而不只今天的, 所以漏几天也能一次追平。"""
    from datetime import datetime, timezone, timedelta
    from services.dca import fire_due_dcas
    from database import list_user_ids
    from services.context import run_as_user

    async def _settle(reason: str):
        from api.assets_routes import settle_pending_dca
        try:
            r = await settle_pending_dca()
            # 一笔没结算也要打, 否则"净值没出"和"结算逻辑挂了"在日志里长得一样
            print(f"[dca] {reason}: 结算 {r.get('settled', 0)} 笔, 净值未出跳过 {r.get('skipped', 0)} 笔")
            if r.get("settled"):
                if feishu_notify.is_enabled():
                    await feishu_notify.send_text(
                        f"{reason}: 自动确认定投 {r['settled']} 笔"
                        + (f", 净值未出 {r['skipped']} 笔待下次" if r.get("skipped") else ""))
            return True
        except Exception as e:
            print(f"[dca] settle_pending failed: {e}")
            return False

    async def _has_stale_pending(today: str) -> bool:
        """存在早于今天的 pending 流水 → 说明漏结算了(通常是 server 关着)。"""
        from database import list_external_assets, list_external_actions
        for a in await list_external_assets():
            if (a.get("asset_type") or "") != "FUND":
                continue
            for x in await list_external_actions(a["id"]):
                if ((x.get("status") or "confirmed") == "pending"
                        and (x.get("action_type") or "").upper() in ("ADD", "BUY", "REDEEM", "SELL")
                        and (x.get("trade_date") or "")[:10] < today):
                    return True
        return False

    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")

            for uid in await list_user_ids():
                with run_as_user(uid):
                    await feishu_notify.refresh_config()

                    if today != _dca_done.get(uid):
                        try:
                            fired = await fire_due_dcas()
                            _dca_done[uid] = today
                            if fired:
                                print(f"[dca] user={uid} Fired {len(fired)} schedules on {today}")
                                if feishu_notify.is_enabled():
                                    lines = [f"💸 {today} 定投触发 {len(fired)} 笔"]
                                    for f in fired:
                                        v = f["value"]
                                        unit = "¥" if f["mode"] == "amount" else "份"
                                        lines.append(f"  asset#{f['asset_id']} {unit}{v} → action #{f['action_id']} (pending)")
                                    await feishu_notify.send_text("\n".join(lines))
                        except Exception as e:
                            print(f"[dca] fire_due_dcas failed: {e}")

                    # 启动补账: 有隔夜积压就立刻结算一次, 不等到晚上
                    if uid not in _dca_catchup:
                        _dca_catchup.add(uid)
                        try:
                            if await _has_stale_pending(today):
                                await _settle("隔夜积压补账")
                        except Exception as e:
                            print(f"[dca] catchup check failed: {e}")

                    # 每晚净值出了之后自动结算一次(海外基金当天没出的, 明晚这一轮补上)
                    if today != _dca_settle.get(uid) and cst_now.hour >= 19:
                        if await _settle(f"{today} 自动结算"):
                            _dca_settle[uid] = today

            await asyncio.sleep(60)
        except Exception as e:
            print(f"[dca] Loop error: {e}")
            await asyncio.sleep(120)


_rule_mine: dict[int, str] = {}   # {uid: 最后挖掘日}


async def rule_mine_loop():
    """每周挖一次"用户纠正"→ 起草候选规则进待审队列(人批准才进 prompt)。

    周一早上 9:20 前后跑, 每次最多起草 3 条 —— 后台循环不该悄悄烧模型额度, 而且待审
    队列一次涌进十条也没人看得完。批准/否决在设置页里点。
    """
    from datetime import datetime, timezone, timedelta
    from database import list_user_ids
    from services.context import run_as_user
    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute
            if cst_now.weekday() == 0 and 560 <= t <= 600:
                for uid in await list_user_ids():
                    if _rule_mine.get(uid) == today:
                        continue
                    _rule_mine[uid] = today      # 先占位: 失败也不在同一窗口内反复重试
                    with run_as_user(uid):
                        from services import rule_forge as rf
                        st = await rf.mine_new(max_drafts=3)
                print(f"[rules] 本周挖掘: 新纠正 {st['corrections']} 条, "
                      f"新增候选 {st['added']}, 判为非规则 {st['skipped']}, "
                      f"重复 {st['dup']}, 失败 {st['failed']}")
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[rules] Loop error: {e}")
            await asyncio.sleep(1800)
