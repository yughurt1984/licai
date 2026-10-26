# 多用户模式改造方案（修订：无存量数据认领）

## 目标架构（已确认：开放注册 / 个人设置完整隔离 / **存量数据不认领，开发完成后你自行清空测试数据**）

```
浏览器 ── cookie(licai_session) ──> FastAPI 鉴权中间件（/api/* 白名单外全部校验）
                                       │ 校验通过: 注入当前 user_id 到 ContextVar
                                       ▼
              api/* 路由 → services/* → database.py（每条用户数据 SQL 自动带 user_id 过滤）
                                       │
        后台循环(12个) ── run_as_user(uid) 逐用户包裹 ──┘
                                       │
PostgreSQL:  ledger.users / ledger.sessions
             用户表(user_id 列) + 共享表(行情/档案/字典, 不动)
             market.* 全体用户共享
```

核心机制 = **ContextVar 注入**：`services/context.py` 提供 `current_user_id()` / `set_current_user()`。鉴权中间件每个请求设置一次；后台循环用 `run_as_user(uid)` 逐用户包裹。`database.py` 里所有用户数据函数从上下文取 uid 拼进 SQL（**函数签名不变**，30+ 个服务层调用方零改动）。缺上下文时 uid=0（无主数据，脚本场景的安全兜底）。

**存量数据处理**：不做认领逻辑。现有 user_id=0 的行对注册用户不可见；你验收时自行清空这些测试数据（提供一条 SQL 或小脚本：`DELETE FROM <各用户表> WHERE user_id = 0`）。

## 一、数据库结构调整（ledger schema）

**新增表**：
```sql
users(id IDENTITY PK, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      display_name TEXT DEFAULT '', created_at, updated_at)
-- password_hash: pbkdf2_hmac(sha256, 20万轮, 随机盐), 无需新依赖
sessions(token_hash TEXT PK, user_id BIGINT NOT NULL, created_at,
         expires_at TEXT, last_seen_at TEXT)   -- cookie 存原文token, 库里只存 sha256
```

**用户表加 `user_id BIGINT NOT NULL DEFAULT 0` 列**（holdings、position_actions、external_assets、external_asset_actions、unwind_plans、unwind_tranches、custom_alerts、alert_config、cashflow_monthly、morning_briefings、portfolio_snapshots、watchlist、watchlist_group、position_thesis、thesis_revision、dca_schedules、trade_plan、brokers、user_memory、ask_session、ask_message、ask_run、prompt_rule）。

**主键/唯一约束调整**（防不同用户撞键）：app_config→(user_id,key)、watchlist→(user_id,stock_code)、watchlist_group→(user_id,stock_code,grp)、unwind_plans→(user_id,stock_code)、position_thesis→(user_id,code)、cashflow_monthly→(user_id,month)、portfolio_snapshots→(user_id,snap_date)、morning_briefings UNIQUE+(user_id)、thesis_revision UNIQUE+(user_id)。常用表补 user_id 索引。

**共享表不动**：kline_cache、symbol_dict、sentiment_history、sector_share_history、limit_up_pool、stock_tag、market_volume_history、market.*、tool_gap（agent 诊断台账，全局）。

**存量库迁移**（init_db 内幂等执行）：`ADD COLUMN IF NOT EXISTS user_id BIGINT NOT NULL DEFAULT 0` + 条件式重建旧主键/唯一约束（查 pg_constraint 判断是否存在旧形态再 DROP/ADD，老库 allassets 和新库都能跑）。

## 二、鉴权（新增 api/auth_routes.py + services/auth.py + run.py 中间件）

