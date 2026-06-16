# AgentTrader

一个面向 A 股的半自动选股项目：

- 使用 Tushare 拉取股票日线数据
- 用量化规则做初选（目前只实现了B1选股）
- 导出候选股票 K 线图
- 调用 Gemini CLI 对图表进行 AI 复评打分

---

## 更新说明

- 推翻了旧版选股模式（各式各样的B1太麻烦了）
- 新加入了AI看图打分精选功能（是的，不用再自己看图了）
- 目前只支持B1选股，后续Z哥讲了砖型图10张图后，会更新砖型图精选

---

## 1. 项目流程

默认产品流程在 `./start_product` 的运行中心中执行；历史完整流程由
[run_all.py](run_all.py) 串联，但 R7 后已默认退休：

1. 下载 K 线数据（运行中心 / `POST /api/runs/market-data`）
2. 量化初选（运行中心 / `POST /api/runs/preselect`）
3. 导出候选图表（运行中心 / `POST /api/runs/chart-export`）
4. Gemini CLI 复评（运行中心 / `POST /api/runs/review/provider`）
5. 归档与查看推荐结果（运行中心 / `POST /api/runs/archive`）

运行中心页面顶部是“Workbench 工作流”，默认运行模式是旧 Workbench 的“跳过抓取”。
可选模式包括“完整流程”“跳过抓取”“初选+导出图表”“只抓取数据”“只跑初选”
“只导出图表”“只跑复评”。点击“开始运行”会按所选模式串行触发对应产品 API；
点击“停止当前任务”会取消当前 live run。每个步骤仍然创建独立 run，Workbench
工作流卡片内的“运行观测”区可以查看配置快照、进度、控制台日志和失败诊断。
产物仍按 run 建索引并通过产品 artifact API 访问。

legacy 文件输出链路：

- data/raw：原始日线 CSV
- data/candidates：初选候选列表
- data/kline/日期：候选图表
- data/review/日期：AI 单股评分与汇总建议

---

## 2. 目录说明

- [pipeline](pipeline)：数据抓取与量化初选
- [dashboard](dashboard)：看盘界面与图表导出
- [agent](agent)：LLM 评审逻辑（Gemini）
- [config](config)：抓取、初选、Gemini 复评配置
- [data](data)：运行数据与结果
- [docs](docs)：方案文档
- [run_all.py](run_all.py)：legacy 全流程一键入口，默认已退休

Agent 协作与 harness 规范见 [AGENTS.md](AGENTS.md) 和
[docs/agent-harness/index.md](docs/agent-harness/index.md)。

---

## 3. 产品化本地启动（React/FastAPI）

R7 重构后的默认本地产品入口是 React web 前端 + FastAPI 后端：

~~~bash
./start_product
~~~

完整产品化使用手册见
[docs/product-usage-manual.md](docs/product-usage-manual.md)。

默认地址：

- Web: http://127.0.0.1:5173
- API: http://127.0.0.1:8000

首次启动前需要安装 Python 依赖和前端依赖：

~~~bash
pip install -r requirements.txt
nvm use
cd apps/web && npm install
~~~

前端工具链在 Node 23 上验证；仓库包含 `.nvmrc`，用于保持本地
Vite/Rolldown 行为可复现。
`./start_product` 会在启动前拒绝非 Node 23.x，并自动初始化 SQLite 产品 schema。
React 前端默认使用中文界面，侧栏可切换中文/英文和跟随系统/浅色/深色主题。

旧的 `start_workbench` 已默认退休；新工作流应使用 `./start_product`。
仅在迁移、对照或回滚时可显式设置 `STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1`
启动 legacy Streamlit 工作台。

---

## 4. 快速开始（产品路径）

### 4.1 Clone 项目

~~~bash
git clone https://github.com/SebastienZh/StockTradebyZ
cd StockTradebyZ
~~~

### 4.2 安装依赖

~~~bash
pip install -r requirements.txt
~~~

### 4.3 设置环境变量

Windows PowerShell（永久写入）：

~~~powershell
[Environment]::SetEnvironmentVariable("TUSHARE_TOKEN", "你的TushareToken", "User")
~~~

写入后请重开终端，环境变量才会在新会话中生效。
Gemini CLI 复评需要先在本机完成 `gemini` 登录；如果要使用旧的 Gemini API
复评方式，再额外设置 `GEMINI_API_KEY`。

### 4.4 启动产品工作台

在项目根目录执行：

~~~bash
./start_product
~~~

然后在运行中心运行每日数据下载、初选、图表导出、复评和归档。旧的 `run_all.py` 与
legacy CLI 链路仅保留用于迁移、对照或回滚；一键脚本本身需要显式设置
`STOCKTRADE_ALLOW_LEGACY_RUN_ALL=1`，子步骤还需要按需设置对应的 legacy
flag。

legacy 一键脚本示例：

~~~bash
STOCKTRADE_ALLOW_LEGACY_RUN_ALL=1 \
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1 \
STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1 \
STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1 \
python run_all.py --skip-fetch
~~~

参数说明：

- --skip-fetch：跳过数据下载，直接进入初选
- --start-from N：从第 N 步开始执行（1 到 4）
- --reviewer：选择复评方式，默认 gemini-cli；gemini-api 使用 GEMINI_API_KEY
- --skip-review：跳过 Gemini 复评，直接打印已有 suggestion.json

