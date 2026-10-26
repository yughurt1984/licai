"""多用户上下文: 当前用户 id 的注入与读取。

为什么用 ContextVar 而不是给 ~110 个 database 函数都加 user_id 参数: 全部服务层
(30+ 模块)都经由 database.py 拿数据, 上下文注入让它们的调用方零改动。

设置点:
- run.py 鉴权中间件: 每个请求校验会话后 set_current_user(uid)
- api/ws.py: WebSocket 握手校验后对连接生命周期设置
- 后台循环: `with run_as_user(uid):` 逐用户包裹(循环里同时处理多个用户)
- 脚本/评测: 不设置 → current_user_id() 返回 0(无主数据), 是安全兜底而非越权口
  —— /api 全部在中间件后面, uid=0 只可能出现在脚本和测试里。
"""
from __future__ import annotations

from contextlib import contextmanager

from contextvars import ContextVar

_user_id: ContextVar[int] = ContextVar("licai_user_id", default=0)


def set_current_user(uid: int) -> None:
    _user_id.set(int(uid or 0))


def current_user_id() -> int:
    """当前用户 id; 无上下文(脚本/测试)返回 0。"""
    return int(_user_id.get() or 0)


@contextmanager
def run_as_user(uid: int):
    """在指定用户上下文里执行一段代码(后台循环逐用户处理用), 结束恢复原上下文。"""
    token = _user_id.set(int(uid or 0))
    try:
        yield uid
    finally:
        _user_id.reset(token)
