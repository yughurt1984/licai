"""回填 symbol_dict 的 isin / figi / cusip(数据源: EODHD id-mapping API)。

设计背景: 免费档 20 请求/天, 但每请求最多返回 1000 行。
逐 symbol 查询(旧版)需 1 请求/行 → 几百行就爆额度, 不可行。
故改成两阶段:

  阶段 A  --pull  (耗 API, 受每日预算约束, 可断点续拉)
      按交易所(filter[ex]=US/HK/SH/SZ)分页全量拉取 id-mapping,
      每页 1000 行, 累积写入本地缓存 data/eodhd_id_mapping/<EX>.json,
      进度(progress.json)记录哪些交易所已拉完、进行中的 offset。
      用 --budget 限制本次最多用几次请求, 超预算即停, 下次续拉。

  阶段 B  --match (纯本地, 0 API 请求)
      载入本地缓存, 建 symbol->映射 字典, 遍历 symbol_dict,
      用 ticker+suffix 重建 EODHD symbol 做匹配, 回填 isin/figi/cusip。

  --probe  单次探测: 拉 2 行 US 并打印原始 JSON, 确认 symbol 字段格式/
           交易所代码/返回字段(仅耗 1 次请求, 必做前置校验)。

用法:
    python scripts/update_isin.py --probe                 # 先确认字段格式(1 次请求)
    python scripts/update_isin.py --pull --ex SH --budget 20   # 拉 SH(受 20 次/天约束, 自动续拉)
    python scripts/update_isin.py --run  --ex SH --budget 20   # 拉 SH 并立刻写库
    python scripts/update_isin.py --match                 # 仅本地匹配回填(0 请求)
    python scripts/update_isin.py --match --all            # 覆盖已有值
    python scripts/update_isin.py --match --limit 50       # 先试 50 行
    python scripts/update_isin.py --token XXXX             # 覆盖 EODHD_API_TOKEN

分多天手动运行, 不重复/不浪费额度的关键:
  * 进度记在 data/eodhd_id_mapping/progress.json, 记录每个交易所"已拉取到第几条"。
  * 不指定 --ex 时按 CONFIG_EXCHANGES / symbol_dict 已有 suffix 自动选市场。
  * 每天只传 --budget(默认 20), 脚本从进度自动续拉, 绝不重拉已拉过的区间。
  * --run 拉完当天额度后立刻把缓存写进 symbol_dict.isin/figi/cusip。
  * 想固化运行方式: 改脚本顶部 CONFIG_ACTION(动作)/ CONFIG_EXCHANGES(市场), 之后直接
    `python scripts/update_isin.py`(不带参数)即按文件配置执行。

依赖 config.env 的 EODHD_API_TOKEN。
"""
import os
import sys
import json
import asyncio
import argparse
import logging
import time

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, "config.env"))

from database import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE = "https://eodhd.com/api/id-mapping"
CACHE_DIR = os.path.join(ROOT_DIR, "data", "eodhd_id_mapping")
PROGRESS_FILE = os.path.join(CACHE_DIR, "progress.json")

# symbol_dict.suffix -> EODHD 交易所代码(用 --probe 确认后按需调整)
SUFFIX_TO_EX = {"US": "US", "HK": "HK", "SH": "SHG", "SZ": "SZ"}
DEFAULT_SUFFIXES = list(SUFFIX_TO_EX.keys())
PAGE_LIMIT = 1000

# ===== 手动运行配置(改这里即可, 之后直接 `python scripts/update_isin.py` 按此运行) =====
# action: 默认动作; 可选 "probe"/"pull"/"run"/"match"; 设为 None 则仍需命令行指定动作
#   例: 每次只想拉 SH 并写库 -> CONFIG_ACTION="run"
# exchanges: 指定要拉的市场(字符串或列表均可); None=自动取 symbol_dict 已有 suffix
# SHG=上海证券交易所, SZ=深圳证券交易所, US=美股, HK=港股
#   例: 只跑上海所 -> CONFIG_EXCHANGES="SH" 或 ["SH","SZ"]
CONFIG_ACTION = "Run"          
CONFIG_EXCHANGES = "SHG"

