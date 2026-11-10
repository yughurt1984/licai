"""Logostream 股票 logo 数据源(https://logostream.dev, OpenAPI 契约见 tests/logostream-stock-logo-api.yaml)。

- 端点: GET https://api.logostream.dev/stocks/symbol/{SYMBOL}, 鉴权用 X-API-Key 请求头
  (服务端到服务端首选; ?key= 查询串是给 <img> 标签用的)。要知道"有没有真 logo"必须带
  fallback=none —— 缺省时未命中会回一张 monogram 占位图, 把占位图当 logo 缓存就闹笑话了。
  本项目固定参数: format=png&size=128&variant=transparent&fallback=none(128px 足够列表行
  2x retina, transparent 在浅色主题上不留白底方块)。
- symbol 候选链: 首选**标准码原样直传**(symbol_dict 同款格式, 认了就不需要映射), 不认再落
  Yahoo 风格后缀 / 交易所前缀。官方只给了 .DE/.L 后缀示例, A股/港股格式未逐一列出 —— 每码
  一条候选链, 404 试下一个, 全挂才算 miss; 命中哪个候选随文件落盘(icons/{symbol}.png)。
- 磁盘缓存: icons/(项目根, 已进 .gitignore —— 第三方品牌资产可随时重下)。logo 基本不变,
  命中即零联网; miss 记内存 6h, 防止冷门的码反复打接口。
- key: config.env 的 LOGOSTREAM_API_KEY(经 db.py 灌进 os.environ, 本模块直读)。
  未配置 = 功能整体关闭(get_logo 恒 None, 前端 <img> onError 退回字母圆标)。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests as _requests

BASE = "https://api.logostream.dev"
_TIMEOUT = 10

_ICONS_DIR = Path(__file__).resolve().parent.parent / "icons"
_MISS_TTL = 6 * 3600        # 无 logo 的码 6 小时内不再试
_AUTH_FAIL_TTL = 600        # 401/403(key 无效)后冷却 10 分钟, 不拿无效 key 反复打接口

_session = _requests.Session()
_session.trust_env = False

_key_cache: list = [None, 0.0]          # (key, ts)
_miss: dict[str, float] = {}            # code → miss 过期时刻
_auth_fail_until = 0.0


async def _api_key() -> str:
    """config.env 的 LOGOSTREAM_API_KEY。缓存 60s(与 tickflow._api_key 同款)。"""
    if time.time() - _key_cache[1] < 60 and _key_cache[0] is not None:
        return _key_cache[0]
    key = (os.environ.get("LOGOSTREAM_API_KEY") or "").strip()
    _key_cache[0], _key_cache[1] = key, time.time()
    return key


def symbol_candidates(code: str) -> list[str]:
    """标准码 → logostream symbol 候选链。港股/美股**前缀式(HK.00700/US.AAPL)与
    后缀式(00700.HK/SNDK.US)两种取向都收** —— 老持仓/WS 推流用前缀式, symbol_dict、
    榜单与 instruments 源(code_suffix 体系)用后缀式, 入口不归一就有一半码本地 404。

    首选**原样直传** —— logostream 若恰好认这个格式, 映射层即告无效; 不认也就是多一次
    404(首次, 之后落盘/记 miss, 不再重复)。备选: Yahoo 风格后缀(官网示例 SAP.DE / VOD.L
    同款)与交易所前缀(XETR:DAX 同款)。全链指向同一标的, 无错配风险。

    600519.SH → ['600519.SH', '600519.SS', 'SSE:600519']
    00700.HK / HK.00700 → ['…原样…', '0700.HK', 'HKEX:0700']
    SNDK.US / US.SNDK → ['…原样…', 'SNDK']
    场外基金(.OF)/裸码/加密/期货等没有交易所 ticker → 空链, 调用方直接当 miss。
    """
    code = (code or "").strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", code)
    if m:
        n, ex = m.groups()
        if ex == "SH":
            return [code, f"{n}.SS", f"SSE:{n}"]
        if ex == "SZ":
            return [code, f"SZSE:{n}"]
        return [code]                                    # BJ: 只有原样一条
    # 港股 4 位为主流(Yahoo 0700.HK); 0* 吸掉前导零, 00894.HK / HK.00894 都归一到 0894
    m = re.match(r"^(?:HK\.0*(\d{1,5})|0*(\d{1,5})\.HK)$", code)
    if m:
        n = (m.group(1) or m.group(2)).zfill(4)
        return [code, f"{n}.HK", f"HKEX:{n}"]
    m = re.match(r"^(?:US\.([A-Z0-9.\-]{1,10})|([A-Z0-9.\-]{1,10})\.US)$", code)
    if m:
        return [code, m.group(1) or m.group(2)]
    return []


def _ext_of(content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
            "image/svg+xml": "svg"}.get(ct, "png")


def _remote_fetch(symbol: str, key: str) -> Optional[tuple[bytes, str, str]]:
    """联网拉一张 logo。命中返回 (bytes, content_type, ext); 404(无 logo)/网络错误 → None;
    401/403(key 缺失或无效) → 抛 RuntimeError, 由调用方统一冷却。"""
    url = f"{BASE}/stocks/symbol/{symbol}"
    r = _session.get(url, headers={"X-API-Key": key},
                     params={"format": "png", "size": "128",
                             "variant": "transparent", "fallback": "none"},
                     timeout=_TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError(f"logostream key rejected: HTTP {r.status_code}")
    if r.status_code != 200:
        return None
    ctype = r.headers.get("Content-Type", "image/png")
    if "image" not in ctype:
        return None                     # 按契约不该发生(format 传错才会静默回 svg), 防御一下
    return r.content, ctype, _ext_of(ctype)


async def get_logo(code: str) -> Optional[tuple[bytes, str]]:
    """取一个码的 logo: icons/ 磁盘缓存优先, 未命中且配置了 key 才联网。
    返回 (bytes, content_type); 无 logo / 未配 key / 非股票码 → None。"""
    code = (code or "").strip().upper()
    cands = symbol_candidates(code)
    if not cands:
        return None
    now = time.time()
    if _miss.get(code, 0) > now:
        return None

    # 磁盘缓存: 任一候选有落盘文件即用(之前哪一环命中过就信哪一环)。
    # 注意 pathlib 的 glob 不认正则转义(Windows 上 \ 是路径分隔符), 而 symbol 字符集
    # 只含 [A-Z0-9.\-] 无 glob 特殊字符, 模式里的 . 当通配用也无实害。
    _ICONS_DIR.mkdir(exist_ok=True)
    for sym in cands:
        for f in _ICONS_DIR.glob(f"{sym}.*"):
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if data:
                ext = f.suffix.lstrip(".")
                ctype = {"png": "image/png", "jpg": "image/jpeg",
                         "webp": "image/webp", "svg": "image/svg+xml"}.get(ext, "image/png")
                return data, ctype

    key = await _api_key()
    if not key:
        return None
    global _auth_fail_until
    if now < _auth_fail_until:
        return None
    for sym in cands:
        try:
            got = await asyncio.to_thread(_remote_fetch, sym, key)
        except RuntimeError:
            _auth_fail_until = time.time() + _AUTH_FAIL_TTL
            return None
        except Exception:
            continue                    # 网络失败: 不记 miss(下次还试), 本轮先放弃
        if got:
            data, ctype, ext = got
            try:
                (_ICONS_DIR / f"{sym}.{ext}").write_bytes(data)
            except OSError:
                pass                    # 落盘失败不影响本次返回
            _miss.pop(code, None)
            return data, ctype
    _miss[code] = time.time() + _MISS_TTL
    return None
