"""区间盈亏归因: 逐标的累加 [起始快照日, 今天] 的当日盈亏(券商口径)。

数据源 portfolio_snapshots: 每次打开看板 lazy 落一条当日快照, by_asset = {code: 当日盈亏}。
当日盈亏 = 现市值 + 今日卖出所得 − 昨收市值 − 今日买入成本(含手续费), 已净掉期间现金流,
所以区间盈亏 = 起始日之后各快照的当日盈亏按标的累加, 不再用「市值差−现金流」旧算法。

老快照(by_asset 存市值、day_pnl 为空)口径不同, 跳过; 未打开看板的日子无快照, 那段盈亏
不计入 —— 历史深度随使用自然增长。

用法: python scripts/interval_pnl.py 2026-07-29
"""
from __future__ import annotations

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(start: str) -> None:
    from database import list_portfolio_snapshots
    from api.portfolio_routes import list_holdings

    snaps = await list_portfolio_snapshots(limit=730)
    # 只认新口径快照(day_pnl 有值); 按日期升序累加
    days = sorted((r for r in snaps if r.get("day_pnl") is not None),
                  key=lambda r: r["snap_date"])
    available = [r["snap_date"] for r in days]
    if start not in available:
        print(f"没有 {start} 的有效快照(当日盈亏口径), 已有: {available[-8:]}")
        return

    # 名称映射: 快照只有 code, 名称取当前持仓(已清仓标的退化为 code 显示)
    names: dict[str, str] = {}
    for h in await list_holdings():
        h = h if isinstance(h, dict) else h.model_dump()
        if h.get("stock_code"):
            names[h["stock_code"]] = h.get("stock_name") or h["stock_code"]

    # 累加 (start, 今天] 的当日盈亏(起始日自身的当日盈亏属于基准之前, 不计入区间)
    pnl_by_code: dict[str, float] = {}
    counted_days = 0
    for r in days:
        d = r["snap_date"]
        if d <= start:
            continue
        counted_days += 1
        for code, v in (json.loads(r.get("by_asset") or "{}") or {}).items():
            if v is None:
                continue
            pnl_by_code[code] = pnl_by_code.get(code, 0.0) + float(v)

    rows = [(pnl, code, names.get(code, code))
            for code, pnl in pnl_by_code.items() if abs(pnl) >= 0.005]
    rows.sort()

    print(f"\n区间 {start} 收盘 → 最近快照   逐标的盈亏(当日盈亏累加, {counted_days} 个快照日)")
    print(f"{'盈亏':>11}  标的")
    for pnl, code, name in rows:
        print(f"{pnl:>11,.2f}  {name} ({code})")
    tot = sum(r[0] for r in rows)
    print(f"{'-' * 56}\n{tot:>11,.2f}  区间盈亏合计 ({len(rows)} 项)")
    print("注: 只累加有快照的日子; 没打开看板的日期无记录, 合计会少于真实区间盈亏。")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "2026-07-29"))
