"""数据导出/导入: 全量 JSON 备份, 用于换设备 / 还原.

策略:
- 导出: 把用户数据表 dump 成 JSON, 跳过 kline_cache (可从网络重建).
- 导入: 先把当前库 pg_dump 一份到 backups/ 再覆盖. 失败可手动还原.
- 上传走 multipart/form-data, 体积无上限 (本地用).
"""
from __future__ import annotations
import json
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from config import config
from database import get_db
from services.context import current_user_id


router = APIRouter(prefix="/api/data", tags=["data"])


# 哪些表要进 JSON dump (SQLite 时代只导了 11 张, 漏了自选/逻辑/定投/快照等, 这里补齐).
# 多用户: 全部按**当前用户**过滤/归属 —— 导出只含本人数据行(不含 user_id 列), 导入写入本人.
# 无外键, 导入顺序只按业务习惯排. kline_cache 不导出 (可从网络重建, 共享).
_TABLES = [
    "holdings",
    "position_actions",
    "external_assets",
    "external_asset_actions",
    "unwind_plans",
    "unwind_tranches",
    "custom_alerts",
    "alert_config",
    "cashflow_monthly",
    "morning_briefings",
    "portfolio_snapshots",
    "watchlist",
    "watchlist_group",
    "position_thesis",
    "thesis_revision",
    "dca_schedules",
    "trade_plan",
    "user_memory",
    "prompt_rule",
    "app_config",
]

EXPORT_VERSION = 3


async def _table_columns(db, table: str) -> list[str]:
    cur = await db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'ledger' AND table_name = ? ORDER BY ordinal_position",
        (table,))
    return [r[0] for r in await cur.fetchall()]


async def _table_pk(db, table: str) -> list[str]:
    """主键列(建表时都定义了 PK)。merge 模式的 upsert 冲突目标用。"""
    cur = await db.execute(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.table_schema = 'ledger' AND tc.table_name = ? AND tc.constraint_type = 'PRIMARY KEY' "
        "ORDER BY kcu.ordinal_position",
        (table,))
    return [r[0] for r in await cur.fetchall()]


@router.get("/export")
async def export_all():
    """全量导出. 返回 JSON 文件下载."""
    db = await get_db()
    out: dict = {
        "_meta": {
            "version": EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "app": "licai",
        },
        "tables": {},
    }
    uid = current_user_id()
    try:
        for t in _TABLES:
            try:
                cursor = await db.execute(f"SELECT * FROM {t} WHERE user_id = ?", (uid,))
                rows = await cursor.fetchall()
                out["tables"][t] = [dict(r) for r in rows]
            except Exception as e:
                # 表不存在时跳过 (老备份文件回灌新代码不会走到这; 新表缺列才可能)
                print(f"[export] skip {t}: {e}")
                out["tables"][t] = []
    finally:
        await db.close()

    body = json.dumps(out, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    fname = f"licai-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Export-Version": str(EXPORT_VERSION),
        },
    )


def _pg_dump_path() -> str | None:
    """找 pg_dump: PATH 里没有就试 Windows 常见安装位置。"""
    import shutil
    found = shutil.which("pg_dump")
    if found:
        return found
    for base in (r"D:\Program Files\PostgreSQL", r"C:\Program Files\PostgreSQL"):
        p = Path(base)
        if not p.is_dir():
            continue
        for v in sorted(p.iterdir(), reverse=True):
            cand = v / "bin" / "pg_dump.exe"
            if cand.exists():
                return str(cand)
    return None


def _backup_current_db() -> str | None:
    """导入前先 pg_dump 一份到 backups/, 失败时可手动还原。

    pg_dump 找不到(或失败)时返回 None —— 导入照常进行, 但要提示没有备份。
    """
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"pre-import-{ts}.sql"
    pg_dump = _pg_dump_path()
    if not pg_dump:
        print("[import] 警告: 找不到 pg_dump, 跳过导入前备份")
        return None
    cmd = [pg_dump, "--dbname", config.pg_dsn, "--file", str(dst), "--schema", "ledger"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"[import] pg_dump 失败, 跳过备份: {(r.stderr or '')[:200]}")
            return None
        return dst
    except Exception as e:
        print(f"[import] pg_dump 异常, 跳过备份: {e}")
        return None


@router.post("/import")
async def import_all(file: UploadFile = File(...), mode: str = "replace"):
    """从上传 JSON 还原. mode=replace 清空表后导入; mode=merge 仅 upsert (有主键的表)."""
    if mode not in ("replace", "merge"):
        raise HTTPException(400, "mode 必须是 replace 或 merge")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 解析失败: {e}")

    if not isinstance(payload, dict) or "tables" not in payload:
        raise HTTPException(400, "格式错误: 缺少 tables 字段")
    meta = payload.get("_meta", {})
    if int(meta.get("version", 0)) > EXPORT_VERSION:
        raise HTTPException(400, f"备份文件版本 {meta.get('version')} 比当前应用 ({EXPORT_VERSION}) 还新, 不兼容")

    backup_path = _backup_current_db()
    backup_name = backup_path.name if backup_path else "(无, pg_dump 不可用)"

    db = await get_db()
    summary: dict[str, int] = {}
    try:
        await db.execute("BEGIN")
        for t in _TABLES:
            rows = payload["tables"].get(t)
            if rows is None:
                continue
            cols = await _table_columns(db, t)
            if not cols:
                continue  # 表不存在
            # 过滤备份里多余的字段 (新版备份导回老代码); user_id 不信备份文件, 一律写当前用户
            valid_rows = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                row = {k: v for k, v in r.items() if k in cols and k != "user_id"}
                if "user_id" in cols:
                    row["user_id"] = current_user_id()
                valid_rows.append(row)
            pk = await _table_pk(db, t)
            if mode == "replace":
                await db.execute(f"DELETE FROM {t}")
            inserted = 0
            for r in valid_rows:
                if not r:
                    continue
                keys = list(r.keys())
                placeholders = ",".join(["?"] * len(keys))
                col_list = ",".join(keys)
                values = [r[k] for k in keys]
                if mode == "merge" and pk:
                    pk_in_row = [c for c in pk if c in r]
                    if not pk_in_row:
                        continue
                    updates = ",".join(f"{c} = excluded.{c}" for c in keys if c not in pk_in_row)
                    conflict = f"ON CONFLICT ({','.join(pk_in_row)})"
                    sql = (f"INSERT INTO {t} ({col_list}) VALUES ({placeholders}) {conflict} "
                           + (f"DO UPDATE SET {updates}" if updates else "DO NOTHING"))
                else:
                    sql = f"INSERT INTO {t} ({col_list}) VALUES ({placeholders})"
                try:
                    await db.execute(sql, values)
                    inserted += 1
                except Exception as e:
                    print(f"[import] skip row in {t}: {e}")
            summary[t] = inserted
            # 显式导入的 id 不会推进序列, 复位一下, 否则后续新插入撞主键
            if "id" in cols:
                try:
                    await db.execute(
                        f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                        f"GREATEST((SELECT COALESCE(MAX(id), 1) FROM {t}), 1))")
                except Exception:
                    pass
        await db.execute("COMMIT")
    except Exception as e:
        try: await db.execute("ROLLBACK")
        except Exception: pass
        raise HTTPException(500, f"导入失败 (已自动备份至 {backup_name}, 可手动还原): {e}")
    finally:
        await db.close()

    return {
        "message": "导入完成",
        "mode": mode,
        "pre_import_backup": backup_name,
        "imported": summary,
    }
