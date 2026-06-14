# AgentTrader

一个面向 A 股的半自动选股与 AI 复评工作台：

- 使用 Tushare 拉取股票日线数据
- 用量化规则做初选，当前主策略为 `b1`、`b2`、`brick`
- 导出候选股票 K 线图
- 调用 AGY CLI、Codex CLI 对图表进行 AI 复评打分
- 用策略化评分和多模型共识筛出高质量候选
- 在本地 Workbench 中复盘、筛选，并导出到通达信自定义板块

---

## 更新说明

- 主线从“单 Gemini 复评”升级为“候选冻结 -> 多模型复评 -> 共识结果 -> 通达信导入”。
- AI 复评使用评分体系 V3：公共交易 gate + 分策略 profile + 本地归一化。
- 多模型复评默认以模型为维度运行 `gemini-3.5-flash-high`、`gemini-3.1-pro-high`、`gpt-5.5-high` 三路；两个 Gemini 模型都通过 AGY 执行，Codex 模型通过 Codex CLI 执行。
- 多模型模式禁止模型静默替换或降级；模型失败时记录原因，并按原模型断点重跑一次。
- `run_all.py` 仍保留轻量单 reviewer 流程；完整多模型流程建议使用 Workbench 或 `agent/multi_model_review.py`。
- 当前分支改动总览见 [docs/review-system-change-summary-2026-06-11.md](docs/review-system-change-summary-2026-06-11.md)。

---

## 1. 项目流程

单 reviewer 快速流程对应 [run_all.py](run_all.py)：

1. 下载 K 线数据（pipeline.fetch_kline）
2. 量化初选（pipeline.cli preselect）
3. 导出候选图表（dashboard/export_kline_charts.py）
4. 单 reviewer 图表复评（默认 AGY CLI）
5. 打印推荐结果（读取 suggestion.json）

Workbench 多模型流程：

1. 下载或增量更新 K 线数据
2. 运行 `b1/b2/brick` 初选并生成候选
3. 导出候选图表
4. 冻结候选批次到 `data/review_batches/{batch_id}`
5. 并行运行 Gemini、AGY、Codex 三路 reviewer
6. 汇总到 `data/review_consensus/{batch_id}`
7. 在“共识结果”页面筛选并导入通达信

输出主链路：

- data/raw：原始日线 CSV
- data/candidates：初选候选列表
- data/kline/日期：候选图表
- data/review/日期：AI 单股评分与汇总建议
- data/review_batches：冻结后的多模型复评批次
- data/review_runs：各模型独立复评结果
- data/review_consensus：多模型共识结果
- data/z_quality：Z 视角精选池质量裁决结果

---

## 2. 目录说明

- [pipeline](pipeline)：数据抓取、量化初选、共识汇总、通达信导出
- [dashboard](dashboard)：看盘界面与图表导出
- [agent](agent)：LLM 复评逻辑（Gemini、AGY、Codex、多模型调度）
- [workbench](workbench)：本地 Streamlit 工作台
- [config](config)：抓取、初选、复评、多模型配置
- [paper_trading](paper_trading)：纸上交易子系统
- [data](data)：运行数据与结果
- [docs](docs)：方案文档
- [run_all.py](run_all.py)：单 reviewer 一键入口
- [start_workbench](start_workbench)：本地工作台启动脚本

---

## 3. 快速开始（一键跑通）

### 3.1 Clone 项目

~~~bash
git clone https://github.com/SebastienZh/StockTradebyZ
cd StockTradebyZ
~~~

### 3.2 安装依赖

~~~bash
pip install -r requirements.txt
~~~

如果本机没有 `python` 命令，可把下文命令中的 `python` 替换为 `python3`。

### 3.3 设置环境变量

Windows PowerShell（永久写入）：

~~~powershell
[Environment]::SetEnvironmentVariable("TUSHARE_TOKEN", "你的TushareToken", "User")
~~~

