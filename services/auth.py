"""账号与会话: 注册 / 登录 / 校验 / 登出 / 邮箱验证码 / 资料维护。

密码: pbkdf2_hmac(sha256, 20万轮, 16字节随机盐), 存储格式
  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
会话: secrets.token_urlsafe(32) 作为 cookie 原文; 库里只存它的 sha256(token_hash 为
主键)。30 天滑动过期 —— 每次校验成功都顺延 expires_at 并更新 last_seen_at。

邮箱注册(2026-11): 注册 = 用户名 + 邮箱 + 验证码 + 密码; 登录 identifier 用户名/邮箱
皆可。验证码 6 位数字、10 分钟过期、用后即 consume; 限频同邮箱 60s 冷却 + 每日 20 条。
微信注册/登录预留(wechat_openid 列已建, WECHAT_APP_ID 未配置前不启用)。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from database import get_db
from services import mailer

PBKDF2_ITERATIONS = 200_000
SESSION_TTL = timedelta(days=30)
COOKIE_NAME = "licai_session"

CODE_TTL = timedelta(minutes=10)
CODE_RESEND_COOLDOWN = timedelta(seconds=60)
CODE_DAILY_LIMIT = 20

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


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


def validate_email(email: str) -> str | None:
    e = (email or "").strip().lower()
    if not e:
        return "邮箱必填"
    if len(e) > 254 or not _EMAIL_RE.match(e):
        return "邮箱格式不对"
    return None


async def _email_taken(db, email: str) -> bool:
    cur = await db.execute("SELECT id FROM users WHERE email = ?", (email,))
    return bool(await cur.fetchone())


async def send_email_code(email: str, purpose: str) -> None:
    """生成并发送验证码。所有拒绝路径都抛 ValueError(文案直接给用户)。"""
    e = (email or "").strip().lower()
    if err := validate_email(e):
        raise ValueError(err)
    if purpose not in ("register", "bind"):
        raise ValueError("用途不对")
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    db = await get_db()
    try:
        # 注册: 邮箱必须未注册; 绑定: 邮箱必须未注册(也不能绑到别人头上)
        if await _email_taken(db, e):
            raise ValueError("该邮箱已注册")
        cur = await db.execute(
            "SELECT MAX(created_at) AS last_at, COUNT(*) AS n FROM email_code "
            "WHERE email = ? AND created_at >= ?",
            (e, _utc_text(now - timedelta(days=1))))
        row = await cur.fetchone()
        last = None
        if row and row["last_at"]:
            try:
                last = datetime.strptime(str(row["last_at"]), "%Y-%m-%d %H:%M:%S")
            except Exception:
                last = None
        if last and (now - last).total_seconds() < CODE_RESEND_COOLDOWN.total_seconds():
            raise ValueError("发送太频繁, 请 1 分钟后再试")
        if row and row["n"] >= CODE_DAILY_LIMIT:
            raise ValueError("今日验证码已达上限, 请明天再试")
        code = f"{secrets.randbelow(1_000_000):06d}"
        await db.execute(
            "INSERT INTO email_code (email, code, purpose, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (e, code, purpose, _utc_text(now + CODE_TTL), _utc_text(now)))
        await db.commit()
    finally:
        await db.close()
    await mailer.send_verification_code(e, code, purpose)


async def verify_email_code(email: str, purpose: str, code: str) -> None:
    """校验并 consume 验证码。失败抛 ValueError。"""
    e = (email or "").strip().lower()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, expires_at FROM email_code "
            "WHERE email = ? AND purpose = ? AND code = ? AND consumed = 0 "
            "ORDER BY id DESC LIMIT 1", (e, purpose, (code or "").strip()))
        row = await cur.fetchone()
        if not row:
            raise ValueError("验证码不对")
        try:
            exp = datetime.strptime(str(row["expires_at"]), "%Y-%m-%d %H:%M:%S")
        except Exception:
            exp = now - timedelta(seconds=1)
        if exp < now:
            raise ValueError("验证码已过期, 请重新发送")
        await db.execute("UPDATE email_code SET consumed = 1 WHERE id = ?", (row["id"],))
        await db.commit()
    finally:
        await db.close()


async def register_user(username: str, password: str, email: str = "",
                        display_name: str = "") -> dict:
    """创建账号(用户名+密码; 邮箱可选传入, 已由 verify_email_code 验过)。用户名/邮箱重复抛 ValueError。"""
    u = _normalize_username(username)
    e = (email or "").strip().lower()
    db = await get_db()
    try:
        cur = await db.execute("SELECT id FROM users WHERE username = ?", (u,))
        if await cur.fetchone():
            raise ValueError("用户名已被占用")
        if e and await _email_taken(db, e):
            raise ValueError("该邮箱已注册")
        cur = await db.execute(
            "INSERT INTO users (username, password_hash, display_name, email, email_verified) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (u, hash_password(password), (display_name or "").strip()[:32], e or None,
             1 if e else 0))
        new_id = cur.lastrowid
        await db.commit()
    finally:
        await db.close()
    # 券商: 纯用户私有, 注册用户各自在账户页按需添加(add_broker 落自己 user_id 名下);
    # 无默认券商概念, 新用户券商列表为空, 费率估算回退内置默认。
    return {"user_id": new_id, "username": u}


async def authenticate(identifier: str, password: str) -> dict | None:
    """校验用户名或邮箱 + 密码。成功返回 {user_id, username}; 失败 None(不区分两种失败, 防枚举)。"""
    ident = (identifier or "").strip()
    db = await get_db()
    try:
        # 用户名精确匹配优先, 落空再按邮箱(唯一索引, 至多一行)
        cur = await db.execute(
            "SELECT id, password_hash, username FROM users WHERE username = ?", (ident,))
        row = await cur.fetchone()
        if not row:
            cur = await db.execute(
                "SELECT id, password_hash, username FROM users WHERE email = ?",
                (ident.lower(),))
            row = await cur.fetchone()
        if not row or not verify_password(password or "", row["password_hash"]):
            return None
        return {"user_id": row["id"], "username": row["username"]}
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


# ── 资料维护(账户中心): 昵称 / 绑定邮箱 / 改密 / 头像 ─────────────────────

MAX_AVATAR_BYTES = 200 * 1024          # data URL 总长上限(~200KB, 前端已先压缩)
_AVATAR_DATAURL_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")


async def get_user_full(user_id: int) -> dict | None:
    """账户页展示用的完整用户信息。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, username, display_name, email, email_verified, wechat_openid, "
            "(avatar IS NOT NULL) AS has_avatar, created_at "
            "FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "user_id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "email": row["email"],
            "email_verified": bool(row["email_verified"]),
            "wechat_bound": bool(row["wechat_openid"]),
            "has_avatar": bool(row["has_avatar"]),
            "created_at": str(row["created_at"]) if row["created_at"] else "",
        }
    finally:
        await db.close()


