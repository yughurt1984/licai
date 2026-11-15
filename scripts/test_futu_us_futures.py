"""US 期货 futu 数据源联调脚本(先测试, 再落库)。

用法:
  python scripts/test_futu_us_futures.py ES 2024-12            # 只看 futu 返回的字段
  python scripts/test_futu_us_futures.py ES 2024-12 --write    # 额外写入 symbol_dict

前置: 在设置页或环境变量配好 FUTU_APP_KEY / FUTU_PRIVATE_KEY(futu API Key 签名)。
脚本会打印: ① 拼出的 CME 合约码  ② futu 静态信息(原始 + 解析)  ③ futu 最新报价(snapshot)。
若静态信息为空, 多半是 futu 期货需要 market/stock_type 参数 —— 看下面 [raw] 段的实际返回。
"""
import asyncio
import sys

sys.path.insert(0, r"E:\projects\licai")

from services.instruments import build_us_futures_contract_code, futu_us_futures_instrument
from services import futu_quote
from services import futu_auth
import requests as _requests
from database import save_symbol_dict


async def _raw_basicinfo(contract: str) -> None:
    """直接打 futu stock-basicinfo(带 market/stock_type), 打印原始 JSON —— 用于确认期货字段。"""
    cfg = await futu_auth.futu_config()
    if not (cfg.get("app_key") and cfg.get("private_key")):
        print("  (未配置 futu 凭证, 跳过 raw 探测)")
        return
    auth = await futu_auth.auth_for("POST", "/api/v1.0/quote/stock-basicinfo")
    if auth[0] is None:
        print("  auth:", auth[1])
        return
    futu_code = f"US.{contract}"
    for extra in ({"market": "US", "stock_type": "FUTURE"}, {}):
        body = {"code_list": [futu_code], **extra}
        import json
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if auth[0] == "oauth":
            headers = auth[1]
        else:
            headers = futu_auth.build_auth_headers("POST", "/api/v1.0/quote/stock-basicinfo",
                                                  "", raw, auth[1], auth[2], auth[3])
        try:
            r = _requests.post(cfg["host"] + "/api/v1.0/quote/stock-basicinfo",
                               data=raw, headers=headers, timeout=10)
            print(f"  [raw] body={body} -> HTTP {r.status_code}: {r.text[:400]}")
        except Exception as e:
            print(f"  [raw] 请求失败: {e}")


async def main():
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "ES").upper()
    ym = sys.argv[2] if len(sys.argv) > 2 else "2024-12"
    write = "--write" in sys.argv
    try:
        contract = build_us_futures_contract_code(ticker, ym)
    except Exception as e:
        print("合约码拼装失败:", e)
        return
    print(f"[1] ticker={ticker} ym={ym} -> contract={contract}  futu=US.{contract}")

    print("\n[2] futu 静态信息(解析后):")
    row = await futu_us_futures_instrument(ticker, ym)
    if not row:
        print("  未命中(未配置/合约不存在/无名称)")
    else:
        for k, v in row.items():
            print(f"    {k}: {v}")
    print("\n[2b] futu 静态信息(raw, 带 market/stock_type 探测):")
    await _raw_basicinfo(contract)

    print("\n[3] futu 最新报价(snapshot_rows):")
    q = await futu_quote.snapshot_rows([f"{contract}.US"])
    if not q:
        print("  无报价; last_error =", futu_quote.last_error())
    else:
        for it in q:
            print("   ", it.model_dump() if hasattr(it, "model_dump") else it)

    if write and row:
        await save_symbol_dict([(
            row["code"], row["name"], row["ticker"], row["sub_assets_type"],
            row["suffix"], None, None, row["source"], None, None, row.get("exchange_mic"))])
        print(f"\n[4] 已写入 symbol_dict: {row['code']} (trade_amount/margin_rate 留空, 后续补全)")


asyncio.run(main())
print("done")