写入后请重开终端，环境变量才会在新会话中生效。
AGY reviewer 需要本机已安装并登录 `agy`。Codex reviewer 需要本机可执行 `codex` CLI。如果要使用旧的 Gemini API 复评方式，再额外设置 `GEMINI_API_KEY`。

### 3.4 运行一键脚本

在项目根目录执行：

~~~bash
python run_all.py
~~~

常用参数：

~~~bash
python run_all.py --skip-fetch
python run_all.py --start-from 3
python run_all.py --reviewer gemini-api
python run_all.py --skip-review
~~~

参数说明：

- --skip-fetch：跳过数据下载，直接进入初选
- --start-from N：从第 N 步开始执行（1 到 4）
- --reviewer：选择单 reviewer 复评后端，默认 agy-cli；gemini-api 使用 GEMINI_API_KEY
- --skip-review：跳过复评，直接打印已有 suggestion.json

### 3.5 启动 Workbench

~~~bash
bash start_workbench
~~~

默认访问 `https://localhost:8601`。脚本使用 `.certs/cert.pem` 和 `.certs/key.pem` 启动 HTTPS；如果证书不存在，可参考 [docs/tdx-import-design.md](docs/tdx-import-design.md) 生成自签名证书。

Workbench 适合日常使用：

- 运行中心：配置并执行抓取、初选、导图、复评
- 复评配置：按模型选择 Gemini 3.5 Flash High、Gemini 3.1 Pro High、GPT-5.5 High 或三模型共识
- 结果中心：查看正式单 reviewer 结果
- 共识结果：查看多模型共识、分歧和模型明细
- 通达信导入：导出正式结果或共识筛选结果

---

## 4. 分步运行攻略

### 步骤 1：拉取 K 线

~~~bash
python -m pipeline.fetch_kline
~~~

配置见 [config/fetch_kline.yaml](config/fetch_kline.yaml)：

- start、end：抓取区间
- stocklist：股票池文件
- exclude_boards：排除板块/股票类型（gem、star、bj、st）
- out：输出目录（默认 data/raw）
- workers：并发线程数
- tushare_requests_per_minute：Tushare 调用限速，避免 `adj_factor` 频率超限

流程和底层机制见 [docs/fetch-kline-mechanism.md](docs/fetch-kline-mechanism.md)。

### 步骤 2：量化初选

~~~bash
python -m pipeline.cli preselect
~~~

可选参数示例：

~~~bash
python -m pipeline.cli preselect --date 2026-03-13
python -m pipeline.cli preselect --config config/rules_preselect.yaml --data data/raw
~~~

规则配置见 [config/rules_preselect.yaml](config/rules_preselect.yaml)。

### 步骤 3：导出候选图表

~~~bash
python dashboard/export_kline_charts.py
~~~

输出到 data/kline/选股日期，图像命名为 代码_day.jpg。

### 步骤 4：AGY CLI 图表复评

~~~bash
python agent/agy_cli_review.py
~~~

可选参数示例：

~~~bash
python agent/agy_cli_review.py --config config/agy_cli_review.yaml
python agent/gemini_review.py --config config/gemini_review.yaml
python agent/codex_cli_review.py --config config/codex_cli_review.yaml
python agent/multi_model_review.py --config config/multi_model_review.yaml
python agent/z_quality_review.py --config config/z_quality_rules.yaml
~~~

