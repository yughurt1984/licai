"""迁移 market.quote_latest 到新结构(幂等, 可重复执行):

  1. 数值列 DOUBLE PRECISION -> numeric(18,6)
  2. 改名 symbol -> code(主键), source -> data_source
  3. 删除 asset_type
  4. 新增 ticker(NOT NULL, 由 code 反推) / sub_assets_type(由 symbol_dict 关联)
  5. 回填 name(行情源没写名字的, 用 symbol_dict 补)

用法:
  cd e:/projects/licai
  python scripts/migrate_quote_latest.py

建议执行前对 market.quote_latest 做一次备份:
  pg_dump ... -t market.quote_latest > quote_latest.bak.sql
"""
import asyncio

from database import get_pool

NUM_COLS = ["price", "open", "high", "low", "prev_close",
            "volume", "amount", "change_pct", "turnover_rate"]


async def _col_exists(conn, col: str) -> bool:
    return await conn.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='market' AND table_name='quote_latest' AND column_name=$1",
        col) is not None


async def _col_type(conn, col: str) -> str | None:
    return await conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='market' AND table_name='quote_latest' AND column_name=$1",
        col)


async def main() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1) 数值列类型转换(仅当还是 double precision 时)
        for c in NUM_COLS:
            if await _col_exists(conn, c) and (await _col_type(conn, c)) == "double precision":
                await conn.execute(
                    f"ALTER TABLE market.quote_latest ALTER COLUMN {c} "
                    f"TYPE numeric(18,6) USING ({c}::numeric(18,6))")

        # 2) 改名(幂等: 仅当旧列存在)
        if await _col_exists(conn, "symbol"):
            await conn.execute("ALTER TABLE market.quote_latest RENAME COLUMN symbol TO code")
        if await _col_exists(conn, "source"):
            await conn.execute("ALTER TABLE market.quote_latest RENAME COLUMN source TO data_source")

        # 3) 删除 asset_type
        if await _col_exists(conn, "asset_type"):
            await conn.execute("ALTER TABLE market.quote_latest DROP COLUMN asset_type")

        # 4a) 新增 ticker(先允许 NULL 回填, 再设 NOT NULL)
        if not await _col_exists(conn, "ticker"):
            await conn.execute("ALTER TABLE market.quote_latest ADD COLUMN ticker TEXT")
        await conn.execute("""
            UPDATE market.quote_latest
               SET ticker = CASE
                   WHEN code LIKE 'HK.%' THEN substring(code from 4)
                   WHEN code LIKE 'US.%' THEN substring(code from 4)
                   WHEN code LIKE 'fund_%' THEN substring(code from 6)
                   ELSE split_part(code, '.', 1)
               END
             WHERE ticker IS NULL OR ticker = ''
        """)
        await conn.execute("ALTER TABLE market.quote_latest ALTER COLUMN ticker SET NOT NULL")

        # 4b) 新增 sub_assets_type, 由 symbol_dict 关联回填
        if not await _col_exists(conn, "sub_assets_type"):
            await conn.execute("ALTER TABLE market.quote_latest ADD COLUMN sub_assets_type TEXT")
        await conn.execute("""
            UPDATE market.quote_latest q
               SET sub_assets_type = s.sub_assets_type
              FROM ledger.symbol_dict s
             WHERE q.code = s.code
               AND (q.sub_assets_type IS NULL OR q.sub_assets_type = '')
               AND s.sub_assets_type IS NOT NULL AND s.sub_assets_type <> ''
        """)

        # 5) 回填 name(用户反馈 name 为空)
        await conn.execute("""
            UPDATE market.quote_latest q
               SET name = s.name
              FROM ledger.symbol_dict s
             WHERE q.code = s.code
               AND (q.name IS NULL OR q.name = '')
               AND s.name IS NOT NULL AND s.name <> ''
        """)

    await pool.close()
    print("[migrate] market.quote_latest 迁移完成")


if __name__ == "__main__":
    asyncio.run(main())
