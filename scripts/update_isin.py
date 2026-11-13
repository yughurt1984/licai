"""回填 symbol_dict 的 isin / figi / cusip(数据源: EODHD id-mapping API)。

EODHD id-mapping 按 ticker(如 AAPL.US / 600519.SH / 00700.HK)返回 ISIN / CUSIP / FIGI。
脚本仅处理 symbol_dict.suffix 属于 EODHD 支持市场的行(默认 US/HK/SH/SZ), 其余(期货/期权等)跳过。
仅回填 isin/figi/cusip 为空的行; --all 可强制覆盖已有值。

用法:
    python scripts/update_isin.py                      # 默认: 只填空值, 全部支持后缀
    python scripts/update_isin.py --suffix US          # 只处理 US
    python scripts/update_isin.py --all                # 强制重填所有行(覆盖已有)
    python scripts/update_isin.py --limit 50           # 最多处理 50 行(测试)
    python scripts/update_isin.py --dry-run            # 只打印待请求 symbol, 不写库
    python scripts/update_isin.py --token XXXX         # 覆盖 EODHD_API_TOKEN

依赖 config.env 中的 EODHD_API_TOKEN(或在上方常量填默认)。
"""
import os
import sys
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
# EODHD 支持的后缀(对应 symbol_dict.suffix); 期货/期权( SHF/CFE... )无证券标识, 跳过
DEFAULT_SUFFIXES = ["US", "HK", "SH", "SZ"]
# 请求间隔 / 限流退避(免费档频控较严, 按需调大 --delay)
_REQ_DELAY = 0.25
_RATE_SLEEP = 60


def build_symbol(ticker: str, suffix: str) -> str | None:
    """symbol_dict 的 ticker+suffix → EODHD symbol(AAPL.US / 600519.SH / 00700.HK)。"""
    t = str(ticker or "").strip()
    s = str(suffix or "").strip().upper()
    if not t or s not in DEFAULT_SUFFIXES:
        return None
    return f"{t}.{s}"


def fetch_mapping(symbol: str, token: str, sess: requests.Session) -> dict | None:
    """调 EODHD id-mapping, 返回首个结果的 {isin,figi,cusip}, 失败/无数据返回 None。"""
    params = {
        "filter[symbol]": symbol,
        "api_token": token,
        "fmt": "json",
        "page[limit]": 100,
        "page[offset]": 0,
    }
    for attempt in range(3):
        try:
            r = sess.get(BASE, params=params, timeout=20)
        except requests.RequestException as e:
            logger.warning(f"  {symbol} 请求异常: {e}; 重试 {attempt+1}/3")
            time.sleep(2)
            continue
        if r.status_code == 429:
            logger.warning(f"  {symbol} 限流(429), 休眠 {_RATE_SLEEP}s")
            time.sleep(_RATE_SLEEP)
            continue
        if r.status_code != 200:
            logger.warning(f"  {symbol} HTTP {r.status_code}: {(r.text or '')[:120]}")
            return None
        try:
            j = r.json()
        except ValueError:
            logger.warning(f"  {symbol} 响应非 JSON: {(r.text or '')[:120]}")
            return None
        data = j.get("data") if isinstance(j, dict) else None
        if isinstance(data, dict):
            data = [data]
        if not data:
            logger.info(f"  {symbol} 无映射数据")
            return None
        item = data[0]
        return {
            "isin": str(item.get("isin") or "").strip(),
            "figi": str(item.get("figi") or "").strip(),
            "cusip": str(item.get("cusip") or "").strip(),
        }
    return None


async def main():
    ap = argparse.ArgumentParser(description="回填 symbol_dict 的 isin/figi/cusip(EODHD)")
    ap.add_argument("--suffix", nargs="*", default=DEFAULT_SUFFIXES,
                    help="只处理这些 suffix(默认 US HK SH SZ)")
    ap.add_argument("--all", action="store_true", help="强制重填所有行(覆盖已有值)")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 行(测试)")
    ap.add_argument("--dry-run", action="store_true", help="只打印待请求 symbol, 不写库")
    ap.add_argument("--token", default=os.environ.get("EODHD_API_TOKEN"),
                    help="EODHD API token(默认读 EODHD_API_TOKEN)")
    args = ap.parse_args()

    if not args.token:
        logger.error("未配置 EODHD_API_TOKEN: 请写入 config.env 或用 --token 传入")
        return

    suf = [s.upper() for s in args.suffix]
    db = await get_db()
    try:
        where = "suffix IN ({})".format(",".join("?" * len(suf)))
        params = list(suf)
        if not args.all:
            where += (" AND (isin IS NULL OR isin='' OR figi IS NULL OR figi='' "
                      "OR cusip IS NULL OR cusip='')")
        sql = f"SELECT code, ticker, suffix FROM symbol_dict WHERE {where} ORDER BY code"
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"
        cur = await db.execute(sql, tuple(params))
        rows = await cur.fetchall()
        logger.info(f"待处理行: {len(rows)} (suffix={suf}, all={args.all})")

        # symbol -> 该 symbol 下的 code 列表(一次请求回填多条同 symbol 行)
        by_symbol: dict[str, list[str]] = {}
        skipped_suffix = 0
        for r in rows:
            code = str(r[0])
            sym = build_symbol(r[1], r[2])
            if not sym:
                skipped_suffix += 1
                continue
            by_symbol.setdefault(sym, []).append(code)

        logger.info(f"去重后待请求 symbol: {len(by_symbol)} (跳过不支持后缀 {skipped_suffix} 行)")
        if args.dry_run:
            for sym in sorted(by_symbol):
                print(f"  {sym}  ->  {len(by_symbol[sym])} 行")
            return

        sess = requests.Session()
        updated = 0
        failed = 0
        for sym in sorted(by_symbol):
            mp = fetch_mapping(sym, args.token, sess)
            time.sleep(_REQ_DELAY)
            if not mp:
                failed += len(by_symbol[sym])
                continue
            codes = by_symbol[sym]
            for code in codes:
                await db.execute(
                    "UPDATE symbol_dict SET isin=?, figi=?, cusip=? WHERE code=?",
                    (mp["isin"], mp["figi"], mp["cusip"], code))
                updated += 1
            logger.info(f"  {sym}: isin={mp['isin']} figi={mp['figi']} cusip={mp['cusip']} ({len(codes)} 行)")
        logger.info(f"完成: 更新 {updated} 行, 无映射 {failed} 行")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