AGY 复评配置见 [config/agy_cli_review.yaml](config/agy_cli_review.yaml)，默认使用 `Gemini 3.5 Flash (High)`，输出到 `data/review/agy_cli`。AGY 目前没有 `--output-format json/stream-json`，项目使用 prompt 级 JSON、本地 schema 校验和一次 JSON repair。
旧 API Key 模式配置见 [config/gemini_review.yaml](config/gemini_review.yaml)。
Codex CLI 配置见 [config/codex_cli_review.yaml](config/codex_cli_review.yaml)，默认固定 `gpt-5.5`、`reasoning_effort=high`、标准速度路径。
多模型配置见 [config/multi_model_review.yaml](config/multi_model_review.yaml)，会冻结候选批次、生成共识结果，并在 `z_quality.enabled=true` 时自动运行 Z 质量裁决。
Z 质量裁决配置见 [config/z_quality_rules.yaml](config/z_quality_rules.yaml)，也可单独重跑；默认读取最新共识结果，输出到 `data/z_quality/{batch_id}`。第一版不调用 zettaranc skill 的数据层，只使用本项目共识结果、单股复评 JSON、日线图和 `data/raw` K 线特征；详见 [docs/z-quality-layer.md](docs/z-quality-layer.md)。
在 workbench 的“复评配置”页面，用户按模型选择复评；两个 Google 模型会自动通过 AGY 执行，GPT 模型会自动通过 Codex CLI 执行。`max_items` 留空表示完整复评，开启限制后可用于 smoke test。结果中心和单票复盘可以通过“复评结果源”按模型查看隔离结果。

读取候选与图表后，输出：

- data/review/日期/代码.json
- data/review/日期/suggestion.json
- data/review_consensus/批次/summary.json
- data/review_consensus/批次/decisions.json
- data/review_consensus/批次/details.json
- data/z_quality/批次/summary.json
- data/z_quality/批次/decisions.json

---

## 5. 关键配置建议

### 5.1 抓取层

- 首次全量抓取建议 workers 设为 2 到 4，并通过 `tushare_requests_per_minute` 控制真实接口频率
- 若遇到 Tushare 频率限制，优先降低 `tushare_requests_per_minute` 或提高 `tushare_rate_cooldown_seconds`

### 5.2 初选层

- top_m 决定流动性股票池大小
- b1.enabled、b2.enabled、brick.enabled 控制策略开关
- 可先只开一个策略做回放验证

### 5.3 复评层

在 [config/agy_cli_review.yaml](config/agy_cli_review.yaml) 中可调整：

- model：模型名称
- request_delay：调用间隔（防限流）
- batch_size：每次 AGY CLI 请求最多提交几张图，正式多模型默认 4；小样本实测比 2/3 更快，仍保留超时拆小批兜底
- fallback_to_single_on_batch_error：批量 JSON 解析失败时是否使用同一模型拆批并最终逐只复评
- AGY 的 `print_timeout` / `timeout_seconds` 默认是 `6m` / `360`：批量超时时先按同一模型拆小批，单股仍超时时再按模型级失败记录，交给多模型编排按原模型重跑一次
- save_raw_cli_io / raw_log_dir：保存每次 CLI 调用的原始 prompt、stdout、stderr 和 meta
- max_requests_per_run：单次运行最多请求数；batch_size=4 时，1 次请求最多覆盖 4 支股票
- daily_request_budget：项目侧每日请求预算
- skip_existing：是否断点续跑
- suggest_min_score：推荐分数门槛

多模型复评关键配置：

- expected_strategies：严格模式下要求候选批次包含的策略，默认 `b1/b2/brick`
- strict_batch：缺少策略或缺少图表时是否中止
- no_model_substitution：禁止模型替换或降级，默认开启
- rerun_failed_models_once：失败模型按原模型重跑一次，默认开启
- z_quality：共识完成后的 Z 质量裁决后处理配置
- review_scoring：公共 gate、策略 profile 和 PASS/WATCH 门槛
- reviewers：声明要运行的模型 ID、执行后端、后端模型名和输出 profile

AGY 迁移探索见 [docs/agy-cli-review-migration-exploration-plan.md](docs/agy-cli-review-migration-exploration-plan.md)。当前默认 Google 订阅登录复评路径是 AGY；Gemini CLI 仅作为历史兼容脚本保留，不再进入默认 Workbench 或多模型配置。

