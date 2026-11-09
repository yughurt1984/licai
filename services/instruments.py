"""标的信息源(底稿补全): symbol_dict 未命中的 ticker, 按小类路由 instruments 源获取。

与报价路由同一张表: assets_classify 的 main_instruments_source / back_instruments_source
(人工维护, 查表获取, 60s 缓存)。源实现:
  tickflow —— GET /v1/instruments(股/ETF/港美股; 代码.市场与标准码同构, 零转换);
  ths      —— 期货/期权: varieties/list(品种级, 含 trade_amount/contract_multiplier)
              + tickers/search(合约级名称); 用户录入合约码(CU2601.SHF)时按品种前缀归属。

流程: 未知 ticker → main → back 逐源尝试 → 命中返回
{code, name, ticker, suffix, sub_assets_type, trade_amount?} → 调用方先回显前端,
再落库 symbol_dict(与行情 write-through 同模式)。
防污染: 上游明确未命中不返回不落库; 失败负缓存 60s —— 用户连敲错码不会反复打 API 配额。
"""
from __future__ import annotations

import asyncio

import re
import time

_NEG_TTL = 60.0
_neg: dict[str, float] = {}          # code → 负缓存时刻
_src_cache: list = [None, 0.0]       # ({sub: (main, back)}, ts)


def invalidate_instruments_cache():
    """assets_classify instruments 源改动后清缓存(60s TTL 之外的主动失效位)。"""
    _src_cache[0] = None
    _src_cache[1] = 0.0
    _neg.clear()


async def _sources_for(sub_assets_type: str) -> tuple:
    """小类 → (main, back) instruments 源名(小写)。读 assets_classify, 缓存 60s。"""
    if _src_cache[0] is not None and time.time() - _src_cache[1] < 60:
        return _src_cache[0].get((sub_assets_type or "").strip(), ("", ""))
    out: dict[str, tuple] = {}
    try:
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT sub_assets_type, main_instruments_source, back_instruments_source "
                "FROM assets_classify")
            for r in await cur.fetchall():
                sub = str(r[0] or "").strip()
                main = str(r[1] or "").strip().lower()
                back = str(r[2] or "").strip().lower()
                if sub and (main or back):
                    out[sub] = (main, back)
        finally:
            await db.close()
    except Exception as e:
        print(f"[instruments] assets_classify 标的信息源读取失败: {e}")
    _src_cache[0], _src_cache[1] = out, time.time()
    return out.get((sub_assets_type or "").strip(), ("", ""))


def _variety_letters(code: str) -> str:
    """期货/期权码的品种前缀: CU2601.SHF → CU; IO2512.CFE → IO; CU → CU。"""
    body = (code or "").strip().upper().split(".")[0]
    m = re.match(r"^[A-Za-z]+", body)
    return m.group(0).upper() if m else ""


async def _tf_fetch(sub_assets_type: str, code: str) -> dict | None:
    """tickflow 标的元数据(股/ETF/港美股)。name 缺失按未命中。"""
    from services import tickflow
    if not await tickflow._api_key() and "free-api" not in await tickflow._host():
        raise RuntimeError("tickflow_api_key 未配置(设置页或 TICKFLOW_API_KEY)")
    items = await tickflow.instruments([code]) or []
    it = next((x for x in items if x.get("code") == code), None)
    if not it or not it.get("name"):
        return None
    return {
        "code": code,
        "name": it["name"],
        "ticker": it.get("ticker") or code.split(".")[0],
        "suffix": it.get("exchange") or code.rsplit(".", 1)[-1],
        "sub_assets_type": sub_assets_type,
        "source": "tickflow",
    }


# 交易所原始码 → 市场段位(symbol_dict.suffix 口径): SEHK=港交所, 美所归 US
_EXCHANGE_STD = {"SEHK": "HK", "NASDAQ": "US", "NYSE": "US", "AMEX": "US", "SSE": "SH", "SZSE": "SZ"}


_FUTU_BASICINFO_PATH = "/api/v1.0/quote/stock-basicinfo"