- `POST /api/auth/register {username, password}`：开放注册（用户名唯一、密码 6 位以上）。
- `POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。
- Cookie：`licai_session`，HttpOnly + SameSite=Lax + Path=/，30 天滑动过期（每次校验刷新 last_seen/expires）。
- **中间件**（run.py，与 same_origin_only 并列）：`/api/*` 全部校验会话（白名单：/api/auth/login、/api/auth/register、/api/app-version），通过后 `set_current_user(uid)`；失败 401。静态页/`/` 放行（SPA 自行按 /api/auth/me 结果显示登录页）。
- **WS**：握手时手动解析 Cookie 校验会话（浏览器同源自动带 cookie），失败关连接（close code 4401）。

## 三、数据层与服务的用户化改造

- `database.py`：约 50 个用户数据函数加 `user_id` 条件/列（从上下文取）。`get_config/set_config` 按 **PER_USER_KEYS 集合**分流：个人键（feishu_webhook_url、feishu_muted、zsxq_mcp_url、zsxq_groups、max_daily_loss、cashflow_savings_rate_target、dividend_dismissed、fee_schedules、okx_credentials）读写 (uid,key)，全局键（llm_*、network_proxy、tdx_base_url、market_provider）固定 (0,key)。
- **飞书/星球单例 → 按用户解析**：`feishu_notify.send_text` 内部按 current_user_id 取该用户的 webhook（维护 {uid: 配置} 缓存，设置页保存时刷新）——所有调用方（几十处）不用改。zsxq_client 同法。`user_memory._ACTIVE_CACHE`、`rule_forge._RULES_CACHE` 改成 {uid: cache}。
- **OKX 凭证**：从 macOS Keychain（Windows 上本就不可用）迁到 app_config 个人键 `okx_credentials`（JSON）。okx_client 的 load/save/clear 改为读写 PG 按用户存。
- **apply_saved_config**：只恢复全局键（现状即是），启动逻辑不变。

## 四、后台循环逐用户化（12 个中 6 个有用户数据）

- `price_monitor_loop`：`_clients` 改为 `{uid: set[WebSocket]}`；循环改为逐用户取各自持仓+报价、只推给本人连接（无连接的用户跳过）。
- `plan_monitor_loop` / `briefing_loop` / `dca_loop` / `eod_summary_loop`（推送部分）/ `rule_mine_loop` / `curve_prewarm_loop`（快照部分）：外层 `for uid in list_user_ids(): with run_as_user(uid): 原逻辑`；`_xxx_done_date` 类"今天跑过没"标志改 {uid: date}。5 个市场级预热循环与 backup_loop 不动。

## 五、导出/导入与脚本

- `export_routes.py`：导出只含当前用户的行（各表 WHERE user_id=当前），导入写入当前用户；跳过全局表。
- `scripts/seed_demo.py`：加 `--user` 参数指定归属（默认 uid 0）；evals 不受影响（无上下文 = uid 0 = demo 库全量）。
- 提供清测试数据的 SQL/脚本：`python scripts/clear_user_data.py`（DELETE 各用户表 WHERE user_id=0，供你验收时清空现有测试数据）。

## 六、前端（cookie 方案，9 处裸 fetch 自动生效）

- 新增 `components/AuthPage.jsx`：登录/注册双 tab，沿用现有卡片/输入框样式语言（bg-surface-2 圆角卡 + 行内三色状态文案）。
- `App.jsx`：顶层 auth gate——启动 `GET /api/auth/me`，未登录整屏渲染 AuthPage（不进 NAV/PAGES 体系，不搅动 hash 路由）；登录成功进入主界面。监听 `licai:auth-changed` 事件（与现有 MUTATED_EVENT 同模式）。
- `useApi.js fetchJSON`：401 时派发 `licai:auth-changed`。
- `Header.jsx`：显示用户名 + 登出按钮（POST logout → 事件 → 回登录页）。
- `useWebSocket.js`：close code 4401 时停止无限重连并派发 auth 事件。
- 登录页为全屏独立视图（后端无 SPA fallback，不新增真路径）。

## 七、实施顺序与验证

1. SCHEMA/迁移（users、sessions、user_id 列、约束重建）→ 临时实例跑通 init_db 双路径（全新库 + 带 data 的老库）
2. context.py + services/auth.py + auth 路由 + 中间件 → curl 冒烟（注册→登录→me→未登录 401）
3. database.py 用户化改造（机械改写，逐表核对）
4. settings/feishu/zsxq/okx 个人化 → 后台循环逐用户化
5. 前端 AuthPage/gate/header/WS
6. 回归：pytest（现有 DB 测试在 fixture 里设 uid 上下文）+ 启动冒烟（注册→录入→看板→第二用户隔离验证）
7. 验收后你执行 `python scripts/clear_user_data.py` 清空无主测试数据

## 明确不做（本期）
- 用户管理后台（删号/禁用/改密）、密码找回、HTTPS/CSRF token（SameSite=Lax + same-origin 中间件已覆盖本机部署场景）、多用户操作审计。