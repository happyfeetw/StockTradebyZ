# StockTradebyZ 产品化使用手册

本文档面向本地产品路径：React/Vite 前端 + FastAPI 后端。旧的
`run_all.py`、Streamlit workbench、legacy CLI 写文件链路只保留给迁移、对照
和回滚，不再是默认使用方式。

## 1. 环境要求

- Python 3.12 或兼容版本。
- Node.js 23.x。仓库包含 `.nvmrc`，推荐使用 `nvm use`。
- 本机已配置 Tushare token；Gemini CLI 复评需要先完成 `gemini` 登录。
- 不要把 `TUSHARE_TOKEN`、`GEMINI_API_KEY`、Gemini OAuth 文件、`data/`、
  `var/` 或 provider raw logs 提交到 Git。

首次安装：

```bash
pip install -r requirements.txt
nvm use
cd apps/web && npm install
```

## 2. 启动产品

在仓库根目录运行：

```bash
./start_product
```

默认地址：

- Web: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`

`./start_product` 会在启动前检查 Node.js 23.x，并直接调用本地 Vite CLI；
`npm install` 只用于首次安装前端依赖。FastAPI 默认启动时会自动执行 SQLite
Alembic migrations；空的 `var/db/app.sqlite` 会被初始化为产品 schema。DuckDB
analytics schema 会在读取或写入 analytics 数据时自动迁移。

可选端口覆盖：

```bash
STOCKTRADE_API_PORT=8010 STOCKTRADE_WEB_PORT=5174 ./start_product
```

前端开发服务器会使用同一组 `STOCKTRADE_API_HOST/STOCKTRADE_API_PORT`
环境变量配置 `/api` 代理；覆盖 API 端口时不需要额外设置
`VITE_API_BASE_URL`。

## 3. 前端显示偏好

React 前端默认显示中文界面。侧栏底部提供两个本地显示偏好：

- 语言：中文 / English。
- 主题模式：跟随系统 / 浅色 / 深色。

语言与主题保存在浏览器 `localStorage`，key 分别是
`stocktrade.ui.language` 和 `stocktrade.ui.theme`。这些偏好只影响当前浏览器的显示，
不会改写后端 SQLite settings；设置页面中的产品偏好仍由产品 API 管理。

## 4. 本地状态目录

产品路径使用这些本地生成目录：

- `var/db/app.sqlite`: 产品事务状态，包括 runs、candidates、reviews、archive、
  settings、migration audit。
- `var/db/analytics.duckdb`: analytics facts 和 summary。
- `var/artifacts/{run_id}/`: 图表、provider evidence、logs 等运行产物。
- `var/backups/`: 产品备份。
- `data/`: legacy 输入和迁移源，不是产品默认写入目标。

这些目录默认被 gitignore。

## 5. SQLite 迁移幂等性

FastAPI 产品后端启动时会对文件型 SQLite 数据库执行
`alembic upgrade head`。这个启动路径在 Alembic 正常管理的数据库上是幂等的：
已经到达当前 head 的数据库再次启动时，Alembic 会读取 `alembic_version` 并跳过
已执行版本，不会重复创建表或索引。

这个幂等性有两个边界：

- 它依赖 `alembic_version` 版本表，不代表每个 migration body 都能被手工裸重复
  执行。绕过 Alembic 直接重复执行 `op.create_table(...)` 等 DDL 仍可能冲突。
- R7 本地产品路径按单 FastAPI 进程设计，不承诺多个进程同时对同一个全新
  SQLite 文件做首次迁移。需要多进程共享同一 SQLite 时，应先用单进程完成迁移，
  或在启动器外层增加文件锁/迁移前置步骤。

`:memory:` SQLite 测试库不会自动跑文件型迁移；测试若传入自定义
`session_factory`，应自行准备 schema 或显式关闭 `auto_migrate`。

## 6. 运行中心工作流

### 6.1 初选

在运行中心填写：

- Config path: 默认可留空，等价于 `config/rules_preselect.yaml`。
- Data dir: 默认可留空，等价于 legacy raw 数据目录。
- Pick date / End date: 按需要选择交易日期。

运行后会创建 candidate batch，并写入 SQLite；analytics writer 可同步写入 DuckDB。
候选 identity 始终是 `(code, strategy)`。

### 6.2 图表导出

在候选批次页面选择批次后导出图表。产品图表产物写入 `var/artifacts/{run_id}/`
并通过 artifact API 服务，不再依赖 legacy `data/kline/` 作为默认产品输出。

### 6.3 Gemini CLI 复评

Review 页面可对 candidate batch 发起 `provider=gemini-cli` 的复评。要求：

- Gemini CLI 已安装并能在当前 shell 中运行。
- 本机 Gemini CLI 已完成登录。
- 若要求图表，先完成 chart export。

provider raw prompt、stdout/stderr、checkpoint、usage、result cache 会作为 product
artifact evidence 建索引；原始路径不会直接暴露给前端。

### 6.4 归档

Archive 会把 candidate batch + review run 固化为日级归档快照，并把推荐状态、
rank、chart artifact link 和 review payload 写入 SQLite/DuckDB。

## 7. 迁移页面

迁移页面用于把 legacy `data/` 内容导入产品存储。

推荐顺序：

1. 预检：扫描 candidates、reviews、history，查看 warnings/quarantine。
2. 导入：按 scope 和 pick date 单次导入。
3. 校验：对照 legacy 文件、SQLite 和 DuckDB 记录。

交易账户和 simulated trading 数据不属于当前产品化迁移范围。

## 8. 备份 / 恢复

产品备份包含：

- SQLite state
- DuckDB analytics
- artifact manifest 和 artifacts
- migration version metadata

恢复会替换本地产品状态。执行恢复前应停止其它写入同一 `var/` 根目录
的 API 进程；R7 支持单本地 FastAPI 进程，不支持多进程同时写同一 SQLite/DuckDB。

## 9. 设置页面

设置页面展示：

- 当前产品 stack。
- 本地 state 路径。
- 安全化后的 config inventory。
- 外部集成是否配置，但不会显示 secret value。
- 产品偏好，例如默认策略、分页大小、产品主题、时区。

## 10. 常见问题

### Node 版本不对

`./start_product` 会拒绝非 Node 23.x：

```bash
nvm use
```

然后重新运行 `./start_product`。

### 运行中心报缺少 raw CSV

先确认 `data/raw/{code}.csv` 或自定义 raw dir 中存在对应股票 CSV。Chart Export
只读取 raw CSV，不会自动下载。

### Gemini CLI 未配置

确认：

```bash
which gemini
gemini --version
```

并完成 Gemini CLI 登录。必要时在请求的 provider config 中覆盖 `gemini_bin`。

### 需要使用 legacy 工具

legacy 入口默认关闭，只用于迁移、对照或回滚。必须显式设置对应
`STOCKTRADE_ALLOW_LEGACY_*` 环境变量。

## 11. 验证命令

窄验证：

```bash
scripts/harness/check.sh docs
scripts/harness/check.sh python
scripts/harness/check.sh r7-product-launcher
```

产品化验收建议：

```bash
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh r7-browser-proof
scripts/harness/check.sh r7-resource-envelope
scripts/harness/check.sh r7-runtime-recovery
scripts/harness/check.sh quick
```

前端：

```bash
nvm use
cd apps/web
npm run lint
npm run build
```
