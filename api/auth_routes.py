"""注册 / 登录 / 登出 / 会话信息。

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


class Credentials(BaseModel):
    username: str
    password: str


def _set_cookie(resp: Response, token: str):
    resp.set_cookie(
        auth.COOKIE_NAME, token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True, samesite="lax", path="/",
    )


@router.post("/register")
async def register(body: Credentials, response: Response):
    if err := auth.validate_credentials(body.username, body.password):
        raise HTTPException(400, err)
    try:
        user = await auth.register_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = await auth.create_session(user["user_id"])
    _set_cookie(response, token)
    return {"user_id": user["user_id"], "username": user["username"]}


@router.post("/login")
async def login(body: Credentials, response: Response):
    user = await auth.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "用户名或密码不对")
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
    """当前登录用户。未登录返回 401(前端据此显示登录页)。"""
    from database import get_db
    uid = current_user_id()
    if not uid:
        raise HTTPException(401, "未登录")
    db = await get_db()
    try:
        cur = await db.execute("SELECT id, username, display_name FROM users WHERE id = ?", (uid,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(401, "未登录")
        return {"user_id": row["id"], "username": row["username"],
                "display_name": row["display_name"] or row["username"]}
    finally:
        await db.close()
