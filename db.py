"""PostgreSQL 访问层: asyncpg 连接池 + aiosqlite 兼容外壳。

项目从 SQLite 迁到 PostgreSQL (2026-10)。database.py 里 ~110 个函数以及三个直连库的
service(tool_gaps / rule_forge / user_memory)的调用形态 —— execute → cursor →
fetchone/fetchall、lastrowid、rowcount、commit/rollback、close —— 保持不变, 这一层的
职责就是把方言与协议差异挡在这里:

  - `?` 占位符 → $n 顺序转换(跳过字符串字面量里的 `?`)
  - aiosqlite.Row 的访问习惯 → asyncpg Record 原生全支持: r["col"] / r[0] / 元组解包 /
    dict(r) / dict(zip(cols, r)), 行对象直接透传
  - 写事务语义: aiosqlite 是「首条 DML 隐式 BEGIN, commit() 提交, close 未提交即回滚」,
    这里用惰性事务 1:1 还原 —— 每个函数照旧只在自己 commit() 时落库
  - api/export_routes.py 的裸 BEGIN / COMMIT / ROLLBACK 文本语句 → 拦截转译成 asyncpg 事务
  - bool 参数 → int (SQLite 里布尔就是 0/1 整数, PG 的 SMALLINT 列不吃 bool)
  - CURRENT_TIMESTAMP 的语义由 database.py 里的 utc_now_text() 函数承担(UTC 文本
    'YYYY-MM-DD HH:MM:SS', 与 SQLite 时代的存储格式逐字节一致)

库名不存在时自动 CREATE DATABASE (个人单机项目, 方便首启)。
"""
from __future__ import annotations
import asyncio
import re
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from config import config


class _PlaceholderError(ValueError):
    pass


def to_pg_sql(sql: str) -> str:
    """`?` → $1..$n。字符串字面量('...')与双引号标识符("...")里的 ? 不动。

    运行时断言: 转换后不得残留 `?`, 否则说明 SQL 里混进了无法归位的东西。
    """
    out: list[str] = []
    n = 0
    i, L = 0, len(sql)
    in_s = in_d = False          # '...' 字符串 / "..." 标识符
    while i < L:
        c = sql[i]
        if in_s:
            out.append(c)
            if c == "'":
                if i + 1 < L and sql[i + 1] == "'":   # '' 转义
                    out.append("'")
                    i += 1
                else:
                    in_s = False
            i += 1
            continue
        if in_d:
            out.append(c)
            if c == '"':
                in_d = False
            i += 1
            continue
        if c == "'":
            in_s = True
            out.append(c)
        elif c == '"':
            in_d = True
            out.append(c)
        elif c == "-" and i + 1 < L and sql[i + 1] == "-":   # -- 注释: 里的 ? 不是占位符
            j = sql.find("\n", i)
            j = L if j < 0 else j
            out.append(sql[i:j])
            i = j
            continue
        elif c == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(c)
        i += 1
    if in_s or in_d:
        raise _PlaceholderError(f"SQL 引号未闭合: {sql[:80]!r}")
    converted = "".join(out)
    if _has_bare_qmark(converted):
        raise _PlaceholderError(f"占位符转换后仍残留未转换的 '?': {sql[:120]!r}")
    return converted


def _has_bare_qmark(sql: str) -> bool:
    """引号外(且不在注释里)是否还有 `?` —— 有就是没转换干净的占位符。"""
    i, L = 0, len(sql)
    in_s = in_d = False
    while i < L:
        c = sql[i]
        if in_s:
            if c == "'":
                if i + 1 < L and sql[i + 1] == "'":
                    i += 1
                else:
                    in_s = False
            i += 1
            continue
        if in_d:
            if c == '"':
                in_d = False
            i += 1
            continue
        if c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "-" and i + 1 < L and sql[i + 1] == "-":
            i = sql.find("\n", i)
            i = L if i < 0 else i
            continue
        elif c == "?":
            return True
        i += 1
    return False


