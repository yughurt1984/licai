"""清空无主数据(user_id=0)—— 多用户验收用。

多用户改造后, 旧单用户时代的测试数据都是"无主"行(user_id=0), 注册用户看不到它们。
验收完确认不要了, 跑这个脚本一次性清掉:

    python scripts/clear_user_data.py           # 先预览将删的行数
    python scripts/clear_user_data.py --apply   # 真删

保留: users/sessions(账号)、symbol_dict/kline_cache/情绪/涨停档案等共享表、
user_id>0 的任何数据、app_config 里的全局键(LLM/代理/TDX 等)。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 有 user_id 列、属于"用户数据"的表(与 init_db 多用户迁移清单一致)
_USER_TABLES = [
    "holdings", "position_actions", "external_assets", "external_asset_actions",
    "unwind_plans", "unwind_tranches", "custom_alerts", "alert_config",
    "cashflow_monthly", "morning_briefings", "portfolio_snapshots",
    "watchlist", "watchlist_group", "position_thesis", "thesis_revision",
    "dca_schedules", "trade_plan", "brokers", "user_memory",
    "ask_session", "ask_message", "ask_run", "prompt_rule",
]


async def main(apply: bool) -> None:
    from db import get_conn
    from database import PER_USER_KEYS

    # app_config 的 uid=0 行里混着全局配置(llm/proxy/tdx), 只清个人键
    ph = ",".join("?" * len(PER_USER_KEYS))
    plans = [(t, f"DELETE FROM ledger.{t} WHERE user_id = 0", ()) for t in _USER_TABLES]
    plans.append(("app_config(个人键)",
                  f"DELETE FROM ledger.app_config WHERE user_id = 0 AND key IN ({ph})",
                  tuple(sorted(PER_USER_KEYS))))

    db = await get_conn()
    try:
        total = 0
        for label, sql, args in plans:
            sel = sql.replace("DELETE FROM", "SELECT COUNT(*) FROM", 1)
            cur = await db.execute(sel, args)
            n = (await cur.fetchone())[0]
            if n:
                print(f"  {label}: {n} 行")
                total += n
        print(f"\n合计 {total} 行无主数据" + ("将被删除" if apply else "(预览, 加 --apply 真删)"))
        if not apply or not total:
            return
        for label, sql, args in plans:
            await db.execute(sql, args)
        await db.commit()
        print("✓ 已清空全部无主数据(user_id=0)")
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删(默认只预览)")
    asyncio.run(main(ap.parse_args().apply))