async def _futu_basicinfo(futu_codes: list[str]) -> Optional[list[dict]]:
    """futu 批量静态信息(POST /api/v1.0/quote/stock-basicinfo, code_list ≤400 自动分块)。

    futu_codes 为 futu 码(HK.06186)。返回 [{code, name, sc_name, ticker, exchange_raw,
    lot_size, stock_type, suspension}]; 鉴权/网络失败抛 RuntimeError(调用方转逐源状态)。
    """
    import json as _json
    from services import futu_auth
    clean = [str(c or "").strip().upper() for c in futu_codes or [] if str(c or "").strip()]
    if not clean:
        return None
    cfg = await futu_auth.futu_config()
    auth = await futu_auth.auth_for("POST", _FUTU_BASICINFO_PATH)
    if auth[0] is None:
        raise RuntimeError(auth[1])
    out: list[dict] = []
    for i in range(0, len(clean), 400):
        chunk = clean[i:i + 400]
        body = _json.dumps({"code_list": chunk}, separators=(",", ":")).encode("utf-8")
        headers = auth[1] if auth[0] == "oauth"             else futu_auth.build_auth_headers("POST", _FUTU_BASICINFO_PATH, "", body,
                                              auth[1], auth[2], auth[3])
        r = await asyncio.to_thread(
            lambda b=body, h=headers: futu_auth._session.post(
                cfg["host"] + _FUTU_BASICINFO_PATH, data=b, headers=h, timeout=10))
        try:
            j = r.json()
        except Exception:
            raise RuntimeError(f"HTTP {r.status_code}: {(r.text or '')[:120]}")
        if j.get("ret_code") != 0:
            raise RuntimeError(f"ret_code={j.get('ret_code')}: {str(j.get('ret_msg', ''))[:120]}")
        for it in ((j.get("data") or {}).get("basic_list")) or []:
            try:
                out.append({
                    "code": str(it.get("code") or "").upper(),
                    "name": str(it.get("name") or "").strip(),
                    "sc_name": str(it.get("sc_name") or "").strip(),
                    "ticker": str(it.get("code") or "").split(".")[-1],
                    "exchange_raw": str(it.get("exchange") or "").upper(),
                    "lot_size": it.get("lot_size"),
                    "stock_type": str(it.get("stock_type") or "").upper(),
                    "suspension": bool(it.get("suspension")),
                })
            except Exception:
                continue
    return out or None


async def _futu_fetch(sub_assets_type: str, code: str) -> dict | None:
    """futu 静态信息(quote/stock-basicinfo): 标的元数据(市场由 code_suffix 决定)。

    code 为标准码(06186.HK / 7203.JP) → 请求换 futu 码(HK.06186 / JP.7203) → 字段映射落底稿。
    """
    if "." not in code:
        return None
    body, mkt = code.rsplit(".", 1)
    mkt = mkt.upper()
    if mkt not in ("HK", "US", "JP", "SH", "SZ"):
        return None
    if mkt == "HK" and body.isdigit():
        body = body.zfill(5)
    futu_code = f"{mkt}.{body}"
    items = await _futu_basicinfo([futu_code]) or []
    it = next((x for x in items if x.get("code") == futu_code), None)
    if not it or not (it.get("sc_name") or it.get("name")):
        return None
    raw_ex = it.get("exchange_raw") or ""
    return {
        "code": code,                                        # futu 码 HK.06186 → 标准码 06186.HK
        "name": it.get("sc_name") or it.get("name"),         # 简体中文名
        "ticker": body,                                      # 去市场段
        "suffix": _EXCHANGE_STD.get(raw_ex, raw_ex),       # SEHK → HK
        "sub_assets_type": sub_assets_type,                  # 用户所选小类
        "source": "futu",
    }


_SUB_TO_ASSET_TYPE = {   # 小类 → fuyao ticker-search 的 asset_type 过滤
    "a_comm_stock": "a-share", "cn_etf_fund": "fund-etf", "cn_lof_fund": "fund-lof",
    "cn_otc_fund": "fund-otc", "cn_futures": "futures", "cn_options": "options",
}


