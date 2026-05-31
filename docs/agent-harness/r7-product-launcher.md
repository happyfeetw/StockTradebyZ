# R7 产品启动器

管理 issue：#152
状态日期：2026-05-27

本文定义重构后 React/FastAPI 产品的默认本地启动路径。它是后续退休
`start_workbench` 前必须保留的替代启动证明。

## 默认产品入口

使用：

```bash
./start_product
```

启动器会启动：

- FastAPI 应用：默认在 `127.0.0.1:8000` 运行 `stocktrade_api.main:app`。
- React/Vite 前端：默认在 `127.0.0.1:5173` 运行，通过
  `apps/web/node_modules` 下的本地 Vite CLI 启动。
- Vite dev proxy 读取 `STOCKTRADE_API_HOST/STOCKTRADE_API_PORT`，所以
  `/api` 会跟随启动器的 API 端口覆盖，不硬编码 `8000`。
- `PYTHONPATH=apps/api:src`，使 API 在不依赖 legacy CLI 路径的情况下导入产品模块。
- 启动 Vite 前会强制检查 Node.js 23.x。启动器报告 Node.js 版本不支持时，先运行
  `nvm use`。运行时不依赖 `npm run`；首次本地安装仍需要 `npm install` 填充
  `node_modules`。
- FastAPI 应用创建时会对文件型产品数据库执行 SQLite Alembic migrations，所以干净的
  `var/db/app.sqlite` 会自动具备产品 schema。对 Alembic 管理的数据库来说，这一路径依赖
  `alembic_version` 幂等；但 migration body 不承诺可被手工裸重复执行，对同一个全新
  SQLite 文件并发做首次迁移也不属于 R7 单进程启动器契约。

可选本地覆盖：

- `PYTHON_BIN`
- `STOCKTRADE_API_HOST`
- `STOCKTRADE_API_PORT`
- `STOCKTRADE_WEB_HOST`
- `STOCKTRADE_WEB_PORT`

## 前端语言与主题

React 前端默认使用中文界面，并在侧栏提供显示偏好：

- 语言：中文 / English。
- 主题模式：跟随系统 / 浅色 / 深色。

这两个选择保存在浏览器 `localStorage` 中，key 分别为
`stocktrade.ui.language` 和 `stocktrade.ui.theme`。主题通过
`html[data-theme]` 切换设计 token，不影响后端产品偏好或 SQLite settings。

## Legacy Workbench 关系

R7 退休 Streamlit/workbench surface 时，`start_workbench` 默认退休。它必须指向
`./start_product` 作为受支持 React/FastAPI 工作流的入口；只有显式设置
`STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1` 回滚 flag 时才可以运行。

本启动器不改变：

- 选股公式或候选 identity。
- 复评打分行为。
- 归档/历史语义。
- SQLite/DuckDB 所有权。
- simulated trading；它仍不在本轮产品化范围内。

## 验证

运行：

```bash
python3 -m py_compile scripts/harness/check.py tests/test_product_launcher_harness.py
bash -n start_product start_workbench
PYTHONPATH=apps/api:src python3 -m unittest tests.test_product_launcher_harness
scripts/harness/check.sh r7-product-launcher
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

`r7-product-launcher` gate 检查：

- `start_product` 存在且可执行。
- 启动器使用本地主机/端口默认值启动 FastAPI 和 Vite dev server。
- Vite proxy 跟随启动器的 API host/port 覆盖。
- 启动器在调用 Vite 前拒绝不支持的 Node.js 版本。
- FastAPI 应用可在干净 SQLite 路径上服务产品状态 API。
- `start_workbench` 指向 `./start_product` 作为替代入口。
- 前端提供中文默认界面、语言切换和明暗主题切换。
- simulated trading 仍不在范围内。

## 回滚

仅在修复产品启动器期间使用
`STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1 ./start_workbench` 回滚。启动器回滚不应删除或改写
legacy `data/` 记录。
