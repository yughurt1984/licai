"""从同花顺 fuyao 同步期权品种到 symbol_dict(仅 cn_options)。

与 update_futures_list.py 同源数据模型(services/ths_fuyao.varieties_list(kind='options') 取数 +
database.save_symbol_dict 入库), 但额外补期权交割标的字段:
    delivery_code   由 variety_code 去尾 'O' 派生(如 RBO → RB, 对应螺纹钢期货品种代码)
    delivery_name   按 delivery_code 在 symbol_dict 匹配底层品种名(故建议先跑 update_futures_list.py
                   把期货品种灌入, 否则匹配不到时回落到期权自身 name 并告警)

字段口径(symbol_dict 期权品种行):
    code          品种代码(如 RBO), 同时作 ticker 裸码
    name          品种名(螺纹钢期权)
    sub_assets_type  cn_options
    suffix        交易所后缀(SHF/CFE...), 由 ths exchange_code 映射
    trade_amount  合约乘数(每手数量, 期权同期货口径)
    margin_rate   保证金率(仅填空时回填, 不覆盖已有值)
    delivery_code 交割标的代码(去尾 O, 如 RBO→RB)
    delivery_name 交割标的中文名(按 delivery_code 在 symbol_dict 匹配)
    data_source   ths

用法:
    python scripts/update_options_list.py            # 同步期权品种(交割名按现有 symbol_dict 匹配)
    python scripts/update_options_list.py --force-name  # 匹配不到交割名时也用期权名兜底写入 delivery_name
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
from database import save_symbol_dict, load_symbol_dict

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
    "SSE": "SH",
    "SZSE": "SZ",
}

# 股指/ETF 期权: 去尾 O 得不到真实标的(IO→I 而非 IF), 用显式覆盖表(按需补充, 以 ths 实际 code 为准)
DELIVERY_OVERRIDE = {
    # "IO": "IF",   # 沪深300股指期权 → 沪深300期货(范例, 取消注释并按 ths 实际品种代码校准)
    # "HO": "IH",   # 上证50股指期权  → 上证50期货
    # "MO": "IC",   # 中证1000股指期权 → 中证1000期货
}


def _derive_delivery(variety_code: str) -> str:
    """品种代码 → 交割标的代码。先查覆盖表, 否则默认去尾 'O'(RBO→RB)。"""
    code = variety_code.upper()
    return DELIVERY_OVERRIDE.get(code) or (code[:-1] if code.endswith("O") else code)


def _map_rows(items: list[dict], name_by_code: dict, force_name: bool) -> list[tuple]:
    """ths options varieties 原始列表 → save_symbol_dict 的 10 元组列表。"""
    rows, skipped = [], 0
    for v in items:
        variety_code = str(v.get("variety_code") or "").strip().upper()
        name = str(v.get("name") or "").strip() or variety_code
        ex = str(v.get("exchange_code") or "").strip().upper()
        if not variety_code or not ex:
            logger.warning(f"跳过缺 variety_code/exchange_code 的记录: {v}")
            skipped += 1
            continue
        suffix = EXCHANGE_SUFFIX_MAP.get(ex)
        if suffix is None:
            logger.warning(f"跳过未知交易所 {ex} variety={variety_code}")
            skipped += 1
            continue
        trade_amount = v.get("trade_amount") or v.get("contract_multiplier")
        margin_rate = v.get("margin_rate")
        delivery_code = _derive_delivery(variety_code)
        delivery_name = name_by_code.get(delivery_code)
        if delivery_name is None:
            if force_name:
                delivery_name = name
            else:
                logger.warning(
                    f"{variety_code}: 交割标的 {delivery_code} 未在 symbol_dict 匹配到名称"
                    f"(先跑 update_futures_list.py 灌期货品种, 或用 --force-name 兜底)")
        # 10 元组: code, name, ticker, sub, suffix, trade_amount, margin_rate, data_source, delivery_code, delivery_name
        rows.append((variety_code, name, variety_code, "cn_options", suffix,
                     trade_amount, margin_rate, "ths", delivery_code, delivery_name))
    if skipped:
        logger.info(f"跳过 {skipped} 条无效记录")
    return rows


async def main(force_name: bool) -> None:
    # 先载入 symbol_dict, 用于交割标的名称匹配
    sym_rows, _ = await load_symbol_dict()
    name_by_code = {str(c).upper(): n for (c, n, *_rest) in sym_rows}
    items = await ths_fuyao.varieties_list("options")
    if not items:
        logger.warning("options varieties_list 返回空, 跳过")
        return
    rows = _map_rows(items, name_by_code, force_name)
    if not rows:
        logger.info("无可写入的期权品种")
        return
    n = await save_symbol_dict(rows)
    logger.info(f"同步 {n}/{len(items)} 条 cn_options 到 symbol_dict")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从同花顺 fuyao 同步期权品种到 symbol_dict")
    parser.add_argument("--force-name", action="store_true",
                        help="交割标的名匹配不到时, 用期权自身 name 兜底写入 delivery_name")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.force_name))
    except KeyboardInterrupt:
        logger.info("已中断")
