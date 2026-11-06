import asyncio
from database import get_db


async def main():
    db = await get_db()
    try:
        t = await db.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%asset%'")
        print("asset tables:", [r[0] for r in await t.fetchall()])
        # 列出 external_assets 内容(若有)
        try:
            cur = await db.execute("SELECT id, asset_type, name, current_value, cost_amount, shares, pending_amount FROM external_assets ORDER BY id")
            rows = await cur.fetchall()
            print(f"external_assets rows: {len(rows)}")
            for r in rows:
                print(r)
        except Exception as e:
            print("no external_assets or err:", e)
    finally:
        await db.close()


asyncio.run(main())
