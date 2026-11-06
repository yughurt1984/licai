"""富途 OpenAPI REST 行情快照(港/美股): https://open.futunn.com/zh-cn/api/overview/getting-started

- REST 直连 webapi.futunn.com, 不再依赖本地 FutuOpenD 网关与 futu-api SDK:
  POST /api/v1.0/quote/snapshot, body {"code_list": [...]}, 单次 ≤400 只;
  快照字段 last_price/open_price/high_price/low_price/prev_close_price/volume/turnover,
  update_time 为毫秒时间戳(转北京时间落库)。
- 认证(入门文档「方式二: 传统 API Key」, 适合服务端定时任务): 请求头
  X-Api-Key / X-Timestamp(毫秒) / X-Nonce / Authorization = base64(签名)。
  签名原文 5 段以 \n 连接: {timestamp_ms}\n{METHOD}\n{request_path}\n{query_string}\n{body_part},
  body_part = 原始 body 字节的 SHA256 小写十六进制(无 body 为空串); 签名算法
  Ed25519 或 RSA-SHA256(PKCS#1 v1.5), 与 dashboard 创建 AppKey 时上传公钥所选一致。
  凭证在 https://open.futunn.com/dashboard 创建: AppKey + 本地私钥(PEM 文本, 绝不入库/日志)。
- 凭证来源(优先级): app_config(futu_app_key/futu_private_key/futu_sign_algo/futu_api_host)
  → .env/env(FUTU_APP_KEY/FUTU_PRIVATE_KEY/FUTU_SIGN_ALGO/FUTU_API_HOST) → config.py 默认。
- 代码转换收在本模块: 项目标准码是「代码.市场」(00700.HK / SNDK.US), futu 是「市场.代码」
  (HK.00700)。请求前用 symbol_dict 的 exchange+ticker 拼接成 futu 码(查不到段交换兜底),
  响应转回标准码 —— futu 码绝不写数据库(见 _futu_symbol_map/_std_code)。
- 所有请求 best-effort: 未配置/私钥不对/网络错误 → 返回 [], 不连累持仓页;
  -12011(限流)进 60s 冷却, 期间直接空返回不再打 API。
- 访问策略(与 ths/fuyao 一致): 只有手动刷新(force)和新增持仓冷启动(quote_latest 无行)
  才联网, 其余一律读 PG(market.quote_latest)。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests as _requests

from config import config
from services.quote_source import QuoteRow  # 规范层模型(仅依赖 pydantic, 无循环导入)

_DEFAULT_HOST = "https://webapi.futunn.com"
_SNAPSHOT_PATH = "/api/v1.0/quote/snapshot"
_SERVER_TIME_PATH = "/api/v1.0/server-time"
_BATCH_LIMIT = 400
_COOLDOWN_ON_RATE_LIMIT = 60.0

_session = _requests.Session()
_session.trust_env = False          # 不吃系统代理(与 ths_fuyao 同模式)

_cfg_cache: list = [None, 0.0]      # (cfg dict, ts); 60s TTL, 设置页保存后 invalidate
_last_error: str = ""
_cooldown_until: float = 0.0        # 限流冷却截止(monotonic 基准用 time.time 即可)


def _resolve_cfg_from_env() -> dict:
    return {
        "host": (os.environ.get("FUTU_API_HOST", "") or getattr(config, "futu_api_host", "") or _DEFAULT_HOST).rstrip("/"),
        "app_key": (os.environ.get("FUTU_APP_KEY", "") or getattr(config, "futu_app_key", "") or "").strip(),
        "private_key": (os.environ.get("FUTU_PRIVATE_KEY", "") or getattr(config, "futu_private_key", "") or "").strip(),
        "algo": (os.environ.get("FUTU_SIGN_ALGO", "") or getattr(config, "futu_sign_algo", "") or "ed25519").strip().lower(),
    }


async def futu_config() -> dict:
    """凭证配置(异步): 优先 app_config(设置页), 其次 .env/config。缓存 60s。"""
    global _cfg_cache
    if _cfg_cache[0] is not None and time.time() - _cfg_cache[1] < 60:
        return _cfg_cache[0]
    cfg = _resolve_cfg_from_env()
    try:
        from database import get_config
        host = ((await get_config("futu_api_host")) or "").strip()
        app_key = ((await get_config("futu_app_key")) or "").strip()
        private_key = ((await get_config("futu_private_key")) or "").strip()
        algo = ((await get_config("futu_sign_algo")) or "").strip().lower()
        if host:
            cfg["host"] = host.rstrip("/")
        if app_key:
            cfg["app_key"] = app_key
        if private_key:
            cfg["private_key"] = private_key
        if algo:
            cfg["algo"] = algo
    except Exception:
        pass
    _cfg_cache[0], _cfg_cache[1] = cfg, time.time()
    return cfg


def invalidate_ctx():
    """设置页改了凭证后清配置缓存(函数名沿用旧 OpenD 时代, 语义=重建上下文)。"""
    global _cfg_cache, _cooldown_until
    _cfg_cache = [None, 0.0]
    _cooldown_until = 0.0


def last_error() -> str:
    return _last_error


# ── 签名(入门文档「方式二」) ──────────────────────────────────────────────
def _load_private_key(pem: str):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    return load_pem_private_key(pem.encode("utf-8"), password=None)


def _sign_message(key, algo: str, message: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if isinstance(key, Ed25519PrivateKey):
        return key.sign(message)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA256())


def build_auth_headers(method: str, path: str, query: str, body: bytes,
                       app_key: str, private_key: str, algo: str) -> dict:
    """按富途 API Key 规范构造鉴权头。签名原文: ts\\nMETHOD\\npath\\nquery\\nsha256hex(body)"""
    ts_ms = str(int(time.time() * 1000))
    body_part = hashlib.sha256(body or b"").hexdigest() if body else ""
    raw = "\n".join([ts_ms, method.upper(), path, query or "", body_part])
    key = _load_private_key(private_key)
    sig = base64.b64encode(_sign_message(key, algo, raw.encode("utf-8"))).decode("ascii")
    return {
        "X-Api-Key": app_key,
        "X-Timestamp": ts_ms,
        "X-Nonce": uuid.uuid4().hex,
        "Authorization": sig,
        "Content-Type": "application/json",
    }


# ── 分红派息事件(corporate-actions/dividends) ─────────────────────────────
def _futu_date(v) -> str | None:
    """富途日期 YYYY/MM/DD → ISO(YYYY-MM-DD); 空返回 None。"""
    import datetime as _dt
    try:
        return _dt.datetime.strptime(str(v or ""), "%Y/%m/%d").date().isoformat()
    except ValueError:
        return None


async def dividends_sync(futu_code: str) -> Optional[list[dict]]:
    """富途分红派息事件(futu_code 前缀形态 HK.00700 / US.AAPL):
    [{ex_date, pay_date, per_share, currency, declared, source=futu}], 除净日降序。
    只取 process=Implementation(已实施), 预案不要。有凭证走签名请求, 没凭证试匿名
    (文档示例免鉴权)。拉不到 → None。"""
    cfg = await futu_config()
    path = f"/api/v1.0/quote/{futu_code}/corporate-actions/dividends"
    headers = {}
    if cfg.get("app_key") and cfg.get("private_key"):
        try:
            headers = build_auth_headers("GET", path, "", b"",
                                         cfg["app_key"], cfg["private_key"], cfg.get("algo") or "ed25519")
        except Exception:
            headers = {}                      # 签名失败按匿名试(文档示例免鉴权)

    def _req() -> Optional[list]:
        r = _session.get((cfg.get("host") or _DEFAULT_HOST) + path,
                         headers=headers or None, timeout=10)
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("ret_code") != 0:
            return None
        return (j.get("data") or {}).get("dividend_list") or []

    rows = await asyncio.to_thread(_req)
    if rows is None:
        return None
    out = []
    for row in rows:
        if (row.get("process") or "") != "Implementation":
            continue                          # 预案(Plan)不要
        ex = _futu_date(row.get("ex_date"))
        if not ex:
            continue
        try:
            ps = float(row.get("dividend_per_share") or 0)
        except (TypeError, ValueError):
            continue
        if ps <= 0:
            continue
        out.append({"ex_date": ex, "pay_date": _futu_date(row.get("dividend_payable_date")),
                    "per_share": ps, "currency": str(row.get("currency") or ""),
                    "declared": str(row.get("statement") or ""), "source": "futu"})
    return out


# ── 代码转换(futu 专用, 绝不落库) ─────────────────────────────────────────
# 标准码是「代码.市场」(00700.HK / SNDK.US, 与 symbol_dict/ths/tickflow 同构); futu 是
# 「市场.代码」(HK.00700)。转换只发生在本模块内: 请求前把标准码翻成 futu 码, 响应转回
# 标准码后才进 QuoteRow/落库链 —— futu 码任何形态都不写数据库。
_FUTU_MARKETS = ("HK", "US", "SH", "SZ")           # symbol_dict.exchange 允许拼接的市场段位


def _std_code(futu_code: str) -> str:
    """futu 码(HK.00700 / US.SNDK) → 标准码(00700.HK / SNDK.US)。futu 第一段是市场, 段交换, 兜底原样。"""
    c = (futu_code or "").strip().upper()
    if "." in c:
        mkt, body = c.split(".", 1)
        if mkt == "HK" and body.isdigit():
            body = body.zfill(5)                   # futu 有时不带前导零(如 HK.700)
        if mkt in ("HK", "US") and body:
            return f"{body}.{mkt}"
    return c


def _to_futu_code(code: str) -> str:
    """标准码(00700.HK) → futu 码(HK.00700)。段交换兜底; symbol_dict 拼接优先(见 _futu_symbol_map)。"""
    c = (code or "").strip().upper()
    if "." in c:
        body, mkt = c.split(".", 1)
        if mkt == "HK" and body.isdigit():
            body = body.zfill(5)
        if mkt in ("HK", "US") and body:
            return f"{mkt}.{body}"
    return c


async def _futu_symbol_map(codes: list[str]) -> dict[str, str]:
    """标准码 → futu 码。口径: symbol_dict 的 exchange+ticker 拼接(00700.HK →
    exchange='HK' + ticker='00700' → HK.00700); 底稿查不到/段位不可信的码走段交换兜底。"""
    std = sorted({str(x or "").strip().upper() for x in codes} - {""})
    if not std:
        return {}
    out: dict[str, str] = {}
    try:
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT code, ticker, exchange FROM symbol_dict WHERE UPPER(code) = ANY(?)", (std,))
            for code, ticker, ex in await cur.fetchall():
                c = str(code or "").strip().upper()
                ticker = str(ticker or "").strip().upper()
                ex = str(ex or "").strip().upper()
                if c and ticker and ex in _FUTU_MARKETS:
                    out[c] = f"{ex}.{ticker}"
        finally:
            await db.close()
    except Exception as e:
        print(f"[futu] symbol_dict 码表查询失败(段交换兜底): {e}")
    for c in std:
        out.setdefault(c, _to_futu_code(c))        # 没登记/登记不可信 → 段交换
    return out


# ── 快照 ──────────────────────────────────────────────────────────────────
def _snapshot_sync(codes: list[str], cfg: dict, sym_map: dict[str, str] | None = None) -> Optional[list[dict]]:
    """批量快照(≤400/次, 自动分块)。失败返回 None(错误记 _last_error)。

    codes 是标准码(00700.HK); 请求体里换成 futu 码(sym_map), 响应码转回标准码再返回。
    """
    global _last_error, _cooldown_until
    if not cfg.get("app_key") or not cfg.get("private_key"):
        _last_error = "未配置 futu AppKey/私钥(设置页或 .env)"
        return None
    if time.time() < _cooldown_until:
        _last_error = "限流冷却中, 稍后再试"
        return None
    try:
        key = _load_private_key(cfg["private_key"])
    except Exception as e:
        _last_error = f"私钥加载失败: {str(e)[:120]}"
        return None
    sym_map = sym_map or {}

    out: list[dict] = []
    for i in range(0, len(codes), _BATCH_LIMIT):
        chunk = codes[i:i + _BATCH_LIMIT]
        body = json.dumps({"code_list": [sym_map.get(c, _to_futu_code(c)) for c in chunk]},
                          separators=(",", ":")).encode("utf-8")
        try:
            r = _session.post(cfg["host"] + _SNAPSHOT_PATH, data=body,
                              headers=build_auth_headers(
                                  "POST", _SNAPSHOT_PATH, "", body,
                                  cfg["app_key"], cfg["private_key"], cfg["algo"]),
                              timeout=10)
            j = r.json()
        except Exception as e:
            _last_error = str(e)[:160]
            return None
        ret = j.get("ret_code")
        if ret != 0:
            _last_error = f"ret_code={ret}: {j.get('ret_msg', '')[:120]}"
            if ret == -12011:                       # 请求被限流
                _cooldown_until = time.time() + _COOLDOWN_ON_RATE_LIMIT
            return None
        rows = ((j.get("data") or {}).get("snapshot_list")) or []
        for it in rows:
            try:
                last = float(it.get("last_price") or 0)
                prev = float(it.get("prev_close_price") or 0)
                code = _std_code(it.get("code"))     # futu 码(HK.00700) → 标准码(00700.HK)
                if not last or not code:
                    continue
                out.append({
                    "code": code,
                    "name": str(it.get("sc_name") or it.get("tc_name") or it.get("name") or ""),
                    "price": last,
                    "prev_close": prev or None,
                    "change_pct": round((last - prev) / prev * 100, 3) if prev else None,
                    "open": float(it.get("open_price") or 0) or None,
                    "high": float(it.get("high_price") or 0) or None,
                    "low": float(it.get("low_price") or 0) or None,
                    "volume": float(it.get("volume") or 0),
                    "turnover": float(it.get("turnover") or 0),
                    "update_time": int(it.get("update_time") or 0),
                    "currency": "HKD" if code.endswith(".HK") else "USD",
                })
            except Exception:
                continue
    _last_error = ""
    return out or None


async def futu_probe() -> dict:
    """探活 + 配置状态(设置页用)。server-time 未配置凭证也能打(连通性 vs 配置分开展示)。"""
    cfg = await futu_config()
    configured = bool(cfg.get("app_key") and cfg.get("private_key"))
    note, ok = "", None
    try:
        r = _session.get(cfg["host"] + _SERVER_TIME_PATH, timeout=6)
        j = r.json()
        if j.get("server_time_ms"):
            ok, note = True, "接口可达"
        elif j.get("ret_code") == 0:
            ok, note = True, "接口可达"
        else:
            ok, note = None, f"ret_code={j.get('ret_code')}: {str(j.get('ret_msg', ''))[:80]}"
    except Exception as e:
        ok, note = False, str(e)[:120]
    return {"configured": configured, "valid": ok, "note": note,
            "host": cfg["host"], "algo": cfg["algo"],
            "app_key_masked": (cfg["app_key"][:4] + "****" + cfg["app_key"][-2:]) if len(cfg.get("app_key") or "") > 6 else ("****" if cfg.get("app_key") else ""),
            "last_error": _last_error}


def _quote_time_bj(update_time_ms: int) -> str:
    """futu update_time 毫秒时间戳 → 北京时间 'YYYY-MM-DD HH:MM:SS'。"""
    if not update_time_ms:
        return ""
    try:
        dt = datetime.fromtimestamp(int(update_time_ms) / 1000, tz=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


async def snapshot_rows(codes: list[str]) -> list[QuoteRow]:
    """futu 源适配器: 标准码(00700.HK / SNDK.US) → 规范 QuoteRow。

    代码转换收在本模块: 请求前经 _futu_symbol_map(symbol_dict exchange+ticker 拼接,
    段交换兜底)翻成 futu 码, 响应码转回标准码 —— futu 码绝不落库。
    契约: 不抛异常 —— 未配置/私钥不对/OpenAPI 报错/部分码无数据, 都表现为该码不在返回里,
    派发层据此把缺码交给链上的下一个源(新浪 legacy)。
    """
    clean = [str(c or "").strip().upper() for c in codes if str(c or "").strip()]
    if not clean:
        return []
    cfg = await futu_config()
    sym_map = await _futu_symbol_map(clean)
    items = await asyncio.to_thread(_snapshot_sync, clean, cfg, sym_map)   # 内部吞异常, 失败返回 None
    if not items:
        return []
    out: list[QuoteRow] = []
    for it in items:
        try:
            code = str(it.get("code") or "").upper()
            if not code or not it.get("price"):
                continue
            mkt = "HK" if code.endswith(".HK") else "US"
            out.append(QuoteRow(
                code=code, market=mkt,
                name=it.get("name") or "", price=float(it["price"]),
                open=it.get("open"), high=it.get("high"), low=it.get("low"),
                prev_close=it.get("prev_close"),
                volume=it.get("volume") or 0, amount=it.get("turnover") or 0,
                change_pct=it.get("change_pct"),
                currency=it.get("currency") or ("HKD" if mkt == "HK" else "USD"),
                quote_time=_quote_time_bj(it.get("update_time") or 0), source="futu"))
        except Exception:
            continue
    return out


class _FutuSource:
    name = "futu"
    asset_types = ("HK", "US")

    async def snapshot(self, codes: list[str]) -> list[QuoteRow]:
        return await snapshot_rows(codes)


SOURCE = _FutuSource()
