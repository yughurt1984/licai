"""观察池分组: 一票多组 + 从老的单值列迁过来。

「自选」是一个真实分组, 跟「金矿」平级 —— 一只票可以同时在「自选」和「金矿」里。
它只有两处特殊: 加进观察池时默认落它, 以及取消到一个组都不剩时退回它(不然这只票哪个
分组视图都进不去, 只能在「全部」里看到)。

为什么必须测迁移: 老库里分组是 watchlist.grp 一列, 搬到 watchlist_group 关联表之后
老列要删掉 —— 不删就是第二本账, 读到的永远是搬迁那一刻的快照。而删列这件事只在真实
老库上跑一次, 跑错了就是用户手工归的那些组全丢。

组内位次也一并搬: 每个组各有一份顺序, 挂在 watchlist 上时一只票只能有一个位次, 进
第二个组必然跟第一个组抢。
"""
import asyncio

import pytest

from tests.conftest import insert_rows

# temp_db 由 tests/conftest.py 提供(指向每次清空重建的 PG 测试库)


def _init():
    from database import init_db
    asyncio.run(init_db())


def _cols(table="watchlist"):
    """列名集合(information_schema 直查, 替代旧 PRAGMA table_info)。"""
    async def q():
        from database import get_db
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? AND table_schema IN ('ledger', 'public')", (table,))
            return {r[0] for r in await cur.fetchall()}
        finally:
            await db.close()
    return asyncio.run(q())


def _add(code, name="X", price=10.0):
    from database import add_watchlist
    asyncio.run(add_watchlist(code, name, price))


def _rows():
    from database import list_watchlist
    return {r["code"]: r for r in asyncio.run(list_watchlist())}


def _set(code, groups):
    from database import set_watchlist_groups
    return asyncio.run(set_watchlist_groups(code, groups))


# ── 一票多组 ────────────────────────────────────────────

def test_one_stock_in_several_groups(temp_db):
    _init()
    _add("600519")
    _set("600519", ["高端内存", "博弈"])
    assert _rows()["600519"]["groups"] == ["博弈", "高端内存"]     # 按组名排, 稳定


def test_groups_are_deduped_and_trimmed(temp_db):
    _init()
    _add("600519")
    assert _set("600519", ["  金矿 ", "金矿", "", "  "]) == ["金矿"]
    assert _rows()["600519"]["groups"] == ["金矿"]


def test_empty_list_falls_back_to_default_group(temp_db):
    """清空分组 = 退回默认组「自选」, 不是"一个组都不在"。"""
    _init()
    _add("600519")
    _set("600519", ["金矿"])                                # 加进来时本来就在「自选」
    _set("600519", [])
    assert _rows()["600519"]["groups"] == ["自选"]


def test_default_group_coexists_with_others(temp_db):
    """核心诉求: 同时在「自选」和某个分组里。勾了「金矿」不该把「自选」顶掉。"""
    _init()
    _add("600519")
    assert _rows()["600519"]["groups"] == ["自选"]           # ☆ 加入 = 落默认组
    _set("600519", ["自选", "金矿"])
    assert sorted(_rows()["600519"]["groups"]) == sorted(["自选", "金矿"])


def test_default_group_can_be_dropped_when_others_remain(temp_db):
    """只想留「金矿」也行 —— 「自选」不是强制的, 只是默认的。"""
    _init()
    _add("600519")
    _set("600519", ["金矿"])
    assert _rows()["600519"]["groups"] == ["金矿"]
    _init()                                           # 再启动一次也不给塞回来
    assert _rows()["600519"]["groups"] == ["金矿"]


def test_adding_a_group_keeps_position_in_the_old_one(temp_db):
    """勾第二个组不能让这只票在原来那个组里挪到末尾 —— 已在的组要保留原位次。"""
    _init()
    for c in ("000001", "000002", "000003"):
        _add(c)
        _set(c, ["金矿"])
    before = _rows()["000001"]["group_orders"]["金矿"]
    _set("000001", ["金矿", "博弈"])
    after = _rows()["000001"]
    assert after["group_orders"]["金矿"] == before
    assert "博弈" in after["group_orders"]
    # 组内顺序也没变: 000001 仍在金矿组第一个
    r = _rows()
    assert r["000001"]["group_orders"]["金矿"] < r["000002"]["group_orders"]["金矿"]


def test_new_group_membership_goes_to_the_end_of_that_group(temp_db):
    _init()
    for c in ("000001", "000002"):
        _add(c)
        _set(c, ["金矿"])
    _add("000003")
    _set("000003", ["金矿"])
    orders = {c: _rows()[c]["group_orders"]["金矿"] for c in ("000001", "000002", "000003")}
    assert orders["000003"] > orders["000002"] > orders["000001"]


def test_group_order_is_per_group(temp_db):
    """同一只票在两个组里各有一份位次 —— 在 A 组调顺序不该动它在 B 组的位置。"""
    from database import reorder_watchlist
    _init()
    for c in ("000001", "000002"):
        _add(c)
        _set(c, ["甲", "乙"])
    asyncio.run(reorder_watchlist(["000002", "000001"], scope="group", group="甲"))
    r = _rows()
    assert r["000002"]["group_orders"]["甲"] < r["000001"]["group_orders"]["甲"]
    # 乙组不受影响: 还是先加的在前
    assert r["000001"]["group_orders"]["乙"] < r["000002"]["group_orders"]["乙"]


