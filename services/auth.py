"""账号与会话: 注册 / 登录 / 校验 / 登出。

密码: pbkdf2_hmac(sha256, 20万轮, 16字节随机盐), 存储格式
  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
会话: secrets.token_urlsafe(32) 作为 cookie 原文; 库里只存它的 sha256(token_hash 为
主键)。30 天滑动过期 —— 每次校验成功都顺延 expires_at 并更新 last_seen_at。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from database import get_db

PBKDF2_ITERATIONS = 200_000
SESSION_TTL = timedelta(days=30)
COOKIE_NAME = "licai_session"


def _utc_text(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    return (username or "").strip()


def validate_credentials(username: str, password: str) -> str | None:
    """返回 None 表示合法, 否则返回给用户看的错误文案。"""
    u = _normalize_username(username)
    if not (2 <= len(u) <= 32):
        return "用户名需 2-32 个字符"
    if not (6 <= len(password or "") <= 128):
        return "密码需 6-128 个字符"
    return None


async def register_user(username: str, password: str, display_name: str = "") -> dict:
    """创建账号。返回 {user_id, username}。用户名重复抛 ValueError。"""
    u = _normalize_username(username)
    db = await get_db()
    try:
        cur = await db.execute("SELECT id FROM users WHERE username = ?", (u,))
        if await cur.fetchone():
            raise ValueError("用户名已被占用")
        cur = await db.execute(
            "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?) RETURNING id",
            (u, hash_password(password), (display_name or "").strip()[:32]))
        new_id = cur.lastrowid
        # 新用户送默认券商费率档案(与 init_db 的全局种子同一套) —— 否则费率下拉是空的
        await db.execute(
            "INSERT INTO brokers (user_id, name, stock_rate, stock_min, etf_rate, etf_min, is_default) "
            "VALUES (?, '招商证券', 0.00025, 5.0, 0.00025, 5.0, 1), "
            "(?, '银河证券', 0.00012, 5.0, 0.0001, 0.1, 0), "
            "(?, '盈立证券', 0.0008, 0.0, 0.0008, 0.0, 0)",
            (new_id, new_id, new_id))
        await db.commit()
        return {"user_id": new_id, "username": u}
    finally:
        await db.close()


async def authenticate(username: str, password: str) -> dict | None:
    """校验用户名密码。成功返回 {user_id, username}; 失败 None(不区分两种失败, 防枚举)。"""
    u = _normalize_username(username)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (u,))
        row = await cur.fetchone()
        if not row or not verify_password(password or "", row["password_hash"]):
            return None
        return {"user_id": row["id"], "username": u}
    finally:
        await db.close()


async def create_session(user_id: int) -> str:
    """新会话: 返回放 cookie 的 token 原文。"""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, _utc_text(now + SESSION_TTL), _utc_text(now)))
        await db.commit()
        return token
    finally:
        await db.close()


async def resolve_session(token: str) -> dict | None:
    """token → {user_id, username}。有效则滑动续期; 无效/过期返回 None。"""
    if not token:
        return None
    th = _token_hash(token)
    # 无时区 UTC(全库时间列都是 UTC 文本, 带时区的 now 没法和 naive 的 exp 比较)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT s.user_id, s.expires_at, u.username FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (th,))
        row = await cur.fetchone()
        if not row:
            return None
        try:
            exp = datetime.strptime(str(row["expires_at"]), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
        if exp < now:
            await db.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            await db.commit()
            return None
        await db.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
            (_utc_text(now), _utc_text(now + SESSION_TTL), th))
        await db.commit()
        return {"user_id": row["user_id"], "username": row["username"]}
    finally:
        await db.close()


async def delete_session(token: str) -> None:
    if not token:
        return
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        await db.commit()
    finally:
        await db.close()


async def list_user_ids() -> list[int]:
    """全部用户 id(后台循环逐用户处理用)。"""
    db = await get_db()
    try:
        cur = await db.execute("SELECT id FROM users ORDER BY id")
        return [r[0] for r in await cur.fetchall()]
    finally:
        await db.close()