def _norm_params(args: tuple) -> tuple:
    """SQLite 时代布尔就是 0/1 整数; PG 的 SMALLINT 列不收 Python bool。"""
    return tuple(int(v) if isinstance(v, bool) else v for v in args)


_WRITE_RE = re.compile(r"\s*(INSERT|UPDATE|DELETE|MERGE)\b", re.I)
_TX_CMD_RE = re.compile(r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;?\s*$", re.I)
_SELECT_RE = re.compile(r"\s*(SELECT|WITH|TABLE|VALUES)\b", re.I)


class Cursor:
    """模拟 aiosqlite cursor: fetchone / fetchall / lastrowid / rowcount / description。"""

    def __init__(self, rows: list, status: str = "", lastrowid=None, description=None):
        self._rows = rows
        self._i = 0
        self._status = status
        self.lastrowid = lastrowid
        self._description = description

    @property
    def rowcount(self) -> int:
        if self._status:
            m = re.search(r"(\d+)\s*$", self._status)
            if m:
                return int(m.group(1))
        return len(self._rows)

    @property
    def description(self):
        return self._description

    @property
    def statement(self) -> str:
        return self._status    # asyncpg 语义: execute() 的返回值(状态串), 保留给需要的调用方

    async def fetchone(self):
        if self._i >= len(self._rows):
            return None
        r = self._rows[self._i]
        self._i += 1
        return r

    async def fetchall(self) -> list:
        rest = self._rows[self._i:]
        self._i = len(self._rows)
        return rest

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for r in self._rows:
            yield r


class Conn:
    """单个逻辑连接(从池里借)。事务边界与 aiosqlite 对齐:
    - 首条写语句惰性开启事务
    - commit()/rollback() 结束事务
    - close() 时未提交的事务回滚(与 SQLite 行为一致)
    - 裸 BEGIN/COMMIT/ROLLBACK 文本语句被拦截, 转为显式事务管理
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx = None              # asyncpg Transaction (手动 start/commit)
        self._tx_explicit = False    # 裸 BEGIN 开起来的事务(区别于惰性事务)
        self.row_factory = None      # aiosqlite 兼容: 收下但不做事(Record 本身就是 Row 语义)

    # -- 连接管理 --

    async def _ensure(self) -> asyncpg.Connection:
        if self._conn is None:
            self._conn = await self._pool.acquire()
        return self._conn

    async def _start_tx_if_needed(self):
        if self._tx is None and self._conn is not None:
            self._tx = self._conn.transaction()
            await self._tx.start()

    async def _finish_tx(self, commit: bool):
        if self._tx is not None:
            try:
                await (self._tx.commit() if commit else self._tx.rollback())
            finally:
                self._tx = None
                self._tx_explicit = False

    # -- 核心接口 --

    async def execute(self, sql, params=()) -> Cursor:
        pg = to_pg_sql(str(sql))
        args = _norm_params(tuple(params))

        m = _TX_CMD_RE.match(pg)
        if m:                                     # 裸事务语句: export_routes 用
            await self._ensure()
            cmd = m.group(1).upper()
            if cmd == "BEGIN":
                if self._tx is not None:
                    raise RuntimeError("事务已开启(nested BEGIN)")
                self._tx = self._conn.transaction()
                await self._tx.start()
                self._tx_explicit = True
            else:
                await self._finish_tx(commit=(cmd == "COMMIT"))
            return Cursor([], status=cmd)

        await self._ensure()
        head = pg.lstrip()[:8].upper()
        is_query = bool(_SELECT_RE.match(pg)) or " RETURNING " in pg.upper()

        if is_query:
            ps = await self._conn.prepare(pg)
            rows = await ps.fetch(*args)
            attrs = ps.get_attributes()
            desc = [(a.name, None, None, None, None, None, None) for a in attrs]
            lastid = rows[0][0] if (rows and " RETURNING " in pg.upper()) else None
            return Cursor(list(rows), status=f"SELECT {len(rows)}",
                          lastrowid=lastid, description=desc)

        if _WRITE_RE.match(pg):
            await self._start_tx_if_needed()
        status = await self._conn.execute(pg, *args)
        return Cursor([], status=status or "")

    async def executemany(self, sql, seq) -> Cursor:
        pg = to_pg_sql(str(sql))
        await self._ensure()
        if _WRITE_RE.match(pg):
            await self._start_tx_if_needed()
        rows = [tuple(_norm_params(tuple(p))) for p in seq]
        await self._conn.executemany(pg, rows)
        return Cursor([], status=f"EXECUTE MANY {len(rows)}")

    async def executescript(self, script: str):
        """按语句拆分执行(CREATE FUNCTION 的 $$ 体与字符串字面量里的分号不拆)。"""
        for stmt in _split_script(script):
            await self.execute(stmt)

    async def commit(self):
        await self._finish_tx(commit=True)

    async def rollback(self):
        await self._finish_tx(commit=False)

    async def close(self):
        # 与 SQLite 对齐: 没提交的事务在关闭时回滚
        try:
            await self._finish_tx(commit=False)
        finally:
            if self._conn is not None:
                await self._pool.release(self._conn)
                self._conn = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


def _split_script(script: str) -> list[str]:
    out, buf = [], []
    i, L = 0, len(script)
    in_s = in_d = in_line_comment = in_block = False
    dollar = False
    while i < L:
        c = script[i]
        nxt = script[i + 1] if i + 1 < L else ""
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
                buf.append(c)
            i += 1
            continue
        if in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_s:
            buf.append(c)
            if c == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 1
                else:
                    in_s = False
            i += 1
            continue
        if in_d:
            buf.append(c)
            if c == '"':
                in_d = False
            i += 1
            continue
        if dollar:
            buf.append(c)
            if c == "$" and nxt == "$":
                buf.append(nxt)
                dollar = False
                i += 1
            i += 1
            continue
        if c == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "$" and nxt == "$":
            dollar = True
            buf.append("$$")
            i += 1
            i += 1
            continue
        elif c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# ---- 连接池 ----

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _dbname_of(dsn: str) -> str:
    path = urlsplit(dsn).path.lstrip("/")
    return path.split("/")[0] or "postgres"


def _swap_db(dsn: str, dbname: str) -> str:
    parts = urlsplit(dsn)
    path = f"/{dbname}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


async def _create_database_if_missing(dsn: str):
    """目标库不存在(首次启动)时连到 postgres 库把它建出来。"""
    dbname = _dbname_of(dsn)
    admin = _swap_db(dsn, "postgres")
    conn = await asyncpg.connect(admin, timeout=10)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
            print(f"[db] 已创建 PostgreSQL 数据库: {dbname}")
    finally:
        await conn.close()


async def get_pool() -> asyncpg.Pool:
    global _pool
    async with _pool_lock:
        if _pool is not None and not _pool._closed:
            return _pool
        dsn = config.pg_dsn
        try:
            # min_size=0 的池创建时不建立真实连接, 库不存在要在这里就探出来
            probe = await asyncpg.connect(dsn, timeout=10)
            await probe.close()
        except asyncpg.exceptions.InvalidCatalogNameError:
            # 首次启动目标库还不存在: 连到 postgres 库把它建出来再重试
            await _create_database_if_missing(dsn)
        _pool = await asyncpg.create_pool(
            dsn, min_size=0, max_size=20,     # 12 个后台循环 + 并发请求, SQLite 时代无上限
            server_settings={"search_path": "ledger,public"},
        )
        return _pool


async def close_pool():
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


async def get_conn() -> Conn:
    """database.get_db() 与直连库 service 的统一入口。"""
    pool = await get_pool()
    return Conn(pool)
