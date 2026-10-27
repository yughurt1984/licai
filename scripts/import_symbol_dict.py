"""底稿全市场导入: akshare 一次拉取 → symbol_dict 批量 upsert。

你手动择时执行(单次联网任务, 与应用启动无关):
    python scripts/import_symbol_dict.py                 # 全部类型
    python scripts/import_symbol_dict.py --types a,etf   # 只导指定类型
    python scripts/import_symbol_dict.py --types hk,us,fut,opt,fund

类型与来源(全部免费接口, 逐个 best-effort, 单个失败不影响其余):
    a     A股个股        stock_info_a_code_name          (~5400)
    etf   场内基金       fund_etf_category_sina          (~1000)
    hk    港股           stock_hk_spot_em                (~2700)
    us    美股           stock_us_spot_em                (~1万, 代码为 ticker)
    fut   期货主力       futures_display_main_sina       (~70, CU0/SC0 形态, 与资产页同符号)
    opt   期权合约       option_current_em 多标的聚合    (万级, 沪深ETF期权/股指期权)
    fund  场外基金       fund_name_em                    (~2万)

导入后资产页搜股(A股/ETF/港美股/基金)与添加持仓的名称自动查询立即覆盖全市场;
应用无需重启(_code_name_maps 有 12h 缓存, 重启或等缓存过期生效)。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pull_a():
    import akshare as ak
    df = ak.stock_info_a_code_name()
    return [(str(r["code"]).strip(), str(r["name"]).strip(), "a_stock") for _, r in df.iterrows()
            if str(r["code"]).strip()]


def _pull_etf():
    import akshare as ak
    df = ak.fund_etf_category_sina(symbol="ETF基金")
    return [(str(r["代码"]).strip()[-6:], str(r["名称"]).strip(), "etf") for _, r in df.iterrows()
            if len(str(r["代码"]).strip()[-6:]) == 6 and str(r["代码"]).strip()[-6:].isdigit()]


def _pull_hk():
    import akshare as ak
    df = ak.stock_hk_spot_em()
    return [(str(r["代码"]).strip().zfill(5), str(r["名称"]).strip(), "hk_stock") for _, r in df.iterrows()
            if str(r["代码"]).strip().isdigit()]


def _pull_us():
    import akshare as ak
    df = ak.stock_us_spot_em()
    return [(str(r["代码"]).strip().upper(), str(r["名称"]).strip(), "us_stock") for _, r in df.iterrows()
            if str(r["代码"]).strip()]


def _pull_fut():
    import akshare as ak
    df = ak.futures_display_main_sina()          # 主力连续: symbol=CU0, display_name=沪铜连续
    rows = []
    for _, r in df.iterrows():
        sym = str(r.get("symbol") or "").strip().upper()
        if sym:
            rows.append((sym, str(r.get("display_name") or sym).strip(), "fut"))
    return rows


_OPT_SOURCES = [
    "沪深300期权", "50ETF期权", "300ETF期权", "500ETF期权", "创业板ETF期权",
    "科创50ETF期权", "深100ETF期权", "中证500ETF期权", "1000股指期权", "股指期权",
]


def _pull_opt():
    import akshare as ak
    rows, seen = [], set()
    for name in _OPT_SOURCES:
        try:
            df = ak.option_current_em(symbol=name)
        except Exception as e:
            print(f"  [opt] {name} 拉取失败(跳过): {str(e)[:80]}")
            continue
        for _, r in df.iterrows():
            code = str(r.get("期权代码") or r.get("代码") or "").strip()
            label = str(r.get("期权名称") or r.get("名称") or "").strip()
            if code and code not in seen:
                seen.add(code)
                rows.append((code, label or code, "opt"))
    return rows


def _pull_fund():
    import akshare as ak
    df = ak.fund_name_em()
    return [(str(r["基金代码"]).strip().zfill(6), str(r["基金简称"]).strip(), "fund")
            for _, r in df.iterrows() if str(r["基金代码"]).strip().isdigit()]


_PULLERS = {"a": _pull_a, "etf": _pull_etf, "hk": _pull_hk, "us": _pull_us,
            "fut": _pull_fut, "opt": _pull_opt, "fund": _pull_fund}


async def main(types: list[str]) -> int:
    from database import save_symbol_dict
    total = 0
    for t in types:
        puller = _PULLERS.get(t)
        if not puller:
            print(f"! 未知类型 {t}, 可选: {','.join(_PULLERS)}")
            continue
        print(f"[{t}] 拉取中…")
        try:
            rows = await asyncio.to_thread(puller)
        except Exception as e:
            print(f"[{t}] 失败(跳过): {str(e)[:160]}")
            continue
        if not rows:
            print(f"[{t}] 空结果")
            continue
        n = await save_symbol_dict(rows)
        total += n
        print(f"[{t}] ✓ {n} 条 (asset_type={t})")
    print(f"\n合计 upsert {total} 条。应用内搜股 12h 缓存过期或重启后生效。")
    return 0 if total else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="a,etf,hk,us,fut,opt,fund",
                    help="逗号分隔: a,etf,hk,us,fut,opt,fund")
    args = ap.parse_args()
    sys.exit(asyncio.run(main([t.strip() for t in args.types.split(",") if t.strip()])))
