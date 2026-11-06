"""日期列真类型化: SCHEMA 列型(DATE/TIMESTAMP) + db.py 文本 codec 读写往返 + 老库 TEXT 列对齐。

背景: purchase_date/created_at/updated_at/quote_time/fetched_at 从 TEXT 改为真类型
(北京时间墙钟), 应用层字符串契约由 db.py 的类型 codec 保障 —— 这里钉住三条:
列型是对的、字符串进出不变、TEXT 老列启动时被幂等 ALTER。
"""
import asyncio

# temp_db 由 tests/conftest.py 提供(指向每次重建的 PG 测试库)

_IN_SCOPE = ("purchase_date", "created_at", "updated_at", "quote_time", "fetched_at")


def _col_types(dsn: str, table: str) -> dict:
    import asyncpg

    async def _q():
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            rows = await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = $1", table)
            return {r["column_name"]: r["data_type"] for r in rows}
        finally:
            await conn.close()
    return asyncio.run(_q())


def test_schema_uses_real_date_types(temp_db):
    types = _col_types(temp_db, "holdings")
    assert types["purchase_date"] == "date"
    assert types["created_at"] == "timestamp without time zone"
    assert types["updated_at"] == "timestamp without time zone"
    ql = _col_types(temp_db, "quote_latest")
    assert ql["quote_time"] == "timestamp without time zone"
    assert ql["fetched_at"] == "timestamp without time zone"
    # 范围外的时间列保持 TEXT
    assert _col_types(temp_db, "position_actions")["trade_time"] in ("text", "character varying")


def test_codec_roundtrip_string_contract(temp_db):
    """应用层字符串进出: 写 str('YYYY-MM-DD') → 读回同形态文本, created_at 默认值非空。"""
    from database import add_holding, get_holding, add_position_action

    asyncio.run(add_holding("600406.SH", "三友化工", 1000, 8.5, purchase_date="2026-03-14"))
    h = asyncio.run(get_holding("600406.SH"))
    assert h["purchase_date"] == "2026-03-14"
    assert isinstance(h["created_at"], str) and len(h["created_at"]) == 19   # 'YYYY-MM-DD HH:MM:SS'
    assert h["created_at"][:4].isdigit() and h["created_at"][4] == "-"
    assert isinstance(h["updated_at"], str) and len(h["updated_at"]) == 19

    # 时间戳列显式传字符串(兼容层 codec 放行)
    from tests.conftest import insert_rows
    asyncio.run(insert_rows("position_actions", [{
        "code": "600406.SH", "ticker": "600406", "action_type": "OPEN",
        "price": 8.5, "shares": 1000,
        "created_at": "2026-03-14 09:31:00",
    }]))
    from database import get_position_actions_until
    actions = asyncio.run(get_position_actions_until("2026-03-15"))["600406.SH"]
    assert str(actions[0]["created_at"])[:10] == "2026-03-14"      # left(created_at::text,10) 语义
    # resolve_action_time: 北京时间不再 +8h
    from database import resolve_action_time
    assert resolve_action_time(actions[0]) == "09:31:00"


def test_align_date_column_types_migrates_text(temp_db):
    """TEXT 老列(含脏空串)启动时被幂等 ALTER 成真类型, 数据保留。"""
    import db as dbm

    async def _setup():
        conn = await dbm.get_conn()
        try:
            # 把 holdings.created_at 退回 TEXT 并塞一行老格式数据(空串在 quote_time)
            await conn.execute("ALTER TABLE holdings ALTER COLUMN created_at TYPE text "
                               "USING created_at::text")
            await conn.execute("ALTER TABLE market.quote_latest ALTER COLUMN fetched_at TYPE text "
                               "USING fetched_at::text")
            await conn.execute(
                "INSERT INTO holdings (code, name, created_at) VALUES ('TEST1.TI', '迁移样例', '2025-06-01 08:00:00')")
            await conn.commit()
        finally:
            await conn.close()

    async def _align():
        conn = await dbm.get_conn()
        try:
            from database import _align_date_column_types
            await _align_date_column_types(conn)
        finally:
            await conn.close()

    asyncio.run(_setup())
    asyncio.run(_align())
    types = _col_types(temp_db, "holdings")
    assert types["created_at"] == "timestamp without time zone"
    assert _col_types(temp_db, "quote_latest")["fetched_at"] == "timestamp without time zone"
    # 数据还在且形态一致(空串行不炸迁移)
    from database import get_db
    async def _read():
        conn = await get_db()
        try:
            cur = await conn.execute("SELECT code, created_at::text FROM holdings WHERE code = 'TEST1.TI'")
            return await cur.fetchone()
        finally:
            await conn.close()
    row = asyncio.run(_read())
    assert row[0] == "TEST1.TI" and row[1] == "2025-06-01 08:00:00"


def test_market_store_quote_time_datetime_write(temp_db):
    """market_store(直连池, 不经 db.py codec): 字符串时点被解析成 datetime 落库, 读回 datetime。"""
    from datetime import datetime
    from services import market_store

    async def _flow():
        n = await market_store.save_quotes([{
            "code": "HK.00700", "ticker": "00700", "sub_assets_type": "hk_comm_stock",
            "name": "腾讯控股", "price": 500.0, "currency": "HKD", "data_source": "futu",
            "quote_time": "2026-11-05 10:00",
        }])
        rows = await market_store.get_stored_quotes(["HK.00700"])
        await market_store.close_pool()    # 池绑定创建它的循环, 在同一循环内关闭
        return n, rows

    n, rows = asyncio.run(_flow())
    assert n == 1
    r = rows["HK.00700"]
    assert isinstance(r["fetched_at"], datetime)
    assert r["quote_time"] == datetime(2026, 11, 5, 10, 0)