async def get_avatar(user_id: int) -> str | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT avatar FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        return row["avatar"] if row else None
    finally:
        await db.close()


async def set_avatar(user_id: int, data_url: str) -> None:
    if not _AVATAR_DATAURL_RE.match(data_url or ""):
        raise ValueError("头像只支持 png/jpeg/webp")
    if len(data_url) > MAX_AVATAR_BYTES:
        raise ValueError("头像超过 200KB, 请换小图")
    db = await get_db()
    try:
        await db.execute("UPDATE users SET avatar = ?, updated_at = ? WHERE id = ?",
                         (data_url, _utc_text(datetime.now(timezone.utc)), user_id))
        await db.commit()
    finally:
        await db.close()


async def clear_avatar(user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("UPDATE users SET avatar = NULL WHERE id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()


async def update_display_name(user_id: int, display_name: str) -> str:
    name = (display_name or "").strip()[:32]
    db = await get_db()
    try:
        await db.execute("UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?",
                         (name, _utc_text(datetime.now(timezone.utc)), user_id))
        await db.commit()
    finally:
        await db.close()
    return name


async def bind_email(user_id: int, email: str, code: str) -> None:
    """登录态绑定/换绑邮箱(验证码已验)。换绑 = 直接覆盖 + 置 verified。"""
    e = (email or "").strip().lower()
    if err := validate_email(e):
        raise ValueError(err)
    db = await get_db()
    try:
        cur = await db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (e, user_id))
        if await cur.fetchone():
            raise ValueError("该邮箱已被其他账号绑定")
        await db.execute(
            "UPDATE users SET email = ?, email_verified = 1, updated_at = ? WHERE id = ?",
            (e, _utc_text(datetime.now(timezone.utc)), user_id))
        await db.commit()
    finally:
        await db.close()


async def change_password(user_id: int, old_password: str, new_password: str) -> None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if not row or not verify_password(old_password or "", row["password_hash"]):
            raise ValueError("当前密码不对")
        if not (6 <= len(new_password or "") <= 128):
            raise ValueError("新密码需 6-128 个字符")
        await db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                         (hash_password(new_password), _utc_text(datetime.now(timezone.utc)), user_id))
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