def test_reorder_in_group_adds_membership_without_touching_others(temp_db):
    """拖进某个组 = 入组, 但它在别的组里待着不受影响。"""
    from database import reorder_watchlist
    _init()
    _add("000001")
    _set("000001", ["甲"])
    asyncio.run(reorder_watchlist(["000001"], scope="group", group="乙"))
    assert sorted(_rows()["000001"]["groups"]) == sorted(["甲", "乙"])


def test_reorder_global_does_not_touch_groups(temp_db):
    from database import reorder_watchlist
    _init()
    for c in ("000001", "000002"):
        _add(c)
    _set("000001", ["甲"])
    asyncio.run(reorder_watchlist(["000002", "000001"], scope="global"))
    r = _rows()
    assert r["000002"]["sort_order"] < r["000001"]["sort_order"]
    assert r["000001"]["groups"] == ["甲"] and r["000002"]["groups"] == ["自选"]


def test_reorder_with_blank_group_name_is_a_noop(temp_db):
    """组名为空是脏入参(默认组叫「自选」), 别给它凭空造出一行空组名。"""
    from database import reorder_watchlist
    _init()
    _add("000001")
    asyncio.run(reorder_watchlist(["000001"], scope="group", group=""))
    assert _rows()["000001"]["groups"] == ["自选"]


def test_reorder_in_default_group_writes_its_own_order(temp_db):
    """「自选」是真实分组, 有自己那套组内位次 —— 在它里面调顺序不该动全局位次。"""
    from database import reorder_watchlist
    _init()
    for c in ("000001", "000002"):
        _add(c)
    before = {c: _rows()[c]["sort_order"] for c in ("000001", "000002")}
    asyncio.run(reorder_watchlist(["000002", "000001"], scope="group", group="自选"))
    r = _rows()
    assert r["000002"]["group_orders"]["自选"] < r["000001"]["group_orders"]["自选"]
    assert {c: r[c]["sort_order"] for c in ("000001", "000002")} == before


def test_removing_from_watchlist_drops_group_rows(temp_db):
    """不删成员行的话, 这只票再加回观察池会连着旧分组一起复活。"""
    from database import remove_watchlist
    _init()
    _add("600519")
    _set("600519", ["金矿"])
    asyncio.run(remove_watchlist("600519"))
    _add("600519")
    assert _rows()["600519"]["groups"] == ["自选"]          # 只剩默认组, 金矿没复活


# ── 从老的单值列迁移 ────────────────────────────────────

def _legacy_db(rows):
    """按改造前的形态插数据: 老库里分组是 watchlist.grp/group_order 两列。PG 里分组已
    拆到 watchlist_group 关联表 —— 等价的"老数据"是: watchlist 有行, 部分票带着旧组行
    (组内位次一起搬); 还没归组的票交给 init_db 的一次性闸门补进默认组。
    conftest 的 temp_db 建库时已经跑过一次 init_db(闸门键已置位), 这里把键清掉,
    模拟"老库还没跑过新代码"。"""
    wl_rows, grp_rows = [], []
    for code, name, grp, sort_order, group_order in rows:
        wl_rows.append({"stock_code": code, "stock_name": name, "sort_order": sort_order})
        if grp:
            grp_rows.append({"stock_code": code, "grp": grp, "group_order": group_order})
    asyncio.run(insert_rows("watchlist", wl_rows))
    if grp_rows:
        asyncio.run(insert_rows("watchlist_group", grp_rows))

    async def _unset_gate():
        from database import get_db
        db = await get_db()
        try:
            await db.execute("DELETE FROM app_config WHERE key = 'watchlist_default_group_filled'")
            await db.commit()
        finally:
            await db.close()
    asyncio.run(_unset_gate())


def test_migration_moves_groups_and_drops_the_old_columns(temp_db):
    _legacy_db([("601728", "中国电信", "高端内存", 0, 5),
                ("600489", "中金黄金", "金矿", 1, 2),
                ("000001", "平安银行", "", 2, 0)])
    _init()
    r = _rows()
    assert r["601728"]["groups"] == ["高端内存"]
    assert r["601728"]["group_orders"]["高端内存"] == 5      # 组内位次一起搬, 顺序不重置
    assert r["600489"]["groups"] == ["金矿"]
    assert r["000001"]["groups"] == ["自选"]                 # 老的空串 = 补进默认组
    assert r["000001"]["sort_order"] == 2                    # 全局位次原样保留
    # 老列必须没了: 留着就是第二本账(PG 版 watchlist 从一开始就不带 grp/group_order)
    assert "grp" not in _cols() and "group_order" not in _cols()
    assert "sort_order" in _cols()


def test_migration_does_not_resurrect_a_group_the_user_removed(temp_db):
    """搬完再启动一次不能照着老列重搬 —— 那会把用户后来移出的分组复活。"""
    _legacy_db([("601728", "中国电信", "高端内存", 0, 0)])
    _init()
    _set("601728", ["自选"])                # 用户把它移出高端内存
    _init()                                # 再启动一次
    assert _rows()["601728"]["groups"] == ["自选"]


def test_migration_is_idempotent_on_a_new_db(temp_db):
    _init()
    _add("600519")
    _set("600519", ["金矿", "博弈"])
    _init()
    assert _rows()["600519"]["groups"] == ["博弈", "金矿"]