async def _ths_fetch(sub_assets_type: str, code: str) -> dict | None:
    """ths 标的检索(/api/meta/tickers/search): 覆盖 A股/ETF/期货/期权(港股不在其范围)。

    q 直接用标准码(001298.SZ / CU2601.SHF —— 与 thscode 同构), 精确匹配 thscode。
    期货/期权: 合约命中后用品种参数(trade_amount/margin_rate)回填; 合约未命中退品种行
    (CU → 沪铜)。命中行 source='ths'。
    """
    from services import ths_fuyao
    atype = _SUB_TO_ASSET_TYPE.get(sub_assets_type, "")
    items = await ths_fuyao.contracts_search(code, asset_type=atype) or []
    it = next((x for x in items
               if str(x.get("thscode") or "").upper() == code.upper()), None)
    if it:
        row = {
            "code": str(it.get("thscode") or "").upper(),
            "name": str(it.get("name") or "").strip(),
            "ticker": str(it.get("ticker") or "") or code.split(".")[0],
            "suffix": str(it.get("exchange") or "").upper() or code.rsplit(".", 1)[-1],
            "sub_assets_type": sub_assets_type,
            "source": "ths",
        }
        # 期货/期权: 品种参数回填(trade_amount/margin_rate 是品种级)
        if sub_assets_type in ("cn_futures", "cn_options"):
            letters = _variety_letters(code)
            kind = "options" if sub_assets_type == "cn_options" else "futures"
            v = next((x for x in await ths_fuyao.varieties_list(kind)
                      if str(x.get("variety_code") or "").upper() == letters), None)
            if v:
                row["trade_amount"] = v.get("trade_amount") or v.get("contract_multiplier")
                row["margin_rate"] = v.get("margin_rate")
        return row
    # 合约未命中 → 品种行兜底(仅期货/期权: CU → 沪铜)
    if sub_assets_type in ("cn_futures", "cn_options"):
        return await _ths_variety_row(sub_assets_type, code)
    return None


async def _ths_variety_row(sub_assets_type: str, code: str) -> dict | None:
    """品种行兜底: 期货/期权合约未命中检索时, 按品种前缀取品种行(CU → 沪铜)。"""
    from services import ths_fuyao
    kind = "options" if sub_assets_type == "cn_options" else "futures"
    letters = _variety_letters(code)
    if not letters:
        return None
    v = next((x for x in await ths_fuyao.varieties_list(kind)
              if str(x.get("variety_code") or "").upper() == letters), None)
    if not v:
        return None
    body = code.split(".")[0].upper()
    ticker = body if body.isalpha() else letters
    return {
        "code": ticker,
        "name": str(v.get("name") or letters),
        "ticker": ticker,
        "suffix": str(v.get("exchange_code") or "").upper(),
        "sub_assets_type": sub_assets_type,
        "trade_amount": v.get("trade_amount") or v.get("contract_multiplier"),
        "margin_rate": v.get("margin_rate"),
        "source": "ths",
    }


async def fetch_instrument(sub_assets_type: str, code: str) -> dict | None:
    """未知 ticker → 按小类的 instruments 源链(main → back)获取标的信息。

    命中返回 {code, name, ticker, suffix, sub_assets_type, trade_amount?};
    全部源未命中返回 None(并进负缓存)。best-effort: 不抛异常。
    """
    row, _ = await fetch_instrument_diag(sub_assets_type, code)
    return row


async def fetch_instrument_diag(sub_assets_type: str, code: str) -> tuple:
    """同 fetch_instrument, 另带逐源状态 [(源名, 结果说明)] —— 前端能看到"为什么没拿到"。"""
    trace: list[tuple] = []
    code = (code or "").strip().upper()
    sub_assets_type = (sub_assets_type or "").strip()
    if not code or not sub_assets_type:
        return None
    key = f"{sub_assets_type}:{code}"
    if time.time() - _neg.get(key, 0) < _NEG_TTL:
        return None, [("negative_cache", "60s 内失败过, 短路")]
    main, back = await _sources_for(sub_assets_type)
    if not (main or back):
        trace.append(("(未配置)", "assets_classify 该小类没配 instruments 源"))
    for name in (main, back):
        if not name:
            continue
        # 调用时按名解析(读模块全局): 便于测试替换/未来加源不改分发处
        impl = (_tf_fetch if name == "tickflow"
                else _ths_fetch if name == "ths"
                else _futu_fetch if name == "futu" else None)
        if impl is None:
            trace.append((name, "没有实现的源适配器"))
            continue
        try:
            row = await impl(sub_assets_type, code)
            status = "命中" if (row and row.get("name")) else "未命中"
        except Exception as e:
            row, status = None, f"失败: {str(e)[:100]}"
        trace.append((name, status))
        print(f"[instruments] {code} @ {name}: {status}")
        if row and row.get("name"):
            return row, trace
    _neg[key] = time.time()
    if len(_neg) > 500:
        for k in list(_neg)[:200]:
            _neg.pop(k, None)
    return None, trace
