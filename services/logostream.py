"""Logostream 股票 logo 数据源(https://logostream.dev, OpenAPI 契约见 tests/logostream-stock-logo-api.yaml)。

- 端点: GET https://api.logostream.dev/stocks/symbol/{SYMBOL}, 鉴权用 X-API-Key 请求头
  (服务端到服务端首选; ?key= 查询串是给 <img> 标签用的)。要知道"有没有真 logo"必须带
  fallback=none —— 缺省时未命中会回一张 monogram 占位图, 把占位图当 logo 缓存就闹笑话了。
  本项目固定参数: format=png&size=128&variant=transparent&fallback=none(128px 足够列表行
  2x retina, transparent 在浅色主题上不留白底方块)。
- 候选链三段(**isin → ticker → code 兜底**): isin 段用 symbol_dict 的 isin(全球唯一标识,
  未回填的码自动跳过); ticker 段用底稿裸 ticker(7203/AAPL)及交易所限定变体(XTKS:7203 /
  HKEX:0700 —— 官网 XETR:DAX 同款); code 段保留原标准码/后缀链做兜底。每码一条候选链,
  404 试下一个, 全挂才算 miss; 命中哪个候选随行落库(market.logo_cache.symbol)。
- 磁盘文件名统一**标准码**: icons/{code}.{ext}(如 7203.JP.png) —— 不管哪一环命中都叫这个名,
  找文件/手动放图都只认标准码。改造前按候选名落的旧文件(7203.png 等)仍可读(兼容段)。
- 缓存两级落库(market.logo_cache): **hit 存图 + miss 存状态** —— 重启零上游请求, 不再浪费 API。
  hit 同时镜像 icons/ 磁盘(老缓存兼容, 磁盘命中优先于 DB 读)。miss 30 天过期重试
  (上游会陆续补新 logo); 内存 miss 6h 是热路径快闸。
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
_MISS_TTL = 6 * 3600            # 内存 miss 快闸: 无 logo 的码 6 小时内不再试
_DB_MISS_TTL = 30 * 86400      # DB miss: 上游确认无 logo, 30 天过期重试(上游会补新 logo)
_AUTH_FAIL_TTL = 600           # 401/403(key 无效)后冷却 10 分钟, 不拿无效 key 反复打接口
_META_TTL = 60                 # symbol_dict ticker/isin 元数据缓存(手动补 isin 后 1 分钟生效)

_session = _requests.Session()
_session.trust_env = False

_key_cache: list = [None, 0.0]          # (key, ts)
_miss: dict[str, float] = {}            # code → miss 过期时刻(热路径快闸, 重启由 DB 兜)
_auth_fail_until = 0.0
_meta_cache: dict[str, tuple[dict, float]] = {}   # code → ({ticker, isin}, ts)


async def _api_key() -> str:
    """config.env 的 LOGOSTREAM_API_KEY。缓存 60s(与 tickflow._api_key 同款)。"""
    if time.time() - _key_cache[1] < 60 and _key_cache[0] is not None:
        return _key_cache[0]
    key = (os.environ.get("LOGOSTREAM_API_KEY") or "").strip()
    _key_cache[0], _key_cache[1] = key, time.time()
    return key


async def _meta(code: str) -> dict:
    """底稿(symbol_dict)的 ticker/isin, 候选链 ticker/isin 段用。缓存 5 分钟。"""
    hit = _meta_cache.get(code)
    if hit and time.time() - hit[1] < _META_TTL:
        return hit[0]
    out = {"ticker": "", "isin": ""}
    try:
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT ticker, isin FROM symbol_dict WHERE UPPER(code) = ?", (code,))
            r = await cur.fetchone()
            if r:
                out["ticker"] = str(r[0] or "").strip().upper()
                out["isin"] = str(r[1] or "").strip().upper()
        finally:
            await db.close()
    except Exception:
        pass
    _meta_cache[code] = (out, time.time())
    return out


def _code_candidates(code: str) -> list[str]:
    """code 段候选(兜底): 标准码原样 + Yahoo 风格后缀 + 交易所前缀。"""
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", code)
    if m:
        n, ex = m.groups()
        if ex == "SH":
            return [code, f"{n}.SS", f"SSE:{n}"]
        if ex == "SZ":
            return [code, f"SZSE:{n}"]
        return [code]                                    # BJ: 只有原样一条
    m = re.match(r"^(?:HK\.0*(\d{1,5})|0*(\d{1,5})\.HK)$", code)
    if m:
        n = (m.group(1) or m.group(2)).zfill(4)
        return [code, f"{n}.HK", f"HKEX:{n}"]
    m = re.match(r"^(?:US\.([A-Z0-9.\-]{1,10})|([A-Z0-9.\-]{1,10})\.US)$", code)
    if m:
        return [code, m.group(1) or m.group(2)]
    m = re.match(r"^(\d{4})\.JP$", code)                 # 日股: 标准码兜底
    if m:
        return [code]
    return []


def symbol_candidates(code: str, meta: dict | None = None) -> list[str]:
    """候选链三段: **isin → ticker → code 兜底**(去重保序)。

    isin 段: symbol_dict 的 isin —— 全球唯一标识, 最精准, 未回填为空自动跳过。
    ticker 段: 底稿裸 ticker(7203/AAPL —— logostream 官网查询同款)优先,
    交易所限定变体殿后(XTKS:7203 / HKEX:0700 / XETR:DAX 同款)。
    code 段: 标准码/后缀/前缀变体兜底(老行为, 老持仓/WS 推流的前缀式也在这里)。
    """
    meta = meta or {}
    out: list[str] = []
    isin = str(meta.get("isin") or "").strip().upper()
    ticker = str(meta.get("ticker") or "").strip().upper()
    if isin:
        out.append(isin)                                  # 全球唯一标识, 最精准, 首选
    if ticker:
        out.append(ticker)                                # 裸 ticker: 官网查询同款
        m = re.match(r"^(\d{4})\.JP$", code)
        if m:
            out.append(f"XTKS:{ticker}")                  # 东京: 交易所限定
        elif code.endswith(".HK"):
            out.append(f"HKEX:{ticker}")
    out.extend(_code_candidates(code))
    seen: set[str] = set()
    uniq = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


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


# ── market.logo_cache 落库(hit 存图 + miss 存状态, 重启零上游请求) ─────────────


async def _db_get(code: str) -> Optional[dict]:
    """DB 缓存行: {status, symbol, file_name, image, age}。无行 → None。"""
    try:
        from services import market_store
        pool = await market_store.get_pool()
        r = await pool.fetchrow(
            "SELECT symbol, status, file_name, image, "
            "EXTRACT(EPOCH FROM (now() - fetched_at)) AS age "
            "FROM market.logo_cache WHERE code = $1", code)
        return dict(r) if r else None
    except Exception as e:
        print(f"[logostream] logo_cache 读失败(当无缓存): {e}")
        return None


async def _db_write(code: str, status: str, symbol: str = "",
                    file_name: str = "", image: Optional[bytes] = None) -> None:
    try:
        from services import market_store
        pool = await market_store.get_pool()
        await pool.execute(
            "INSERT INTO market.logo_cache (code, symbol, status, file_name, image) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (code) DO UPDATE SET symbol = excluded.symbol, status = excluded.status, "
            "file_name = excluded.file_name, image = excluded.image, fetched_at = now()",
            code, symbol, status, file_name, image)
    except Exception as e:
        print(f"[logostream] logo_cache 写失败(不影响本次返回): {e}")


async def get_logo(code: str) -> Optional[tuple[bytes, str]]:
    """取一个码的 logo。查找序: 内存 miss 闸(6h 热闸) → DB 行(唯一权威) → 候选链
    (isin→ticker→code)联网 → 写 DB + 磁盘镜像。

    status 定稿语义(人工可用 SQL 改; DB 行是唯一权威, icons/ 只是镜像):
      hit   有图, 直接回(镜像缺失时补写 icons/)
      miss  上游确认无 logo, 30 天过期重试
      stop  人工确认没有 → 永不再取
    删 DB 行 = 强制重取(候选链重跑, 上游结果覆盖 icons/ 旧镜像)。
    无 logo / 未配 key / 非股票码 → None。"""
    code = (code or "").strip().upper()
    now = time.time()
    if _miss.get(code, 0) > now:
        return None

    # DB 行(唯一权威): hit 回图(镜像缺失补写); stop 永久跳过; miss 30 天内不重试。
    row = await _db_get(code)
    if row:
        status = row.get("status")
        if status == "hit" and row.get("image"):
            data = bytes(row["image"])
            file_name = row.get("file_name") or f"{code}.png"
            ctype = {"png": "image/png", "jpg": "image/jpeg",
                     "webp": "image/webp", "svg": "image/svg+xml"}.get(
                         file_name.rsplit(".", 1)[-1].lower(), "image/png")
            try:
                _ICONS_DIR.mkdir(exist_ok=True)
                if not any(_ICONS_DIR.glob(f"{code}.*")):
                    (_ICONS_DIR / file_name).write_bytes(data)
            except OSError:
                pass
            _miss.pop(code, None)
            return data, ctype
        if status == "stop":
            return None                       # 人工确认没有: 永不再取
        if status == "miss" and (row.get("age") or 0) < _DB_MISS_TTL:
            return None                       # miss 30 天内不重试(重启不打上游 —— 省配额的关键)

    meta = await _meta(code)
    cands = symbol_candidates(code, meta)
    if not cands:
        _miss[code] = time.time() + _MISS_TTL
        await _db_write(code, "miss")
        return None

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
            file_name = f"{code}.{ext}"                        # 文件名统一标准码, 不随候选
            try:
                _ICONS_DIR.mkdir(exist_ok=True)
                (_ICONS_DIR / file_name).write_bytes(data)
            except OSError:
                pass                    # 落盘失败不影响本次返回
            _miss.pop(code, None)
            await _db_write(code, "hit", symbol=sym, file_name=file_name, image=data)
            return data, ctype
    # 全链未命中: miss 落库(内存 6h 热闸 + DB 30 天), 重启不再重复打上游
    _miss[code] = time.time() + _MISS_TTL
    await _db_write(code, "miss")
    return None


async def refresh_holdings_logos() -> dict:
    """启动对账: 遍历在持标的, 逐个严格走 get_logo 状态机。

    hit → 直接回镜像; miss 未过期/stop → 跳过; miss 过期或无行 → 联网重取。
    顺序执行(持仓量小), 逐码一次。返回 {fetched, cached, skipped, codes}。"""
    from services.context import run_as_user
    from database import get_all_holdings, list_user_ids
    codes: list[str] = []
    seen: set[str] = set()
    # 多用户: 启动时无用户会话, 逐注册用户切上下文取各自的在持标的
    for uid in await list_user_ids():
        with run_as_user(uid):
            rows = await get_all_holdings()
        for h in rows:
            if (h.get("shares") or 0) <= 0:
                continue
            c = str(h.get("stock_code") or "").strip().upper()
            if c and c not in seen:
                seen.add(c)
                codes.append(c)
    stats = {"fetched": 0, "cached": 0, "skipped": 0, "codes": len(codes)}
    for c in codes:
        try:
            got = await get_logo(c)
        except Exception:
            got = None
        if got:
            stats["fetched"] += 1
        else:
            row = await _db_get(c)
            st = row.get("status") if row else None
            if st in ("stop", "miss"):
                stats["skipped"] += 1
            else:
                stats["cached"] += 1          # 无行/无 key/非股票码等
    return stats


# ── 国家/地区旗(assets 页市场徽标统一改国旗) ─────────────────────────────────
# 端点: GET /country/{ISO 3166-1 alpha-2}, aspect=1x1 方形。国旗按国家确定性存在 ——
# 无 miss 语义, 不入 logo_cache, 磁盘缓存(icons/flags/{cc}.png)永久, 一次抓取终身用。
_FLAGS_DIR = _ICONS_DIR / "flags"


async def get_flag(cc: str) -> Optional[tuple[bytes, str]]:
    """取国家/地区旗: icons/flags/ 磁盘缓存优先, 未命中且配置了 key 才联网。

    cc: ISO 3166-1 alpha-2(CN/HK/US/JP/SG/GB…), 大小写不敏感。无效码/未配 key → None。"""
    cc = (cc or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", cc):
        return None
    _FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    for f in _FLAGS_DIR.glob(f"{cc}.*"):
        try:
            data = f.read_bytes()
        except OSError:
            continue
        if data:
            ext = f.suffix.lstrip(".")
            ctype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
                     "svg": "image/svg+xml"}.get(ext, "image/png")
            return data, ctype
    key = await _api_key()
    if not key:
        return None
    global _auth_fail_until
    if time.time() < _auth_fail_until:
        return None
    try:
        got = await asyncio.to_thread(_flag_fetch, cc, key)
    except RuntimeError:
        _auth_fail_until = time.time() + _AUTH_FAIL_TTL
        return None
    except Exception:
        return None
    if not got:
        return None
    data, ctype, ext = got
    try:
        (_FLAGS_DIR / f"{cc}.{ext}").write_bytes(data)
    except OSError:
        pass
    return data, ctype


def _flag_fetch(cc: str, key: str) -> Optional[tuple[bytes, str, str]]:
    """联网拉一面旗。命中返回 (bytes, content_type, ext); 404/网络错误 → None;
    401/403 → 抛 RuntimeError 由调用方冷却。"""
    url = f"{BASE}/country/{cc}"
    r = _session.get(url, headers={"X-API-Key": key},
                     params={"format": "png", "aspect": "1x1", "fallback": "none"},
                     timeout=_TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError(f"logostream key rejected: HTTP {r.status_code}")
    if r.status_code != 200:
        return None
    ctype = r.headers.get("Content-Type", "image/png")
    if "image" not in ctype:
        return None
    return r.content, ctype, _ext_of(ctype)
