"""注册 / 登录 / 登出 / 会话信息 / 资料维护 / 显示偏好。

cookie 方案: HttpOnly + SameSite=Lax, 同源页面自动携带(含 WebSocket 握手),
前端 9 处不经 fetchJSON 的裸 fetch 也自动生效。安全边界由 run.py 的 same_origin
中间件(Host 白名单 + Origin 校验) + 本路由的会话校验共同构成。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from services import auth
from services.context import current_user_id

router = APIRouter(prefix="/api/auth", tags=["auth"])

_PREF_KEYS = ("pref_theme", "pref_wan_unit", "pref_updown_red")


class Credentials(BaseModel):
    # 登录 identifier: 用户名或邮箱(字段名沿用 username 兼容旧前端/脚本)
    username: str
    password: str


class RegisterBody(BaseModel):
    username: str
    password: str
    email: str
    code: str


class EmailCodeBody(BaseModel):
    email: str
    purpose: str = "register"          # register=注册 | bind=登录后绑定


class DisplayNameBody(BaseModel):
    display_name: str


class AvatarBody(BaseModel):
    data: str                          # data URL (image/png|jpeg|webp, ≤200KB)


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


class BindEmailBody(BaseModel):
    email: str
    code: str


class PreferencesBody(BaseModel):
    theme: str | None = None           # light | dark | eyecare(护眼=旧暖纸配色)
    wan_unit: bool | None = None       # True=金额≥1万显示「万」
    updown_red: bool | None = None     # True=红涨绿跌(中国惯例)
    font_scale: str | None = None      # s | m | l(小/中/大, html 根字号 14/16/18px)


def _set_cookie(resp: Response, token: str):
    resp.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True, samesite="lax", path="/",
    )


def _uid_or_401() -> int:
    uid = current_user_id()
    if not uid:
        raise HTTPException(401, "未登录")
    return uid


@router.post("/email-code")
async def email_code(body: EmailCodeBody):
    """发送邮箱验证码。purpose=bind 需登录。SMTP 未配置/限频/邮箱已占用 → 400 带文案。"""
    if body.purpose == "bind":
        _uid_or_401()
    try:
        await auth.send_email_code(body.email, body.purpose)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"邮件发送失败: {str(e)[:120]}")
    return {"ok": True}


@router.post("/register")
async def register(body: RegisterBody, response: Response):
    if err := auth.validate_credentials(body.username, body.password):
        raise HTTPException(400, err)
    try:
        # 验证码先行(注册 purpose 且邮箱未被占用时才可能发出), 过了才建号
        await auth.verify_email_code(body.email, "register", body.code)
        user = await auth.register_user(body.username, body.password, body.email)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = await auth.create_session(user["user_id"])
    _set_cookie(response, token)
    return {"user_id": user["user_id"], "username": user["username"]}


@router.post("/login")
async def login(body: Credentials, response: Response):
    user = await auth.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "用户名/邮箱或密码不对")
    token = await auth.create_session(user["user_id"])
    _set_cookie(response, token)
    return {"user_id": user["user_id"], "username": user["username"]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE_NAME) or ""
    await auth.delete_session(token)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me():
    """当前登录用户(完整资料)。未登录返回 401(前端据此显示登录页)。"""
    uid = _uid_or_401()
    user = await auth.get_user_full(uid)
    if not user:
        raise HTTPException(401, "未登录")
    return user


@router.post("/me")
async def update_me(body: DisplayNameBody):
    uid = _uid_or_401()
    name = await auth.update_display_name(uid, body.display_name)
    return {"ok": True, "display_name": name or ""}


@router.get("/me/avatar")
async def get_avatar():
    uid = _uid_or_401()
    data = await auth.get_avatar(uid)
    return {"data": data}


@router.put("/me/avatar")
async def put_avatar(body: AvatarBody):
    uid = _uid_or_401()
    try:
        await auth.set_avatar(uid, body.data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/me/avatar")
async def delete_avatar():
    uid = _uid_or_401()
    await auth.clear_avatar(uid)
    return {"ok": True}


@router.post("/me/password")
async def change_password(body: PasswordBody):
    uid = _uid_or_401()
    try:
        await auth.change_password(uid, body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/me/bind-email")
async def bind_email(body: BindEmailBody):
    """绑定/换绑邮箱(验证码由 /email-code purpose=bind 发出)。"""
    uid = _uid_or_401()
    try:
        await auth.verify_email_code(body.email, "bind", body.code)
        await auth.bind_email(uid, body.email, body.code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/preferences")
async def get_preferences():
    """显示偏好(账户页 → 显示设置)。未设置给默认: 浅色(纯白) / 万单位开 / 红涨绿跌 / 中号字体。
    主题三选 light|dark|eyecare; 旧存档的 'system'(跟随系统, 已下线)按浅色返回。"""
    uid = _uid_or_401()
    from database import get_config
    theme = (await get_config("pref_theme")) or "light"
    wan = (await get_config("pref_wan_unit")) != "0"      # 缺省开(=既有显示口径)
    red = (await get_config("pref_updown_red")) != "0"    # 缺省红涨绿跌
    scale = (await get_config("pref_font_scale")) or "m"  # 缺省中号
    if theme not in ("light", "dark", "eyecare"):
        theme = "light"
    return {"theme": theme,
            "wan_unit": wan, "updown_red": red,
            "font_scale": scale if scale in ("s", "m", "l") else "m"}


@router.put("/preferences")
async def put_preferences(body: PreferencesBody):
    uid = _uid_or_401()
    from database import set_config
    if body.theme is not None:
        if body.theme not in ("light", "dark", "eyecare"):
            raise HTTPException(400, "theme 只支持 light/dark/eyecare")
        await set_config("pref_theme", body.theme)
    if body.wan_unit is not None:
        await set_config("pref_wan_unit", "1" if body.wan_unit else "0")
    if body.updown_red is not None:
        await set_config("pref_updown_red", "1" if body.updown_red else "0")
    if body.font_scale is not None:
        if body.font_scale not in ("s", "m", "l"):
            raise HTTPException(400, "font_scale 只支持 s/m/l")
        await set_config("pref_font_scale", body.font_scale)
    return await get_preferences()