---

## 5. 分步运行攻略

### 步骤 1：拉取 K 线

默认产品路径是在 React/FastAPI 运行中心运行“每日数据下载”，或调用
`POST /api/runs/market-data`。旧 CLI 仅用于迁移、对照或回滚：

~~~bash
python -m pipeline.fetch_kline
~~~

配置见 [config/fetch_kline.yaml](config/fetch_kline.yaml)：

- start、end：抓取区间
- stocklist：股票池文件
- exclude_boards：排除板块/股票类型（gem、star、bj、st）
- out：输出目录（默认 data/raw）
- workers：并发线程数

### 步骤 2：量化初选

默认产品路径是在 React/FastAPI 运行中心运行初选，或调用
`POST /api/runs/preselect`。旧 CLI 仅用于迁移、对照或回滚：

~~~bash
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1 python -m pipeline.cli preselect
~~~

可选参数示例：

~~~bash
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1 python -m pipeline.cli preselect --date 2026-03-13
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1 python -m pipeline.cli preselect --config config/rules_preselect.yaml --data data/raw
~~~

规则配置见 [config/rules_preselect.yaml](config/rules_preselect.yaml)。

### 步骤 3：导出候选图表

默认产品路径是在运行中心运行图表导出，或调用
`POST /api/runs/chart-export`。旧脚本仅用于迁移、对照或回滚：

~~~bash
STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1 python dashboard/export_kline_charts.py
~~~

输出到 data/kline/选股日期，图像命名为 代码_day.jpg。

### 步骤 4：Gemini CLI 图表复评

默认产品路径是在运行中心运行复评，或调用
`POST /api/runs/review/provider` 并使用 `provider=gemini-cli`。旧脚本仅用于
迁移、对照或回滚：

可选参数示例：

~~~bash
STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1 python agent/gemini_cli_review.py --config config/gemini_cli_review.yaml
STOCKTRADE_ALLOW_LEGACY_GEMINI_API_REVIEW=1 python agent/gemini_review.py --config config/gemini_review.yaml
~~~

Gemini CLI 配置见 [config/gemini_cli_review.yaml](config/gemini_cli_review.yaml)。
旧 API Key 模式配置见 [config/gemini_review.yaml](config/gemini_review.yaml)。

读取候选与图表后，输出：

- data/review/日期/代码.json
- data/review/日期/suggestion.json

---

## 6. 关键配置建议

### 6.1 抓取层

- 首次全量抓取建议 workers 设小一些（如 4 到 8）
- 若遇到频率限制，降低并发并重试

### 6.2 初选层

- top_m 决定流动性股票池大小
- b1.enabled、brick.enabled 控制策略开关
- 可先只开一个策略做回放验证

### 5.3 复评层

在 [config/gemini_cli_review.yaml](config/gemini_cli_review.yaml) 中可调整：

- model：模型名称
- output_format：CLI 输出格式，默认 stream-json
- request_delay：调用间隔（防限流）
- idle_timeout_seconds：CLI 无 stdout/stderr 输出时的空闲超时，默认 0 关闭，仅保留 900 秒总超时
- batch_size：每次 Gemini CLI 请求最多提交几张图，默认 5
- fallback_to_single_on_batch_error：批量 JSON 解析失败时是否自动降级逐只复评
- save_raw_cli_io / raw_log_dir：保存每次 CLI 调用的原始 prompt、stdout、stderr 和 meta
- max_requests_per_run：单次运行最多请求数；batch_size=5 时，1 次请求最多覆盖 5 支股票
- daily_request_budget：项目侧每日请求预算
- skip_existing：是否断点续跑
- suggest_min_score：推荐分数门槛

---

## 6. 输出结果解读

### 候选文件

Legacy rollback 输出：
[data/candidates/candidates_latest.json](data/candidates/candidates_latest.json)

- pick_date：选股日期
- candidates：候选列表（含 code、strategy、close 等）

### 复评汇总

data/review/日期/suggestion.json

- recommendations：最终推荐（按分数排序）
- excluded：未达门槛代码
- min_score_threshold：推荐门槛

---

## 7. 常见问题

### Q1：fetch_kline 报 token 错误

- 检查 TUSHARE_TOKEN 是否已设置
- 确认 token 有效且账号权限正常

### Q2：导出图表时报 write_image 错误

- 确认已安装 kaleido
- 重新安装：pip install -U kaleido

### Q3：Gemini 运行失败

- CLI 模式：检查 `gemini` 是否已安装并登录
- API 模式：检查 GEMINI_API_KEY 是否设置
- 观察是否命中限流，可提高 request_delay

### Q4：没有候选股票

- 检查 data/raw 是否有最新数据
- 放宽初选阈值（如 B1 或 Brick 参数）
- 检查 pick_date 是否在有效交易日

---

## License

本项目采用 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) 协议发布。

- 允许：学习、研究、非商业用途的使用与分发
- 禁止：任何形式的商业使用、出售或以盈利为目的的部署
- 要求：转载或引用须注明原作者与来源

Copyright © 2026 SebastienZh. All rights reserved.
