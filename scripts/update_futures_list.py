"""从同花顺 fuyao 同步期货/期权品种到 symbol_dict。

与线上行情、开仓表单走同一套数据模型(services/ths_fuyao.varieties_list 取数 +
database.save_symbol_dict 入库), 避免手写 INSERT 与表结构/字段漂移。

字段口径(symbol_dict 品种行):
    code          品种代码(如 CU), 同时作 ticker 裸码
    name          品种名(沪铜)
    sub_assets_type  cn_futures / cn_options
    suffix        交易所后缀(SHF/CFE...), 由 ths exchange_code 映射
    trade_amount  合约乘数(每手数量)
    margin_rate   保证金率(仅填空时回填, 不覆盖已有值)
    data_source   ths

用法:
    python scripts/update_futures_list.py            # 仅期货品种
    python scripts/update_futures_list.py --options  # 期货 + 期权品种
"""
import os
import sys
import asyncio
import logging
import argparse

# 1) 先把 config.env 灌进 os.environ(PG_DSN / THS_API_KEY), 再导入项目模块
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, "config.env"))

# 2) 项目模块(在 load_dotenv 之后导入, 确保能读到 env)
from services import ths_fuyao
from database import save_symbol_dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ths exchange_code(全名) → symbol_dict.suffix(标准后缀)
EXCHANGE_SUFFIX_MAP = {
    "CFFEX": "CFE",
    "CZCE": "CZC",
    "DCE": "DCE",
    "SHFE": "SHF",
    "GFEX": "GFE",
    "INE": "INE",
}

# 品种类别 → 资产小类
KIND_SUB = {"futures": "cn_futures", "options": "cn_options"}


def _map_rows(kind: str, items: list[dict]) -> list[tuple]:
    """ths varieties 原始列表 → save_symbol_dict 的 8 元组列表。"""
    sub = KIND_SUB[kind]
    rows, skipped = [], 0
    for v in items:
        variety_code = str(v.get("variety_code") or "").strip().upper()
        name = str(v.get("name") or "").strip() or variety_code
        ex = str(v.get("exchange_code") or "").strip().upper()
        if not variety_code or not ex:
            logger.warning(f"[{kind}] 跳过缺 variety_code/exchange_code 的记录: {v}")
            skipped += 1
            continue
        suffix = EXCHANGE_SUFFIX_MAP.get(ex)
        if suffix is None:
            logger.warning(f"[{kind}] 跳过未知交易所 {ex} variety={variety_code}")
            skipped += 1
            continue
        trade_amount = v.get("trade_amount") or v.get("contract_multiplier")
        margin_rate = v.get("margin_rate")
        # 8 元组: (code, name, ticker, sub_assets_type, suffix, trade_amount, margin_rate, data_source)
        rows.append((variety_code, name, variety_code, sub, suffix, trade_amount, margin_rate, "ths"))
    if skipped:
        logger.info(f"[{kind}] 跳过 {skipped} 条无效记录")
    return rows


async def _sync_kind(kind: str) -> int:
    items = await ths_fuyao.varieties_list(kind)
    if not items:
        logger.warning(f"[{kind}] varieties_list 返回空, 跳过")
        return 0
    rows = _map_rows(kind, items)
    if not rows:
        return 0
    n = await save_symbol_dict(rows)
    logger.info(f"[{kind}] 同步 {n}/{len(items)} 条到 symbol_dict")
    return n


async def main(include_options: bool) -> None:
    total = await _sync_kind("futures")
    if include_options:
        total += await _sync_kind("options")
    logger.info(f"全部同步完成, 共 {total} 条")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从同花顺 fuyao 同步期货/期权品种到 symbol_dict")
    parser.add_argument("--options", action="store_true", help="同时同步期权品种")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.options))
    except KeyboardInterrupt:
        logger.info("已中断")
