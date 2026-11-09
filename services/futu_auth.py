"""futu OpenAPI REST 公共层: 配置解析 + 鉴权(API Key 签名) + 会话。

报价(futu_quote.py)与标的信息(instruments.py)两类请求共用本层, 职责仅限:
  - 凭证配置: app_config(设置页) → .env/env → config.py;  host/app_key/private_key/algo
  - API Key 签名(方式二): X-Api-Key / X-Timestamp(毫秒) / X-Nonce /
    Authorization = base64(签名)。签名原文 5 段以 \n 连接:
    {timestamp_ms}\n{METHOD}\n{request_path}\n{query_string}\n{body_part},
    body_part = 原始 body 字节的 SHA256 小写十六进制; 签名算法 Ed25519 或 RSA-SHA256
  - auth_for(): 统一鉴权 —— 未配置给出原因
业务请求(报价快照/标的元数据/公司行动)在各自模块发, 本层不发业务请求。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
from typing import Optional

import requests as _requests


DEFAULT_HOST = "https://webapi.futunn.com"

_session = _requests.Session()
_session.trust_env = False          # 不吃系统代理

_cfg_cache: list = [None, 0.0]      # (cfg dict, ts); 60s TTL, 设置页保存后 invalidate
_last_error: str = ""               # 鉴权级错误(区别于各业务模块自己的业务错误)
_cooldown_until: float = 0.0        # 限流冷却截止(-12011)


def last_error() -> str:
    return _last_error


def invalidate_ctx():
    """设置页改了凭证后清全部缓存(语义=重建上下文)。"""
    global _cfg_cache, _cooldown_until
    _cfg_cache = [None, 0.0]
    _cooldown_until = 0.0


# ── 配置解析(优先级: app_config 设置页 → .env/env → config.py 默认) ─────────
def _resolve_cfg_from_env() -> dict:
    """config.env 由 db.py 导入时灌进 os.environ, 这里直读环境变量即可。"""
    return {
        "host": (os.environ.get("FUTU_API_HOST", "") or DEFAULT_HOST).rstrip("/"),
        "app_key": (os.environ.get("FUTU_APP_KEY", "") or "").strip(),
        "private_key": (os.environ.get("FUTU_PRIVATE_KEY", "") or "").strip(),
        "algo": (os.environ.get("FUTU_SIGN_ALGO", "") or "ed25519").strip().lower(),
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


# ── API Key 签名(方式二) ──────────────────────────────────────────────────
def _load_private_key(pem: str):
    """加载 PEM 私钥。宽容处理常见粘贴问题: 字面量 \\n 转义、首尾引号/空白。"""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    pem = (pem or "").strip().strip('"').strip("'")
    if "\\n" in pem:
        pem = pem.replace("\\n", "\n")       # 单行 \\n 转义 → 真换行(多行双引号已带真换行, 此处不触发)
    return load_pem_private_key(pem.encode("utf-8"), password=None)


def _sign_message(key, algo: str, message: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if isinstance(key, Ed25519PrivateKey):
        return key.sign(message)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA256())


def build_auth_headers(method: str, path: str, query: str, body: bytes,
                       app_key: str, private_key, algo: str) -> dict:
    """按富途 API Key 规范构造鉴权头。签名原文: ts\nMETHOD\npath\nquery\nsha256hex(body)。

    private_key 传 PEM 文本(str, 内部加载)或已加载的密钥对象(批量请求避免重复解析)。
    """
    ts_ms = str(int(time.time() * 1000))
    body_part = hashlib.sha256(body or b"").hexdigest() if body else ""
    raw = "\n".join([ts_ms, method.upper(), path, query or "", body_part])
    key = private_key if hasattr(private_key, "sign") else _load_private_key(private_key)
    sig = base64.b64encode(_sign_message(key, algo, raw.encode("utf-8"))).decode("ascii")
    return {
        "X-Api-Key": app_key,
        "X-Timestamp": ts_ms,
        "X-Nonce": os.urandom(8).hex(),
        "Authorization": sig,
        "Content-Type": "application/json",
    }


async def auth_for(method: str, path: str, query: str = "") -> tuple:
    """统一鉴权: API Key 签名(AppKey+PEM 私钥, 永不过期, 无人值守友好)。

    返回 ("apikey", app_key, key_obj, algo) —— 调用方按 body 构造签名头
    (build_auth_headers(method, path, query, body, app_key, key_obj, algo));
    未配置返回 (None, 原因)。
    """
    global _last_error
    cfg = await futu_config()
    if cfg.get("app_key") and cfg.get("private_key"):
        try:
            return ("apikey", cfg["app_key"], _load_private_key(cfg["private_key"]), cfg["algo"])
        except Exception as e:
            _last_error = f"私钥加载失败: {str(e)[:120]}"
            return (None, _last_error)
    return (None, "未配置 futu API Key 签名(AppKey+PEM 私钥, 设置页或 FUTU_APP_KEY/FUTU_PRIVATE_KEY)")


# ── 探活(设置页; server-time 未配置凭证也能打) ─────────────────────────────
async def futu_probe() -> dict:
    cfg = await futu_config()
    configured = bool(cfg.get("app_key") and cfg.get("private_key"))
    note, ok = "", None
    try:
        r = _session.get(cfg["host"] + "/api/v1.0/server-time", timeout=6)
        j = r.json()
        if j.get("server_time_ms") or j.get("ret_code") == 0:
            ok, note = True, "接口可达"
        else:
            ok, note = None, f"ret_code={j.get('ret_code')}: {str(j.get('ret_msg', ''))[:80]}"
    except Exception as e:
        ok, note = False, str(e)[:120]
    return {"configured": configured, "valid": ok, "note": note,
            "host": cfg["host"], "algo": cfg["algo"],
            "app_key_masked": (cfg["app_key"][:4] + "****" + cfg["app_key"][-2:]) if len(cfg.get("app_key") or "") > 6 else ("****" if cfg.get("app_key") else ""),
            "last_error": _last_error}
