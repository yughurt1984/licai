# 底稿表 asset_type 改造 + 报价 PG 优先（提速资产页）

## 一、symbol_dict 底稿改造（is_etf → asset_type）

**is_etf 全库实质使用仅 3 处**（database DDL/load、stock_agent._code_name_maps 填 _etf_codes、stock-search 排序 ETF 沉底），改造点少；其余 9 处 `_is_etf_lof` 类正则是独立启发式，不动。

1. **DDL**：`symbol_dict(code PK, name, asset_type TEXT NOT NULL DEFAULT 'a_stock', updated_at)`。init_db 幂等迁移：`ADD COLUMN IF NOT EXISTS asset_type` → 按 is_etf 回填（1→'etf'，0→'a_stock'）→ `DROP COLUMN IF EXISTS is_etf`。asset_type 取值对齐 quote_latest：`a_stock / etf / hk_stock / us_stock / fut / opt / fund(场外) / crypto`。
2. **读写函数**：`load_symbol_dict` 返回 `[(code, name, asset_type)]`；`save_symbol_dict` 入参同形。`stock_agent._code_name_maps`：`asset_type in ('etf','fund') → _etf_codes`（搜股排序语义不变）。`/api/market/symbol-name` 返回加 `asset_type` 字段（向后兼容）。
3. **自动登记（核心，零联网）**：`market_store.save_quotes` 落库时顺带 upsert symbol_dict——symbol 规范化为裸码（A股/HK/US/基金剥前缀，期货用合约号），name 用报价里的名称，asset_type 直接用 quote 行的（场内 ETF 判定沿用 `_is_etf_lof` 正则 → 'etf'）。覆盖点：A/HK/US 实时报价、场外基金净值、加密币价、期货报价（_enrich FUT 分支补 write-through）、汇率不登（fx 不进底稿）。`ON CONFLICT (code) DO UPDATE`——你实际用到的标的随使用自动长全。
4. **全市场导入脚本** `scripts/import_symbol_dict.py`：akshare 拉 A股+ETF/港股/美股/期货合约/期权合约/场外基金 → 批量 upsert，支持 `--types a,hk,us,fut,opt,fund` 分批拉、结束时报各类型行数。**脚本由你自行择时执行**（单次联网任务，不影响应用启动）。
5. **schema.sql 重写干净版**：现有文件因重复生成堆积了 5 份旧内容，改为固定头 + 当前 SCHEMA 单份。

## 二、报价 PG 优先 + 后台刷新（资产页提速核心）

**前置修复**：`save_quotes` 目前把 `fetched_at` 显式写 NULL（覆盖了 DDL 默认），快照无法判新旧——改为 INSERT/冲突更新都写当前 UTC 时间；A 股行 quote_time 空的用当前北京时间兜底。

1. **`get_realtime_quotes(codes, force=False)` 改三段**：
   - **PG 快路径**：先 `get_stored_quotes(codes)`，按新鲜度判（盘中 age ≤ 60s 或 非交易时段 age ≤ 6h 算新）——**全部新鲜 → 纯 PG 返回（零外网，毫秒级）**；
   - **有旧快照但不新鲜** → 立即返回 PG 快照（不标 stale，前端无感）+ `spawn` 后台刷新远端（抓到后 write-through，下次即新）；
   - **无快照** → 现有远端路径（首次冷启动同现状），远端全挂回退 PG（现有逻辑保留）。
2. **`price_monitor_loop` 传 `force=True`**：它本身就是刷新器，绕过 PG 快路径保证 5s 实时推流不断（推的同时 write-through 让 PG 常新）。
3. **汇率去同步化**：`_store_rows_to_quotes` 的 fx_rate 改为优先读 quote_latest 的 fx 行（USDCNY/HKDCNY，已有 write-through），miss 才走 `get_fx_info`（内存 300s 缓存）——消除冷启动首屏卡事件循环的 5s 同步 HTTP。

**效果**：服务跑过一轮（或盘中开过页面）后，再开资产页的 `/api/portfolio` 报价段纯 PG 毫秒级返回；实时性由 WebSocket 5s 推送覆盖；远端行情源全挂也不影响打开页面。冷启动第一天无快照时退化为你现在看到的旧行为。

## 三、验证
- 临时库自检：迁移回填（is_etf→asset_type→drop）、自动登记（A/HK/US/FUT/fund/crypto 落库后底稿有行且类型正确）、PG 快路径（有新鲜快照时 get_realtime_quotes 不发外网——远端函数打桩计数为 0）、force 路径穿透、远端失败回退、旧库 is_etf 升级路径；
- eslint + build（前端无改动，symbol-name 加字段向后兼容）；
- 你重启验收：二开资产页对比速度、`python scripts/import_symbol_dict.py` 自行导入全市场、SQL 查 symbol_dict 行数。

## 本期不做
- 拼音索引扩到港股/美股（`_pinyin_rows` 仍只对 A股+ETF 名单生效，pypinyin 对英文 ticker 无意义）；
- 底稿的搜索排序权重分类型细化（沿用现有 ETF 沉底）。