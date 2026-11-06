"""底稿全市场导入: akshare 一次拉取 → symbol_dict 批量 upsert(新结构, 含 sub_assets_type 归类)。

你手动择时执行(单次联网任务, 与应用启动无关):
    python scripts/import_symbol_dict.py                 # 全部类型
    python scripts/import_symbol_dict.py --types a,etf   # 只导指定类型
    python scripts/import_symbol_dict.py --types hk,us,fut,opt,fund

类型与来源(全部免费接口, 逐个 best-effort, 单个失败不影响其余):
    a     A股个股        stock_info_a_code_name          (~5400, sub=a_comm_stock)
    etf   场内基金       fund_etf_category_sina          (~1000, sub=cn_etf_fund)
    hk    港股           stock_hk_spot_em                (~2700, sub=hk_comm_stock)
    us    美股           stock_us_spot_em                (~1万, 标准码 ticker.US, sub=us_comm_stock)
    fut   期货主力       futures_display_main_sina       (~70, CU0→CU 品种级, sub=cn_futures)
    opt   期权合约       option_current_em 多标的聚合    (万级, sub=cn_options)
    fund  场外基金       fund_name_em                    (~2万, sub=cn_otc_fund)

行结构: (code, name, ticker, sub_assets_type, exchange) —— code 统一标准「代码.市场」
(600111.SH / 00700.HK / SNDK.US), exchange 为市场段位(HK/US 供 futu 拼接等用)。
sub_assets_type 必须是 assets_classify 里已登记的小类键(人工维护分类与费率)。
期货品种行的交易所后缀/每手乘数数据源拿不到, 导入后按需手工补(UPDATE symbol_dict)。

导入后资产页搜股与添加持仓的名称自动查询覆盖全市场; 应用无需重启(名称映射有缓存,
重启或等缓存过期生效)。老结构库(含费率列)首次启动会被 init_db 自动改名
symbol_dict_copy 并重建空表 —— 升级后跑一次本脚本即可。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _a_suffix(bare: str) -> str:
    """6 位裸码 → 交易所后缀(与 database.py 迁移同规则): 北交 920/4/8, 沪 5/6/9, 其余深。"""
    return "BJ" if (bare[:3] == "920" or bare[:1] in ("4", "8")) \
        else ("SH" if bare[:1] in ("5", "6", "9") else "SZ")


def _pull_a():
    import akshare as ak
    df = ak.stock_info_a_code_name()
    rows = []
    for _, r in df.iterrows():
        bare = str(r["code"]).strip()
        if bare.isdigit() and len(bare) == 6:
            suf = _a_suffix(bare)
            rows.append((f"{bare}.{suf}", str(r["name"]).strip(), bare, "a_comm_stock", suf))
    return rows


def _pull_etf():
    import akshare as ak
    df = ak.fund_etf_category_sina(symbol="ETF基金")
    rows = []
    for _, r in df.iterrows():
        bare = str(r["代码"]).strip()[-6:]
        if len(bare) == 6 and bare.isdigit():
            suf = _a_suffix(bare)
            rows.append((f"{bare}.{suf}", str(r["名称"]).strip(), bare, "cn_etf_fund", suf))
    return rows


def _pull_hk():
    import akshare as ak
    df = ak.stock_hk_spot_em()
    rows = []
    for _, r in df.iterrows():
        d = str(r["代码"]).strip()
        if d.isdigit():
            d = d.zfill(5)
            rows.append((f"{d}.HK", str(r["名称"]).strip(), d, "hk_comm_stock", "HK"))
    return rows


def _pull_us():
    import akshare as ak
    df = ak.stock_us_spot_em()
    rows = []
    for _, r in df.iterrows():
        tk = str(r["代码"]).strip().upper()
        if tk:
            rows.append((f"{tk}.US", str(r["名称"]).strip(), tk, "us_comm_stock", "US"))
    return rows


def _pull_fut():
    import akshare as ak
    df = ak.futures_display_main_sina()          # 主力连续: symbol=CU0, display_name=沪铜连续
    rows = []
    for _, r in df.iterrows():
        sym = str(r.get("symbol") or "").strip().upper()
        # CU0(主力连续) → CU(品种级): 品种代码是纯字母, 开仓表单按 品种+月份 拼合约
        prod = sym.rstrip("0")
        if not prod or not prod.isalpha():
            continue
        name = str(r.get("display_name") or prod).strip()
        for tail in ("连续", "主连", "主力"):
            name = name.removesuffix(tail).strip()
        rows.append((prod, name or prod, prod, "cn_futures", ""))
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
                rows.append((code, label or code, code, "cn_options", ""))
    return rows


def _pull_fund():
    import akshare as ak
    df = ak.fund_name_em()
    rows = []
    for _, r in df.iterrows():
        bare = str(r["基金代码"]).strip()
        if bare.isdigit():
            rows.append((bare.zfill(6), str(r["基金简称"]).strip(), bare.zfill(6), "cn_otc_fund", ""))
    return rows


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
        print(f"[{t}] ✓ {n} 条 (sub_assets_type 见脚本头注释)")
    print(f"\n合计 upsert {total} 条。应用内搜股缓存过期或重启后生效。")
    return 0 if total else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="a,etf,hk,us,fut,opt,fund",
                    help="逗号分隔: a,etf,hk,us,fut,opt,fund")
    args = ap.parse_args()
    sys.exit(asyncio.run(main([t.strip() for t in args.types.split(",") if t.strip()])))
