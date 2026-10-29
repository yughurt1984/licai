# 期货/期权交易重设计：开平仓 + 多空方向 + 同花顺合约与行情

## 核心模型（按你的确认：同合约双向各一行 / 持仓页独立桶 / 无存量数据）

- **合约标识统一用同花顺 thscode**（如 `CU2601.SHF`、期权完整代码），存 `holdings.stock_code`。
- **行情接 fuyao API**（X-api-key 鉴权）：现价 = `/api/futures|options/prices/intraday` 末点 price；昨收 = `/prices/daily` 倒数第二根 close → 涨跌幅自算。**期货和期权都自动报价**（期权不再手填市值）。结果 write-through `market.quote_latest`（复用 PG 优先快路径 + 底稿自动登记，与股票同一套机制）。
- **净持仓→双向**：holdings 唯一键改 `(user_id, stock_code, direction)`，同合约多空各一行。

## 一、数据库（幂等迁移，无存量迁移）

1. `position_actions` 加列：`direction TEXT`（LONG/SHORT）、`contract_multiplier DOUBLE PRECISION`（每笔记当时乘数，历史准确）；action_type 新增 `OPEN`（开仓）/`CLOSE`（平仓）。
2. `holdings` 加列：`direction TEXT`、`contract_multiplier DOUBLE PRECISION DEFAULT 1`、`asset_kind TEXT DEFAULT 'stock'`（stock/fut/opt）、`last_price DOUBLE PRECISION`（期权行情兜底）；唯一键重建为 `(user_id, stock_code, direction)`（沿用 init_db 条件重建机制）。
3. `brokers` 表确认/补 `fut_rate/fut_min` 费率档（手续费默认 0，逐笔手填覆盖优先）。
4. `.env`/`config.py` 加 `THS_API_KEY`（fuyao 鉴权），设置页补一个保存位（app_config 全局键 `ths_api_key`）。

## 二、行情服务（新 services/ths_fuyao.py）

- `contracts_list(asset_type)` / `contracts_search(keyword)`：合约目录与搜索（`/api/meta/tickers/list|search`、`/api/futures/contracts/list`）→ 供开仓表单搜合约带出 thscode/名称。
- `quote(thscode)`：intraday 末点现价 + daily 昨收 → `{price, prev_close, change_pct, name}`，内存缓存 30s + write-through quote_latest（asset_type=fut/opt，顺带自动登记底稿）。
- `get_realtime_quotes` 加分支：代码带交易所后缀（.SHF/.DCE/.CZCE/.CFFEX/.INE/.GFEX 等）→ 走 fuyao 行情；PG 快路径对 fut/opt 天然生效（quote_latest 已有行时零外网）。

## 三、账本（position_ledger 方向语义，盈亏逻辑核查结论）

1. `compute_position_state` 支持方向：**SHORT 序列用负价技巧**——把 SHORT 的 OPEN/CLOSE 价格整体 ×(−1) 喂进现有 LONG FIFO，配对/段/carry/综合成本自动正确，输出 realized 再翻回（数学：LONG 公式 (C−P)×S 经负价变换恰等于空头真实盈亏 (P−C)×S），核心改动集中在入口变换 + 输出翻转，避免重写 FIFO。
2. **三种盈亏核查结论**：
   - 浮动盈亏：**必须调整**——多头=(现价−成本价)×乘数×手数；空头=(成本价−现价)×乘数×手数。list_holdings 加 fut/opt 分支；前端 WS 推送公式按 direction/multiplier 修正；
   - 已实现盈亏：**必须调整**——FIFO 配对按方向（上述负价技巧），/api/portfolio/realized 的 ledger_state 复用后天然输出；
   - 累计盈亏（浮动+realized_carry）：**口径不变**——方向在两个分量内各自消化，防重复机制照用；
   - 今日盈亏：期货现金流口径不适用 → 改用 change_pct × 方向（前端 cryptoTodayPnl 同款公式）。
3. 手数/价格语义：`holdings.shares`=手数、`cost_price`=每单位开仓均价（与报价同单位）；市值=现价×乘数×手数。

## 四、API（api/portfolio_routes.py）

- 添加持仓扩展：`kind='fut'|'opt'` → 校验 thscode、写 direction/multiplier、seed OPEN 流水；fuyao 拉合约名称自动补全。
- 新端点：`GET /api/portfolio/contracts/search?q=`（fuyao 合约搜索，供表单）、`POST .../open` 与 `.../close`（或复用 actions 端点加 OPEN/CLOSE 类型）。
- HoldingResponse 加 `direction/contract_multiplier/asset_kind`。

## 五、前端（持仓页独立桶）

1. 第一层分类：**股票（A/港/美）｜期货｜期权**——`normalizeHolding` 按 asset_kind 输出 type T/O；T/O 行改走 holdings 管线。
2. **开仓表单**（AddAShareForm 风格）：合约搜索（fuyao）→ 带出 thscode/名称 → 方向（多红/空绿）+ 手数 + 每手乘数 + 开仓价（默认现价）+ 手续费 + 券商 + 日期/时刻。
3. 行内操作：加仓（同向 OPEN）/平仓（CLOSE）/流水/编辑，复用现有 lots 交互模式；流水弹窗加 OPEN/CLOSE + 方向徽标；HoldingRow 显示方向徽标与"手数×乘数"。
4. 移除 external 管线的 T/O 入口（AddAssetForm 的 FUT/OPT 分支、添加菜单旧项、EditAssetRow 隐患分支）；TYPE_META 保留供新管线。
5. SummaryStrip/累计盈亏、today-pnl 的期货口径并入（change_pct×方向）。

## 六、验证
- 临时库自检：双向持仓行、OPEN/CLOSE FIFO 多空 realized（含多翻空序列）、浮动×方向×乘数、fuyao 客户端（mock 响应）、底稿登记、唯一键重建；
- eslint + build；你配好 THS_API_KEY 后重启验收。

## 本期不做
- 保证金/杠杆占用跟踪、行权指派（期权到期行权）、K线图（fuyao daily 已具备，后续可接）、梯度佣金精细化。