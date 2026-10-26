# 一次性自检: 在临时库上把多用户改造过的主写路径全部过一遍。
# 目的: 抓"SQL 加了 user_id 占位符、参数没跟上"这类 bug(asyncpg 立刻抛参数数不匹配)。
# 跑完删库, 不碰 allassets。
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from config import config

ADMIN_DSN = config.pg_dsn.rsplit("/", 1)[0] + "/postgres"
TMP = "licai_fixcheck"

FAILS = []


def check(name, cond=True, extra=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} {extra}")
    if not cond:
        FAILS.append(name)


async def main() -> int:
    c = await asyncpg.connect(ADMIN_DSN)
    await c.execute(f'DROP DATABASE IF EXISTS "{TMP}"')
    await c.execute(f'CREATE DATABASE "{TMP}"')
    await c.close()

    os.environ["PG_DSN"] = config.pg_dsn.rsplit("/", 1)[0] + f"/{TMP}"
    import importlib
    import config as cfg_mod
    importlib.reload(cfg_mod)
    import database
    importlib.reload(database)
    import db as dbm
    importlib.reload(dbm)
    database.get_db = _mk_get_db(dbm)

    from services.context import run_as_user
    await database.init_db()

    with run_as_user(7):
        # 持仓全链路(用户报的 bug 路径): add → update(broker) → action → update(shares/cost)
        await database.add_holding("sh600000", "浦发银行", 100, 10.0)
        await database.update_holding("sh600000", broker="招商证券")          # ← 原崩溃点
        hid = await database.add_position_action("sh600000", "BUY", 10.0, 100,
                                                 trade_date="2026-10-25", trade_time="09:31",
                                                 broker="招商证券", fx_rate=None)
        check("add_position_action RETURNING", isinstance(hid, int) and hid > 0)
        await database.update_position_action(hid, price=10.5, fee=5.0, fee_explicit=True)
        await database.update_holding("sh600000", shares=200, cost_price=10.2)  # ← _recompute 用
        h = await database.get_holding("sh600000")
        check("holding updated", h["shares"] == 200 and h["broker"] == "招商证券")
        acts = await database.get_position_actions("sh600000")
        check("action updated", acts and acts[0]["price"] == 10.5 and acts[0]["fee"] == 5.0)

        # 外部资产 + 流水 + DCA
        aid = await database.add_external_asset("FUND", "110022", "易方达", "支付宝", 5000.0, shares=3200.0)
        await database.update_external_asset(aid, broker="支付宝", pending_amount=1.0)
        xid = await database.add_external_action(aid, "BUY", 1000.0, shares=640.0, trade_date="2026-10-01")
        await database.update_external_action(xid, status="confirmed", fee=1.5)
        did = await database.add_dca_schedule(aid, "amount", 500.0, frequency="monthly", day_of_month=5)
        await database.update_dca_schedule(did, status="paused", note="x")
        due = await database.list_due_dca_schedules("2026-12-31")
        check("dca flow", due == [] or all(d["id"] != did for d in due))   # paused 不触发

        # unwind / tranche
        await database.save_unwind_plan("sh600000", 10000.0)
        await database.update_unwind_used_budget("sh600000", 2000.0)
        tid = None
        await database.add_tranche("sh600000", 1, 9.0, 100)
        tr = await database.get_tranches("sh600000")
        tid = tr[0]["id"]
        await database.mark_tranche_executed(tid, 9.1)
        await database.mark_tranche_sold_back(tid, 9.3)
        await database.clear_tranche_sold_back(tid)

        # 自选 + 分组
        await database.add_watchlist("sz000001", "平安银行", 12.0)
        await database.set_watchlist_groups("sz000001", ["金矿", "博弈"])
        await database.reorder_watchlist(["sz000001"], scope="global")
        wl = await database.list_watchlist()
        check("watchlist", len(wl) == 1 and sorted(wl[0]["groups"]) == ["博弈", "金矿"])

        # thesis / 晨报 / 快照 / 现金流
        await database.set_thesis("sh600000", "低估值", facts="{}")
        await database.save_briefing("sh600000", "2026-10-26", "{}")
        await database.save_portfolio_snapshot("2026-10-26", 12345.0, "{}")
        await database.upsert_cashflow("2026-10", 1000, 500, 300)
        cf = await database.get_cashflow("2026-10")
        check("cashflow", cf and cf["income"] == 1000)

        # config 个人键 / 券商 / 告警 / ask / run
        await database.set_config("feishu_webhook_url", "https://x")
        check("per-user config", (await database.get_config("feishu_webhook_url")) == "https://x")
        bid = await database.add_broker("测试券商", 0.0001, 1.0, 0.0001, 1.0)
        await database.update_broker(bid, stock_rate=0.0002, is_default=1)
        await database.add_custom_alert("sh600000", "price_above", 11.0, "m")
        al = await database.get_custom_alerts("sh600000")
        await database.mark_alert_triggered(al[0]["id"])
        sid = await database.create_ask_session("t")
        await database.add_ask_message(sid, "user", "hi")
        ss = await database.list_ask_sessions()
        check("ask session", len(ss) == 1)
        await database.save_ask_run("r1", sid, "market", "q", 1.0)
        await database.update_ask_run("r1", "[]", "done", True, 2.0)
        ar = await database.get_ask_run("r1")
        check("ask_run", ar["status"] == "done")

        # trade_plan 全流程(盯价 mark 是修复点)
        from services import trade_plan as tp
        r = await tp.add_plans([{"code": "sh600000", "side": "BUY", "trigger_op": "<=",
                                 "trigger_price": 9.5, "qty": 100, "plan_date": "2026-10-27"}])
        check("plan add", r["saved"] and r["saved"][0]["id"] > 0)
        await tp.expire_old()
        ok = await tp.cancel(r["saved"][0]["id"])
        check("plan cancel", ok)

        # 记忆/规则
        from services import user_memory as um
        um.untrusted_seen.set(False)   # agent loop 在跑工具前设置; 测试里显式给信任上下文
        await um.remember({"fact": "用户的场外基金扣款均为定投"}, session_id=sid)
        await um.refresh_active_cache()
        check("user_memory", um.active_sync() and um.active_sync()[0]["fact"].startswith("用户的场外"))
        from services import rule_forge as rf
        await rf.add_candidate("测试规则", "正文", "证据", sid)
        rules = await rf.list_rules()
        check("prompt_rule", len(rules) == 1 and rules[0]["title"] == "测试规则")
        await rf.decide(rules[0]["id"], "active")
        await rf.refresh_rules_cache()
        check("rules cache", rf.active_rules_sync() and rf.active_rules_sync()[0]["title"] == "测试规则")

        # 工具缺口
        from services.tool_gaps import record, report
        await record("get_news", {"code": "sh600000"}, {"error": "x"})
        await record("get_news", {"code": "sh600000"}, {"error": "x"})
        rep = await report(1)
        check("tool_gap upsert", rep and rep[0]["hits"] == 2)

        # 导出/导入(按用户)
        from api.export_routes import export_all
        resp = await export_all()
        import json
        payload = json.loads(resp.body)
        check("export per-user", len(payload["tables"]["holdings"]) == 1
              and payload["tables"]["holdings"][0]["user_id"] == 7)
    # 另一个用户不可见
    with run_as_user(8):
        check("isolation", await database.get_holding("sh600000") is None
              and (await database.get_config("feishu_webhook_url")) is None)

    await dbm.close_pool()
    c = await asyncpg.connect(ADMIN_DSN)
    await c.execute(f'DROP DATABASE IF EXISTS "{TMP}"')
    await c.close()

    print(f"\n[FIXCHECK] {'ALL PASS' if not FAILS else f'{len(FAILS)} FAILURES: {FAILS}'}")
    return 1 if FAILS else 0


def _mk_get_db(dbm):
    async def get_db():
        return await dbm.get_conn()
    return get_db


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
