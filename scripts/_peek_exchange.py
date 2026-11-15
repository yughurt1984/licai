import sys, asyncio
sys.path.insert(0, r"E:\projects\licai")
from dotenv import load_dotenv
load_dotenv(r"E:\projects\licai\config.env")
from database import get_db
from services.exchange_map import suffix_to_mic

async def main():
    db = await get_db()
    try:
        cur = await db.execute("SELECT sql FROM sqlite_master WHERE name='exchange'")
        print("=== exchange DDL ===")
        print((await cur.fetchone())[0])
        cur = await db.execute("PRAGMA table_info(exchange)")
        cols = [r[1] for r in await cur.fetchall()]
        print("\ncols:", cols)
        suffix_cols = [c for c in cols if "suffix" in c.lower()]
        print("suffix-like cols:", suffix_cols)

        # 期货后缀样本
        samps = ["SHF", "CFE", "CZC", "DCE", "GFE", "INE", "CME", "CBOT", "US", "SH", "SZ", "HK"]
        print("\n=== suffix_to_mic 实际结果 ===")
        for s in samps:
            print(f"  {s:5} -> {await suffix_to_mic(s)}")

        # 原始反查: 任一后缀列命中这些样本的行
        print("\n=== 后缀列命中样本(原始) ===")
        for s in ["SHF", "CFE", "CZC", "DCE", "GFE", "INE"]:
            where = " OR ".join(f"{c}=?" for c in suffix_cols)
            cur = await db.execute(
                f"SELECT mic, {', '.join(suffix_cols)} FROM exchange WHERE {where}",
                [s] * len(suffix_cols))
            rows = await cur.fetchall()
            print(f"\n  suffix={s}: 命中 {len(rows)} 行")
            for r in rows:
                d = dict(zip(["mic"] + suffix_cols, r))
                print("   ", {k: d[k] for k in d if d[k]})
    finally:
        await db.close()

asyncio.run(main())