AGY 运行中如果触发 OAuth/Keychain 认证抖动，`auth_recovery_enabled` 默认会暂停当前批次并等待登录态恢复。AGY 非交互 `--print` 默认使用 `stdin_mode: devnull`，避免子进程等待 stdin EOF 导致挂起；只有临时改为 `stdin_mode: pipe` 时，才会把 `auth_recovery_status.json` 中提示的 `auth_code_file` 转发到 AGY stdin。恢复后使用同一模型重试当前批次，不做模型降级。可在 [config/agy_cli_review.yaml](config/agy_cli_review.yaml) 调整等待时长、探测间隔和权限开关。

---

## 6. 输出结果解读

### 候选文件

[data/candidates/candidates_latest.json](data/candidates/candidates_latest.json)

- pick_date：选股日期
- candidates：候选列表（含 code、strategy、close 等）

### 复评汇总

data/review/日期/suggestion.json

- recommendations：最终推荐（按分数排序）
- excluded：未达门槛代码
- min_score_threshold：推荐门槛

### 多模型共识

data/review_consensus/批次/summary.json

- complete：所有正式必需模型是否都完成评分
- models：本批实际纳入正式共识的模型
- decision_bucket_counts：共同推荐、多数推荐、单模型推荐、无推荐、缺失的数量
- invariant_violations：共识构建时发现的数据一致性问题
- z_quality：若已运行 Z 层，记录 Z summary、decisions、裁决统计和 result_mode

data/review_consensus/批次/decisions.json

- code、strategy：按股票和来源策略对齐
- consensus_score：平均分和推荐一致性加权后的共识分
- consensus_verdict：PASS / WATCH / FAIL / INCOMPLETE
- recommended_by_model：各模型是否推荐
- scores_by_model、verdicts_by_model：各模型分数和结论
- Workbench 共识结果页会合并展示 Z 裁决，可在决策结果集按 Z 裁决筛选，也可在“Z质量裁决”页签查看 Z 分、硬否决、观察限制、理由和风险。

`incomplete` 不应作为最终决策依据，应断点重跑补齐 Gemini、AGY、Codex 三路正式模型。

### Z 精选池

data/z_quality/批次/decisions.json

- z_quality_verdict：A_SELECT / B_WATCH / C_REVIEW_ONLY / REJECT
- z_quality_score：本地规则 dry-run 质量分；Z 精选/观察使用 `config/z_quality_rules.yaml` 中的 `z_select_min_quality_score` / `z_watch_min_quality_score`，不是模型复评的 `suggest_min_score=4.0`
- quality_reasons：入选或保留观察的核心原因
- quality_risks：主要硬伤和观察风险
- next_day_plan：次日条件化观察预案，不是自动买入指令
- hold_plan：买后观察纪律，供人工参考

共识结果导入通达信支持 Z 层快捷方案和自定义筛选：

- `Z精选`、`Z观察`、`Z精选+观察`、`Z复盘样本`
- Z 裁决、Z 质量分、排除 Z 硬否决、排除 Z 观察限制
- 仍按策略分板块生成 `.blk`；Z 相关方案会在日期和策略之间插入 `Z`，如 `0612ZB1`

当前 Z 层是共识后的后处理器，不绕过多模型复评直接处理原始初选池。

### 运行产物清理口径

- `data/` 下的 raw、review、runs、analysis、consensus、z_quality 都是本地运行产物，默认不应提交。
- 多模型探索过程中可能留下旧模型目录或旧日志；判断当前有效口径时，以 `data/review_consensus/latest.json` 里的 `models`、`complete` 和 `generated_at` 为准。
- Workbench 的 pid 文件只能作为启动记录；如果进程不存在，应以实际进程状态为准。
- 需要给通达信导入时，优先从 Workbench 的“结果中心”或“共识结果”页面发起，避免手工复制 `.blk` 内容。

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