_REQ_DELAY = 0.3
_RATE_SLEEP = 60


def _api_token() -> str:
    return os.environ.get("EODHD_API_TOKEN", "")


def _load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {"done": [], "next_offset": {}}


def _save_progress(p: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def _load_cache(ex: str) -> list:
    fp = os.path.join(CACHE_DIR, f"{ex}.json")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return []


def _dedup_records(records: list) -> list:
    """按 symbol 去重(保留首次出现), 避免手动切片/续拉时缓存出现重复行。"""
    seen: set[str] = set()
    out: list = []
    for r in records:
        k = str(r.get("symbol") or "").upper()
        if k:
            if k in seen:
                continue
            seen.add(k)
        out.append(r)
    return out


def _save_cache(ex: str, records: list) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f"{ex}.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)


def _parse_records(j: object) -> list:
    """兼容 EODHD 两种返回形态: 顶层列表, 或 {'data': [...]}。"""
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        d = j.get("data")
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            return [d]
    return []


def _get_page(ex: str, offset: int, token: str, sess: requests.Session, limit: int = PAGE_LIMIT) -> list | None:
    """拉一页(filter[ex]), 返回记录列表; 429 退避重试; 其它失败返回 None。"""
    params = {
        "filter[ex]": ex,
        "page[limit]": limit,
        "page[offset]": offset,
        "api_token": token,
        "fmt": "json",
    }
    for attempt in range(4):
        try:
            r = sess.get(BASE, params=params, timeout=30)
        except requests.RequestException as e:
            logger.warning(f"  [{ex}] offset={offset} 请求异常: {e}; 重试 {attempt+1}/4")
            time.sleep(3)
            continue
        if r.status_code == 429:
            logger.warning(f"  [{ex}] offset={offset} 限流(429), 休眠 {_RATE_SLEEP}s")
            time.sleep(_RATE_SLEEP)
            continue
        if r.status_code != 200:
            logger.warning(f"  [{ex}] offset={offset} HTTP {r.status_code}: {(r.text or '')[:160]}")
            return None
        try:
            j = r.json()
        except ValueError:
            logger.warning(f"  [{ex}] offset={offset} 响应非 JSON: {(r.text or '')[:160]}")
            return None
        return _parse_records(j)
    return None


def do_probe(token: str) -> None:
    """单次探测: 拉 2 行 US, 打印原始 JSON, 确认 symbol 格式/字段。耗 1 次请求。"""
    sess = requests.Session()
    recs = _get_page("US", 0, token, sess, limit=2)
    if recs is None:
        logger.error("探测失败(见上)。请检查 token / 网络。")
        return
    print("=== probe 原始返回(前 2 行 US) ===")
    print(json.dumps(recs, indent=2, ensure_ascii=False))
    print("\n=== 提示 ===")
    print("1) 看 symbol 字段是否带后缀(如 AAPL.US / 600519.SH), 据此调整 SUFFIX_TO_EX。")
    print("2) 确认返回含 isin / figi / cusip 字段。")


def do_pull(token: str, exchanges: list, budget: int) -> None:
    """按交易所分页全量拉取, 写入本地缓存; 受 budget 约束, 断点续拉(靠 progress.json)。

    每个交易所从 next_offset 接着拉, 拉满 1000 条则 offset+1000 继续, 直到某页 <1000(真实尾页)
    或空页(无更多数据), 才标记 done。分多天运行只传 --budget, 自动续拉不重复不遗漏。
    """
    prog = _load_progress()
    done = set(prog.get("done", []))
    next_off = dict(prog.get("next_offset", {}))

    sess = requests.Session()
    used = 0
    for ex in exchanges:
        if ex in done:
            logger.info(f"[{ex}] 已完成, 跳过")
            continue
        offset = int(next_off.get(ex, 0))
        records = _load_cache(ex)
        logger.info(f"[{ex}] 从 offset={offset} 续拉(已缓存 {len(records)} 行)")
        while True:
            if used >= budget:
                logger.warning(f"已达本次预算 {budget} 次请求, 停止。下次续拉(进度已保存)。")
                prog["next_offset"] = next_off
                _save_progress(prog)
                return
            page = _get_page(ex, offset, token, sess)
            used += 1
            if page is None:
                logger.error(f"[{ex}] offset={offset} 拉取失败, 暂停(进度已保存, 可重试)")
                prog["next_offset"] = next_off
                _save_progress(prog)
                return
            if not page:
                done.add(ex)
                next_off.pop(ex, None)
                logger.info(f"[{ex}] 无更多数据, 标记完成(共 {len(records)} 行)")
                break
            records = _dedup_records(records + page)
            _save_cache(ex, records)
            if len(page) < PAGE_LIMIT:
                done.add(ex)
                next_off.pop(ex, None)
                logger.info(f"[{ex}] 拉到真实尾页(共 {len(records)} 行, 本次用 {used} 次请求)")
                break
            offset += PAGE_LIMIT
            next_off[ex] = offset
            prog["next_offset"] = next_off
            _save_progress(prog)   # 每页保存进度, 下次从此 offset 续拉
            time.sleep(_REQ_DELAY)
    prog["done"] = sorted(done)
    prog["next_offset"] = next_off
    _save_progress(prog)
    logger.info(f"全部完成。本次共使用 {used} 次请求。")


def _index_caches() -> tuple[dict, dict]:
    """载入所有缓存, 建两个索引: symbol(大写) -> rec; (交易所代码, ticker大写) -> rec。"""
    by_symbol: dict[str, dict] = {}
    by_ex_ticker: dict[tuple[str, str], dict] = {}
    if not os.path.isdir(CACHE_DIR):
        return by_symbol, by_ex_ticker
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json") or fn == "progress.json":
            continue
        ex = fn[:-5]
        for rec in _load_cache(ex):
            sym = str(rec.get("symbol") or "").upper()
            if sym:
                by_symbol[sym] = rec
            # 从 symbol 解析交易所后缀作 fallback 键
            ticker = str(rec.get("ticker") or "").upper()
            suffix = sym.split(".")[-1] if "." in sym else ex.upper()
            if ticker:
                by_ex_ticker[(suffix, ticker)] = rec
    return by_symbol, by_ex_ticker


async def do_match(force: bool, limit: int, suffixes: list) -> None:
    """本地匹配: ticker+suffix 重建 EODHD symbol, 回填 isin/figi/cusip(0 API 请求)。"""
    by_symbol, by_ex_ticker = _index_caches()
    if not by_symbol and not by_ex_ticker:
        logger.error("本地缓存为空: 请先 --pull 拉取。")
        return
    logger.info(f"缓存索引: by_symbol={len(by_symbol)} by_ex_ticker={len(by_ex_ticker)}")

    suf = [s.upper() for s in suffixes]
    if not suf:
        logger.warning("match: 未指定任何 suffix, 跳过")
        return
    db = await get_db()
    try:
        where = "suffix IN ({})".format(",".join("?" * len(suf)))
        params = list(suf)
        if not force:
            where += (" AND (isin IS NULL OR isin='' OR figi IS NULL OR figi='' "
                      "OR cusip IS NULL OR cusip='')")
        cur = await db.execute(
            f"SELECT code, ticker, suffix FROM symbol_dict WHERE {where} ORDER BY code",
            params)
        rows = await cur.fetchall()
        logger.info(f"待匹配行: {len(rows)} (suffix={suf}, all={force})")

        updated = 0
        nomatch = 0
        for r in rows:
            code, ticker, suffix = str(r[0]), str(r[1] or ""), str(r[2] or "").upper()
            ex_code = SUFFIX_TO_EX.get(suffix, suffix)
            sym_key = f"{ticker}.{ex_code}".upper()
            rec = by_symbol.get(sym_key) or by_ex_ticker.get((ex_code, ticker.upper()))
            if not rec:
                nomatch += 1
                continue
            isin = str(rec.get("isin") or "").strip()
            figi = str(rec.get("figi") or "").strip()
            cusip = str(rec.get("cusip") or "").strip()
            await db.execute(
                "UPDATE symbol_dict SET isin=?, figi=?, cusip=?, "
                "data_source='scripts', updated_at=now() WHERE code=?",
                (isin, figi, cusip, code))
            updated += 1
            if limit and updated >= limit:
                break
        if updated:
            await db.commit()   # db.py 写事务需显式提交, 否则 close() 回滚(见 db.py close)
        logger.info(f"完成: 更新 {updated} 行, 无匹配 {nomatch} 行")
    finally:
        await db.close()


async def _resolve_exchanges(ex_arg) -> list:
    """解析要处理的交易所列表: 显式指定优先; 否则自动取 symbol_dict 已有 suffix。"""
    if ex_arg:
        if isinstance(ex_arg, str):   # 兼容 CONFIG_EXCHANGES="SH" 这种单字符串写法
            ex_arg = [ex_arg]
        return [e.upper() for e in ex_arg]
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT DISTINCT suffix FROM symbol_dict WHERE suffix IS NOT NULL")
        rows = await cur.fetchall()
    finally:
        await db.close()
    exs = [str(r[0]).upper() for r in rows if str(r[0]).upper() in SUFFIX_TO_EX]
    if not exs:
        exs = list(SUFFIX_TO_EX.keys())
    return exs


async def main():
    ap = argparse.ArgumentParser(description="回填 symbol_dict 的 isin/figi/cusip(EODHD, 批量+缓存)")
    ap.add_argument("--probe", action="store_true", help="单次探测字段格式(1 次请求)")
    ap.add_argument("--pull", action="store_true", help="按交易所分页全量拉取并缓存(不直接写库)")
    ap.add_argument("--run", action="store_true", help="拉取并立即匹配写入 symbol_dict(=pull+match)")
    ap.add_argument("--match", action="store_true", help="本地匹配回填(0 请求)")
    ap.add_argument("--ex", nargs="*", default=CONFIG_EXCHANGES,
                    help="指定市场(如 SH SZ); 默认按 CONFIG_EXCHANGES 或 symbol_dict 已有 suffix")
    ap.add_argument("--suffix", nargs="*", default=DEFAULT_SUFFIXES,
                    help="--match 处理的 suffix(默认 US HK SH SZ)")
    ap.add_argument("--budget", type=int, default=20, help="--pull 本次最多请求次数(默认 20)")
    ap.add_argument("--all", action="store_true", help="--match 强制覆盖已有值")
    ap.add_argument("--limit", type=int, default=0, help="--match 最多处理 N 行(测试)")
    ap.add_argument("--token", default=_api_token(), help="EODHD token(默认读 EODHD_API_TOKEN)")
    args = ap.parse_args()

    if not args.token:
        logger.error("未配置 EODHD_API_TOKEN: 请写入 config.env 或用 --token 传入")
        return

    # 动作优先级: 命令行 --probe/--pull/--run/--match > 文件 CONFIG_ACTION
    actions = [(args.probe, "probe"), (args.pull, "pull"),
               (args.run, "run"), (args.match, "match")]
    chosen = [name for flag, name in actions if flag]
    if len(chosen) > 1:
        logger.error("只能指定一个动作: " + "/".join(chosen))
        return
    action = chosen[0] if chosen else CONFIG_ACTION
    if not action:
        ap.print_help()
        return

    if action == "probe":
        do_probe(args.token)
        return

    if action == "pull":
        exchanges = await _resolve_exchanges(args.ex)
        logger.info(f"--pull 目标市场: {exchanges}, 预算 {args.budget} 次")
        do_pull(args.token, exchanges, args.budget)
        return

    if action == "run":
        run_ex = await _resolve_exchanges(args.ex)
        logger.info(f"--run 目标市场: {run_ex}, 预算 {args.budget} 次")
        do_pull(args.token, run_ex, args.budget)
        # 拉完立即匹配写入 symbol_dict(纯本地, 0 额外 API 请求)
        match_suffixes = [s for s, e in SUFFIX_TO_EX.items() if e in run_ex] or list(run_ex)
        await do_match(False, 0, match_suffixes)
        return

    if action == "match":
        await do_match(args.all, args.limit, args.suffix)
        return


if __name__ == "__main__":
    asyncio.run(main())
