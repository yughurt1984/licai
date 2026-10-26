"""数据库测试夹具: SQLite 时代每个测试指向一个临时 .db 文件, PG 时代统一指向测试库
licai_test(每个测试开始前把 ledger/market schema 整个 DROP 重建, 等效于换一个空库)。

为什么测试库叫 licai_test: config.pg_dsn 指向的库(通常是 licai)留着给真实数据,
测试要一个"随时可以整库砸掉重建"的地方 —— 断在半截的测试会留下脏数据, 绝不能落在
真库上。默认从 config.pg_dsn 推导(库名 + _test 后缀); 想换一个库/实例时设环境变量:

    set PG_DSN_TEST=postgresql://postgres:postgres@127.0.0.1:5432/licai_unittest

或者直接换整条基础连接(PG_DSN), 测试库会跟着推导成 <库名>_test。PG_DSN 也没设时,
兜底用 pgserver 包在本机拉一个一次性实例(scripts/.tmp_pgdata), 让 `python -m pytest tests/`
开箱即跑。

实现上有两个 pytest/asyncio 相关的坑在这里统一处理(测试代码不用关心):

1. 本仓没装 pytest-asyncio, 测试的写法是每步操作 asyncio.run(...) 包一层 —— 每次
   asyncio.run 都是一个**全新的事件循环**, 而 db.py 的连接池是模块级缓存且 asyncpg
   连接绑定创建它的那个循环。所以这里全局包了一层 asyncio.run: 每次跑完先把池关掉,
   下一次调用重建; 同时给 db.get_pool 打了补丁, 发现池属于已关闭的旧循环(比如
   TestClient 的 per-request portal 循环)就丢弃重建, 并在丢弃前先 terminate 拿不回
   的连接 —— graceful close 遇到"连接没还"的异常路径会挂死, terminate 不会。
2. temp_db fixture 的收尾一定走 db.close_pool(改 DSN 前必须先关池), 避免上一个测试
   的池被拿到新 DSN 上复用。
"""
import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

_ROOT = Path(__file__).resolve().parents[1]


# ── asyncio.run / 连接池的循环绑定补丁 ─────────────────────

def _discard_pool(pool) -> None:
    """同步丢弃一个池。terminate 立即掐断所有连接(不等待归还), 不会挂死。"""
    import db as dbm
    try:
        pool.terminate()
    except Exception:
        pass          # 连接属于已关闭的循环时 terminate 也可能抛; GC 会兜底
    dbm._pool = None


async def _teardown_pool() -> None:
    """收尾当前池: 属于当前循环 → graceful close; 否则(旧循环的残池)→ terminate。"""
    import db as dbm
    global _bound_pool
    pool = dbm._pool
    if pool is None:
        return
    if _bound_pool is not None and _bound_pool[0] is pool \
            and _bound_pool[1] is not asyncio.get_running_loop():
        _discard_pool(pool)
        return
    try:
        await asyncio.wait_for(dbm.close_pool(), timeout=5)
    except Exception:
        p = dbm._pool
        if p is not None:
            _discard_pool(p)


if not getattr(asyncio, "_licai_test_pool_guard", False):
    _real_asyncio_run = asyncio.run
    _bound_pool = None            # (pool, 创建它的 loop)

    async def _loop_aware_get_pool():
        """db.get_pool 的补丁: 缓存池若属于另一个(已关闭的)循环, 先丢弃再重建。"""
        global _bound_pool
        import db as dbm
        pool = dbm._pool
        if pool is not None and _bound_pool is not None and _bound_pool[0] is pool \
                and _bound_pool[1] is not asyncio.get_running_loop():
            _discard_pool(pool)
        pool = await dbm._orig_get_pool()
        _bound_pool = (pool, asyncio.get_running_loop())
        return pool

    def _run_then_close_pool(main, **kwargs):
        """asyncio.run 的补丁: 跑完后把连接池关掉 —— 池和连接都绑定创建时的循环,
        而 asyncio.run 每次都是新循环, 不关的话下一次调用会撞上死循环里的连接。"""
        async def _wrapped():
            try:
                return await main
            finally:
                try:
                    await _teardown_pool()
                except Exception:
                    pass
        return _real_asyncio_run(_wrapped(), **kwargs)

    import db as dbm
    dbm._orig_get_pool = dbm.get_pool
    dbm.get_pool = _loop_aware_get_pool
    asyncio.run = _run_then_close_pool
    asyncio._licai_test_pool_guard = True


