"""理财助手 · 演示数据填充脚本.

适用场景:
  1. 第一次 clone 想看效果, 不想录入自己数据 → `python scripts/seed_demo.py --use`
  2. 截图发 README / 演示视频, 又不想暴露真实持仓 → `python scripts/seed_demo.py --use`
  3. 看完 demo 想恢复自己的数据 → `python scripts/seed_demo.py --restore`

数据特点:
  - 6 大类全覆盖: A股/基金/理财/现金/加密 (机器人需 OKX 凭证, 跳过)
  - 跨行业避免单押 (消费/汽车/银行/科技等)
  - 一些浮盈一些浮亏, 演示警告条不同样式
  - 真实股票/基金代码, 实时行情接口能正常拉到

⚠ 演示数据明显是虚构的 (持仓金额规整, 起投日整数), 别误解为投顾建议.
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---- 演示数据定义 ----

DEMO_HOLDINGS = [
    # (code, name, shares, cost_price, buy_days_ago)
    # 建仓日期分散在过去三个月, 让净值曲线/持有天数/事件日历都有真实素材
    ("600519", "贵州茅台",  1,   1620.00, 95),  # 消费 — 白酒龙头, 1 股
    ("002594", "比亚迪",   200,  220.00, 80),   # 汽车 — 新能源
    ("600036", "招商银行",  500, 38.00,  65),    # 金融 — 银行
    ("300750", "宁德时代",  100, 240.00, 50),   # 电气新能源
]

DEMO_ASSETS = [
    # FUND (5 只, 含场内宽基/行业主题/商品 + 场外 + QDII; 真实公开代码方便实时行情可拉)
    # buy_days_ago: 初始建仓日, 让份额流水有历史(净值曲线用)
    {
        "asset_type": "FUND", "code": "510300", "name": "沪深300ETF",
        "platform": "证券账户", "cost_amount": 2100, "shares": 500, "buy_days_ago": 90,
    },
    {
        "asset_type": "FUND", "code": "512480", "name": "半导体ETF",
        "platform": "证券账户", "cost_amount": 3200, "shares": 3000, "buy_days_ago": 70,
    },
    {
        "asset_type": "FUND", "code": "518880", "name": "黄金ETF",
        "platform": "证券账户", "cost_amount": 4500, "shares": 1000, "buy_days_ago": 55,
    },
    {
        "asset_type": "FUND", "code": "000041",
        "name": "华夏全球精选股票(QDII)C",
        "platform": "公募平台", "cost_amount": 2500, "shares": 1000, "buy_days_ago": 45,
    },
    {
        "asset_type": "FUND", "code": "000217", "name": "华安黄金ETF联接C",
        "platform": "公募平台", "cost_amount": 6000, "shares": 3000, "buy_days_ago": 40,
    },
    # WEALTH (3 只, 名称泛化)
    {
        "asset_type": "WEALTH", "code": "DEMO001",
        "name": "稳健型理财 1 号 (演示)",
        "platform": "银行 A", "cost_amount": 5000,
        "annual_yield_rate": 0.025,
        "start_date": (date.today() - timedelta(days=60)).strftime("%Y-%m-%d"),
    },
    {
        "asset_type": "WEALTH", "code": "DEMO002",
        "name": "日开型理财 (演示)",
        "platform": "银行 B", "cost_amount": 10000,
        "annual_yield_rate": 0.022,
        "start_date": (date.today() - timedelta(days=30)).strftime("%Y-%m-%d"),
    },
    {
        "asset_type": "WEALTH", "code": "DEMO003",
        "name": "30 天滚动型 (演示)",
        "platform": "银行 C", "cost_amount": 8000,
        "annual_yield_rate": 0.020,
        "start_date": (date.today() - timedelta(days=15)).strftime("%Y-%m-%d"),
    },
    # CASH (3 只, 货币基金/活期, 名称泛化)
    {
        "asset_type": "CASH", "code": "mmf-a", "name": "货币基金 A",
        "platform": "支付平台 A", "cost_amount": 5000, "manual_value": 5000,
        "annual_yield_rate": 0.015,
    },
    {
        "asset_type": "CASH", "code": "mmf-b", "name": "货币基金 B",
        "platform": "支付平台 B", "cost_amount": 2000, "manual_value": 2000,
        "annual_yield_rate": 0.014,
    },
    {
        "asset_type": "CASH", "code": "mmf-c", "name": "活期账户",
        "platform": "银行 A", "cost_amount": 3000, "manual_value": 3000,
        "annual_yield_rate": 0.016,
    },
    # CRYPTO (1 只, 通用)
    {
        "asset_type": "CRYPTO", "code": "BTC-USDT", "name": "比特币",
        "platform": "加密交易所", "cost_amount": 18000, "shares": 0.05, "buy_days_ago": 85,
    },
]


# ---- 主流程 (PG 版: 备份/恢复走 pg_dump/pg_restore, 写入走 database API) ----

# demo 会清掉的用户数据表(不碰 app_config/symbol_dict/brokers/kline_cache/
# ask_*/tool_gap/prompt_rule/user_memory —— 配置与基础设施要保留)
_DEMO_TABLES = [
    "holdings", "position_actions", "external_assets", "external_asset_actions",
    "unwind_plans", "unwind_tranches", "custom_alerts", "alert_config",
    "cashflow_monthly", "morning_briefings", "portfolio_snapshots",
    "watchlist", "watchlist_group", "position_thesis", "thesis_revision",
    "dca_schedules", "trade_plan",
]


def _pg_tool(name: str) -> str | None:
    import shutil
    found = shutil.which(name)
    if found:
        return found
    for base in (r"D:\Program Files\PostgreSQL", r"C:\Program Files\PostgreSQL"):
        p = Path(base)
        if not p.is_dir():
            continue
        for v in sorted(p.iterdir(), reverse=True):
            cand = v / "bin" / f"{name}.exe"
            if cand.exists():
                return str(cand)
    return None


async def _clear_user_tables():
    from db import get_conn
    db = await get_conn()
    try:
        for t in _DEMO_TABLES:
            await db.execute(f"TRUNCATE ledger.{t} RESTART IDENTITY")
        await db.commit()
    finally:
        await db.close()


async def seed_into():
    """直接调 database API, 不走 HTTP. 服务器没启动时也能跑."""
    from database import (
        init_db, add_holding, add_position_action,
        add_external_asset,
    )
    from services.position_ledger import compute_position_state
    from database import get_position_actions, update_holding

    await init_db()

    # 建仓价 = 建仓日真实收盘(拉不到才用表里的兜底价): 净值曲线上不出现"编造成本 vs
    # 真实行情"的假跳变, demo 的浮盈亏也更像真账户
    async def _close_on(code_, d_):
        try:
            from services.market_data import get_historical_data
            df = await get_historical_data(code_, days=160)
            rows = {str(r["日期"])[:10]: float(r["收盘"]) for _, r in df.iterrows()}
            ks = sorted(k for k in rows if k <= d_)
            return rows[ks[-1]] if ks else None
        except Exception:
            return None

    # A 股持仓 (走 holdings + position_actions, 触发综合成本计算)
    # 建仓日期回填到过去几个月: 净值曲线/持有天数/复盘统计才有真实素材
    for code, name, shares, cost, days_ago in DEMO_HOLDINGS:
        buy_date = (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        cost = (await _close_on(code, buy_date)) or cost
        await add_holding(code, name, shares, cost)
        await add_position_action(code, "BUY", cost, shares,
                                  trade_date=buy_date, note="initial (demo)")
        actions = await get_position_actions(code, limit=500)
        state = compute_position_state(actions, stock_code=code)
        if state["shares"] > 0:
            await update_holding(code, shares=state["shares"], cost_price=state["cost_price"])

    # 外部资产(基金带日期回填的初始申购流水, 份额账本/净值曲线可用;
    # 申购价同样按建仓日真实净值/收盘取)
    from database import add_external_action
    from services.external_assets import _is_onchain_etf
    for a in DEMO_ASSETS:
        cost_amount, unit = a["cost_amount"], None
        if a.get("shares") and a.get("buy_days_ago"):
            d0 = (date.today() - timedelta(days=a["buy_days_ago"])).strftime("%Y-%m-%d")
            code = a["code"]
            if a["asset_type"] == "FUND":
                if _is_onchain_etf(code):
                    unit = await _close_on(code, d0)
                else:
                    try:
                        from services.portfolio_curve import _otc_nav_hist_sync
                        navs = _otc_nav_hist_sync(code)
                        ks = sorted(k for k in navs if k <= d0)
                        unit = navs[ks[-1]] if ks else None
                    except Exception:
                        unit = None
            if unit:
                cost_amount = round(unit * a["shares"], 2)
        aid = await add_external_asset(
            asset_type=a["asset_type"],
            code=a["code"],
            name=a["name"],
            platform=a.get("platform", ""),
            cost_amount=cost_amount,
            shares=a.get("shares"),
            manual_value=a.get("manual_value"),
            note=a.get("note", ""),
            annual_yield_rate=a.get("annual_yield_rate"),
            start_date=a.get("start_date"),
        )
        if a.get("shares") and a.get("buy_days_ago"):
            d0 = (date.today() - timedelta(days=a["buy_days_ago"])).strftime("%Y-%m-%d")
            await add_external_action(
                aid, "BUY", amount=cost_amount, shares=a["shares"],
                unit_price=unit or round(cost_amount / a["shares"], 4),
                trade_date=d0, note="initial (demo)")
    print(f"✓ 已写入 {len(DEMO_HOLDINGS)} 只 A股 + {len(DEMO_ASSETS)} 笔外部资产到 PostgreSQL")


def _demo_dsn() -> str:
    """<库名>_demo 演示库连接串(同实例另一个库, 不动真实账本)。"""
    from urllib.parse import urlsplit, urlunsplit
    from config import config
    parts = urlsplit(config.pg_dsn)
    dbname = (parts.path.lstrip("/") or "postgres") + "_demo"
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def cmd_create():
    """写入 <库名>_demo 演示库(自动建库, 不动真实账本)。给 evals/截图用。"""
    from config import config
    config.config.pg_dsn = _demo_dsn()
    asyncio.run(init_db())
    asyncio.run(_clear_user_tables())
    asyncio.run(seed_into())
    print(f"✓ 演示库就绪: {config.config.pg_dsn}")


def cmd_use():
    """pg_dump 备份当前 ledger → 清用户表 → 写入 demo 数据. 重启服务器生效."""
    from config import config
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = date.today().strftime("%Y%m%d")
    backup = backup_dir / f"pre-demo-{ts}.dump"
    pg_dump = _pg_tool("pg_dump")
    if backup.exists():
        print(f"⚠ 备份已存在 {backup.name}, 跳过覆盖. 想恢复用 --restore.")
    elif pg_dump:
        import subprocess
        r = subprocess.run([pg_dump, "--dbname", config.pg_dsn, "--file", str(backup),
                            "--format", "custom", "--schema", "ledger"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print(f"❌ pg_dump 失败, 中止(避免无备份清库): {(r.stderr or '')[:200]}")
            return
        print(f"✓ 真实数据备份: {backup.name}")
    else:
        print("❌ 找不到 pg_dump, 无法先备份, 中止(避免无备份清库)。把 pg_dump 加进 PATH 后重试。")
        return
    asyncio.run(_clear_user_tables())
    asyncio.run(seed_into())
    print("✓ 演示数据已写入 PostgreSQL. 重启服务器生效.")


def cmd_restore():
    """演示完恢复真实数据(pg_restore --clean 原样还原 --use 时的备份)."""
    from config import config
    backups = sorted((ROOT / "backups").glob("pre-demo-*.dump"))
    if not backups:
        print("❌ 没有找到 pre-demo-*.dump 备份, 无法恢复.")
        return
    pg_restore = _pg_tool("pg_restore")
    if not pg_restore:
        print("❌ 找不到 pg_restore, 无法恢复. 把它加进 PATH 后重试.")
        return
    import subprocess
    r = subprocess.run([pg_restore, "--dbname", config.pg_dsn, "--clean",
                        "--schema", "ledger", str(backups[-1])],
                       capture_output=True, text=True, timeout=600)
    # pg_restore 对"表不存在"之类的告警返回非零也常见(还原本身成功), 看 stderr 摘要即可
    if r.returncode != 0 and "does not exist" not in (r.stderr or ""):
        print(f"⚠ pg_restore 退出码 {r.returncode}: {(r.stderr or '')[:300]}")
    print(f"✓ 真实数据已恢复(来自 {backups[-1].name}). 重启服务器即可.")


def main():
    p = argparse.ArgumentParser(description="理财助手 · 演示数据填充")
    p.add_argument("--use", action="store_true",
                   help="pg_dump 备份当前数据, 清表后写入 demo 数据 (用于截图/演示)")
    p.add_argument("--restore", action="store_true",
                   help="恢复 --use 之前备份的真实数据")
    p.add_argument("--create", action="store_true",
                   help="写入 <库名>_demo 演示库(自动建库, 不动真实账本; evals 默认用这个库)")
    p.add_argument("--user", type=int, default=0,
                   help="demo 数据归属的用户 id (默认 0 = 无主, 供注册用户前的演示)")
    args = p.parse_args()
    from services.context import set_current_user
    set_current_user(args.user)

    if args.use:
        cmd_use()
    elif args.restore:
        cmd_restore()
    elif args.create:
        cmd_create()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
