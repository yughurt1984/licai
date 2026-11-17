"""exchange 表查询缓存 + 按 mic 现算各数据源市场码(方案B: 单一真相, 不落 symbol_dict)。

设计: symbol_dict 只存 exchange_mic(外链 exchange.mic); 各源对交易所使用的市场码
(suffix_ths / suffix_futu / ... / suffix_ninjas, 也可能命名为 ths_suffix 等)只在
exchange 表里维护一份。查询时按 mic 从内存缓存取对应源的码, 零 JOIN、零漂移。缓存 300s
刷新, 与 market_routes._suffix_cache 同套路。
"""
import time
from typing import Optional

from database import get_db

# 已知数据源名(用于 resolve_source_code 的合法性校验)
VALID_SOURCES = frozenset({"ths", "futu", "yhfinance", "eodhd", "massive", "ninjas", "yhf"})

_cache = {"data": {}, "ts": 0.0, "ttl": 300.0}


async def _load() -> dict:
    """返回 {mic: {col_name: val, ...}}, 仅含 exchange 表中列名含 'suffix' 的列。

    列名动态发现(information_schema, PRAGMA 是 SQLite 专属 PG 不认), 兼容
    suffix_ths / ths_suffix 等不同命名 —— 不硬编码、不崩列。
    缓存 300s, 与 market_routes._suffix_cache 同套路。"""
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < _cache["ttl"]:
        return _cache["data"]
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ledger' AND table_name='exchange' "
            "ORDER BY ordinal_position")
        cols = [r[0] for r in await cur.fetchall()]
        suffix_cols = [c for c in cols if "suffix" in c.lower() and c.lower() != "mic"]
        if suffix_cols:
            cur = await db.execute(
                f"SELECT mic, {', '.join(suffix_cols)} FROM exchange")
            data = {}
            for r in await cur.fetchall():
                data[str(r[0])] = {suffix_cols[i]: r[i + 1] for i in range(len(suffix_cols))}
        else:
            cur = await db.execute("SELECT mic FROM exchange")
            data = {str(r[0]): {} for r in await cur.fetchall()}
        _cache["data"] = data
        _cache["ts"] = now
        return data
    finally:
        await db.close()


async def resolve_source_code(mic: Optional[str], source: str) -> Optional[str]:
    """返回某交易所(mic)在指定数据源下的市场码; 无映射/未配置/源名非法 → None。

    source(ths/futu/...) 按列名子串匹配, 兼容 suffix_ths 与 ths_suffix 两种命名。
    同名专属列优先: "yhf" 同时是 yhf_suffix 与 suffix_yhfinance(yfapi 与 Yahoo 同一套
    symbol 口径, yhf_suffix 没配时自动回落 yhfinance 列)的子串 —— 两列都配了时,
    源专属列(yhf_suffix)赢, 不靠列顺序碰运气。"""
    if not mic or source not in VALID_SOURCES:
        return None
    data = await _load()
    row = data.get(str(mic))
    if not row:
        return None
    src = source.lower()
    exact = (src, f"suffix_{src}", f"{src}_suffix")
    for col, v in sorted(row.items(),
                         key=lambda kv: 0 if kv[0].lower() in exact else 1):   # 稳定排序, 组内保原序
        if src in col.lower() and v:
            return str(v)
    return None


# 市场段位(symbol_dict.suffix 口径: SH/SZ/HK/BJ) → exchange.mic(唯一确定, 不歧义)。
_SUFFIX_TO_MIC = {
    "SH": "XSHG", "SZ": "XSHE", "HK": "XHKG", "BJ": "BJSE", "JP": "XTKS",
}


async def suffix_to_mic(suffix: Optional[str]) -> Optional[str]:
    """symbol_dict.suffix(市场段位 / 期货后缀) → exchange.mic。

    口径:
      - 权益市场(SH/SZ/HK/BJ)走显式 _SUFFIX_TO_MIC(唯一确定, 不歧义);
      - 其它后缀(期货 SHF/CFE/CZC/DCE/GFE/INE 等 = 交易所码)在 exchange 表按任一
        suffix 列反查, 仅当**唯一**命中一行才返回 mic, 否则 None(歧义, 如 US 美股
        横跨 NASDAQ/NYSE 多家 → 不唯一);
      - 写入值必校验存在于 exchange.mic, 不触发外键、不写脏 mic。
    """
    if not suffix:
        return None
    s = str(suffix).strip().upper()
    mic = _SUFFIX_TO_MIC.get(s)
    if mic:
        data = await _load()
        return mic if mic in data else None
    if s == "US":                      # 美股是跨所市场段位, 不唯一 → 留空(用户既定口径)
        return None
    # 期货等: 后缀本身是交易所码, 反查 exchange 表(任一 suffix 列匹配), 唯一命中才取
    data = await _load()
    hits = set()
    for mic_key, row in data.items():
        for col, v in row.items():
            if v and str(v).strip().upper() == s:
                hits.add(mic_key)
                break
    return next(iter(hits)) if len(hits) == 1 else None


async def active_instrument_sources() -> set:
    """assets_classify 中实际配置的 main/back_instruments_source 集合 —— 决定 exchange 表
    该维护哪些 suffix_* 列(决策 #5: 只填配置了的数据源)。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT main_instruments_source, back_instruments_source FROM assets_classify")
        srcs = set()
        for r in await cur.fetchall():
            for v in (r[0], r[1]):
                if v:
                    srcs.add(str(v))
        return srcs
    finally:
        await db.close()