# ── 测试库 DSN 与清库 ──────────────────────────────────────

def _test_dsn() -> str:
    """测试库连接串。优先 config.pg_dsn_test(PG_DSN_TEST), 其次由 config.pg_dsn 推导
    (<库名>_test, 同实例的另一个库 —— 绝不碰真实账本库)。"""
    from config import config
    if config.pg_dsn_test:
        return config.pg_dsn_test
    parts = urlsplit(config.pg_dsn)
    dbname = parts.path.lstrip("/").split("/")[0] or "postgres"
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}_test",
                       parts.query, parts.fragment))


async def _ensure_db(dsn: str) -> None:
    """测试库不存在(首次跑)时建出来, 与 db.py 首启逻辑一致。"""
    import asyncpg
    parts = urlsplit(dsn)
    dbname = parts.path.lstrip("/").split("/")[0] or "postgres"
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))
    conn = await asyncpg.connect(admin, timeout=10)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()


async def _wipe_schemas(dsn: str) -> None:
    """整库砸掉重建: 把 ledger/market 两个 schema 和 public 一起 DROP, 等效于一个全新的
    空库(init_db 之后所有表都在干净的 public/ledger 里)。比逐表 TRUNCATE 诚实 ——
    半截测试留下的任何表/函数/脏行都活不过下一次 setup。"""
    import asyncpg
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS ledger CASCADE")
        await conn.execute("DROP SCHEMA IF EXISTS market CASCADE")
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
    finally:
        await conn.close()


def _reset_service_caches() -> None:
    """rule_forge / user_memory 的 active 条目各有一份模块级内存缓存(PG 时代同步读库
    改成的缓存)。换库之后旧缓存里的条目一律作废, 免得泄漏进下一个测试。"""
    try:
        from services import rule_forge as rf
        rf._RULES_CACHE = []
    except Exception:
        pass
    try:
        from services import user_memory as um
        um._ACTIVE_CACHE = []
    except Exception:
        pass


@pytest.fixture
def temp_db(monkeypatch):
    """指向全新重建的测试库。yield 的是测试 DSN(旧 fixture yield 的是文件路径 ——
    现在没有"文件"了, 需要插桩的测试走 seed_rows / database 函数, 别拿它当路径用)。"""
    from database import init_db
    dsn = _test_dsn()
    asyncio.run(_teardown_pool())          # 改 DSN 前先关掉旧池, 避免跨 DSN 复用
    monkeypatch.setattr("config.config.pg_dsn", dsn)
    asyncio.run(_ensure_db(dsn))
    asyncio.run(_wipe_schemas(dsn))
    asyncio.run(init_db())
    _reset_service_caches()
    yield dsn
    # 正常路径就是 db.close_pool(); 若池属于上一个测试留下的死循环(TestClient 的
    # portal), graceful close 会撞死循环里的 transport, 那种残池走 terminate 丢弃。
    asyncio.run(_teardown_pool())


@pytest.fixture
def wf_db(temp_db):
    """test_rule_forge 历史用的名字, 与 temp_db 完全等价。"""
    return temp_db


async def insert_rows(table: str, rows: list) -> None:
    """把 dict 行插进 table(? 占位符走 db.get_conn, 与生产同一套兼容层)。"""
    from db import get_conn
    conn = await get_conn()
    try:
        for row in rows:
            cols = list(row.keys())
            ph = ", ".join("?" for _ in cols)
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})"
            await conn.execute(sql, tuple(row[c] for c in cols))
        await conn.commit()
    finally:
        await conn.close()


@pytest.fixture
def seed_rows(temp_db):
    """异步行插入 helper: await seed_rows("kline_cache", [{...}, ...])。
    供测试函数内部直接插桩(替代旧 sqlite3.connect 的 INSERT); 模块级 seed helper
    用 asyncio.run 包它。"""
    return insert_rows
