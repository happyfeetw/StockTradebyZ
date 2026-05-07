# Gemini CLI 复评方案

本文档描述将当前项目的 Gemini API 复评环节扩展为 Gemini CLI 复评的方案。
当前实现已把 `run_all.py` 的默认复评方式切换为 Gemini CLI，并保留 Gemini API
作为显式兼容模式。

## 背景

当前项目主流程为：

1. `pipeline.fetch_kline` 拉取 A 股日线数据。
2. `pipeline.cli preselect` 做量化初选，生成 `data/candidates/candidates_latest.json`。
3. `dashboard/export_kline_charts.py` 导出候选股日线图。
4. `agent/gemini_review.py` 调用 Gemini API 对图表复评。
5. `run_all.py` 读取 `data/review/{pick_date}/suggestion.json` 打印推荐结果。

现有第 4 步依赖 `GEMINI_API_KEY`。如果使用 Google AI Pro / Ultra 订阅账号，更适合尝试 Gemini CLI 的 Google 账号登录方式，而不是直接走 Gemini API Key。

## 目标

新增 Gemini CLI 复评能力，作为现有 Gemini API 复评的并行实现：

- 保留 `agent/gemini_review.py`，继续支持 API Key 模式。
- 新增 `agent/gemini_cli_review.py`，支持通过本机 `gemini` 命令调用模型。
- 复用现有 `BaseReviewer` 的候选读取、图表查找、结果汇总逻辑。
- 复用 `agent/prompt.md` 的评分规则和 JSON 输出契约。
- 输出文件保持兼容：
  - `data/review/{pick_date}/{code}.json`
  - `data/review/{pick_date}/suggestion.json`

## 非目标

- 不把 Gemini CLI 当成项目文件编辑代理使用。
- 不让 Gemini CLI 直接写入仓库或 `data/review`。
- 不删除现有 Gemini API 版本。
- 不在 Python 脚本中处理 Google 账号登录流程。

## 预期使用方式

首次安装和登录：

```bash
npm install -g @google/gemini-cli
gemini
```

在交互界面中选择 `Login with Google`，使用拥有 Google AI Pro / Ultra 订阅的账号登录。

完成登录后，项目侧运行：

```bash
python agent/gemini_cli_review.py
```

可选配置：

```bash
python agent/gemini_cli_review.py --config config/gemini_cli_review.yaml
```

## 技术方案

### 1. 新增配置文件

新增 `config/gemini_cli_review.yaml`：

```yaml
candidates: data/candidates/candidates_latest.json
kline_dir: data/kline
output_dir: data/review
prompt_path: agent/prompt.md

gemini_bin: gemini
model: "gemini-3.1-pro-preview"
request_delay: 10
batch_size: 10
fallback_to_single_on_batch_error: true
retry_backoff_seconds: [30, 90, 180, 480, 900]
retry_jitter_ratio: 0.2
skip_existing: true
suggest_min_score: 4.0
timeout_seconds: 900
output_format: json
max_requests_per_run: 50
daily_request_budget: 80
stop_on_rate_limit: false
rate_limit_backoff_seconds: 300
usage_file: data/review/.gemini_cli_usage.json
```

说明：

- `gemini_bin`：Gemini CLI 可执行文件名或绝对路径。
- `model`：默认 `gemini-3.1-pro-preview`；可置空以使用 CLI 当前默认模型。
- `output_format`：优先使用 CLI 的 JSON 输出模式，方便稳定解析。
- `timeout_seconds`：防止单次 CLI 调用长时间卡住。
- `batch_size`：单次 CLI 请求最多提交几张图，默认 10，上限仍为 2700，并会按实际图片尺寸和上下文预算动态切批。
- `fallback_to_single_on_batch_error`：批量 JSON 解析失败、超时或流式连接中断时，自动拆批并最终降级为逐只复评。
- `retry_backoff_seconds`：遇到 `429`、`RESOURCE_EXHAUSTED`、`No capacity available`、`Premature close`、超时等错误时的退避序列。
- `retry_jitter_ratio`：在退避秒数上增加随机抖动，避免固定节奏连续撞服务端容量。
- `max_requests_per_run`：控制单次运行最多调用多少次 Gemini CLI；如果降级逐只复评，需要同步提高该值或分多次断点续跑。
- `daily_request_budget`：项目侧每日调用预算，避免撞到订阅账号日限额。
- `stop_on_rate_limit`：重试耗尽后遇到限流或额度错误是否立即停止；默认 false，优先跳过失败股票并继续处理后续候选。

### 额度与限速

Gemini CLI 存在分钟级请求速率限制和每日请求次数限制。批量图表复评必须按“少量、限速、可续跑”的方式设计：

- 默认按上下文上限 1,048,576 tokens 的 90% 控制单批。
- 当前项目导出的 2800×1400 图片估算约 32 个 tile、约 9,280 图像 tokens；90 张理论接近 90% 上下文预算，但实际会触发较大的 SSE 请求体，默认改为 10 张/批。
- 每次 CLI 调用后 sleep `request_delay` 秒。
- 本地维护每日使用计数，例如 `data/review/.gemini_cli_usage.json`。
- 达到 `max_requests_per_run` 或 `daily_request_budget` 后停止。
- 如果 stdout/stderr 或退出码显示 `429`、`RESOURCE_EXHAUSTED`、`No capacity available`、`Premature close`、`ECONNRESET`、超时等错误，按 `retry_backoff_seconds` 加 jitter 重试，并写入 `gemini_cli_review_checkpoint.json`。
- `skip_existing: true` 时优先复用已有 `{code}.json`，支持隔天继续跑。
- 批量重试耗尽后先拆半继续；小批仍失败时再逐只复评，避免一开始就把整批拆成单股请求。
- 每次 CLI 调用前打印实际执行命令和 `--model`，避免与 `/model` 交互状态混淆。

### 2. 新增 GeminiCliReviewer

新增 `agent/gemini_cli_review.py`：

- 继承 `BaseReviewer`。
- `review_stock()`/`review_batch()` 中通过 `subprocess.Popen()` 调用 `gemini`，并在超时时清理
  Gemini CLI 子进程组。
- prompt 中包含：
  - `agent/prompt.md` 的系统评分规则。
  - 单股或批量股票代码。
  - 本地图片的 `@file` 引用。
  - 单股强制返回 JSON 对象，批量强制返回 JSON 数组。
- Python 只读取 CLI stdout，不允许 Gemini CLI 写入结果文件。
- 由于 `data/` 在仓库中被 gitignore，脚本会把待分析图表临时复制到
  `.gemini_cli_tmp/`，再用 `@.gemini_cli_tmp/{code}_day.jpg` 传给 CLI；
  调用结束后删除临时图表。
- 批量模式要求 Gemini CLI 返回与输入股票顺序一致的 JSON 数组；脚本会校验
  `code` 顺序和数组长度，再拆分写入单股 `{code}.json`。

实际命令形态：

```bash
gemini \
  --skip-trust \
  --approval-mode plan \
  --output-format json \
  --prompt "<完整复评提示词，包含 @file 图表引用>"
```

如果配置了模型，则增加：

```bash
gemini \
  --model "<model>" \
  --skip-trust \
  --approval-mode plan \
  --output-format json \
  --prompt "<完整复评提示词，包含 @file 图表引用>"
```

### 3. 解析策略

Gemini CLI 的 stdout 可能有两层 JSON：

1. CLI 外层 JSON，例如包含 `response` 字段。
2. 模型正文中的评分 JSON。

解析顺序：

1. 尝试把 stdout 解析为 CLI 外层 JSON。
2. 如果存在 `response` 或类似文本字段，取该字段。
3. 否则直接把 stdout 当作模型正文。
4. 调用 `BaseReviewer.extract_json()` 提取评分 JSON。
5. 补充 `code` 字段。

### 4. 图片输入验证

正式改造前需要先做最小探针，确认 Gemini CLI 在 headless 模式下能读取本地图片：

```bash
gemini --output-format json --prompt "请读取这张图片并用一句话描述：@.gemini_cli_tmp/600000_day.jpg"
```

验收标准：

- CLI 返回成功退出码。
- stdout 中能看到与图片内容相关的描述。
- 没有要求交互式确认。
- 订阅账号额度可用。

当前实测路径使用 `@file` 引用；如果未来 CLI 改变附件机制，需要优先更新
`agent/gemini_cli_review.py` 的图表引用构造逻辑。

## 安全边界

建议新增 `.gemini/settings.json`，尽量限制 Gemini CLI 的工具能力。

原则：

- 复评阶段只需要读图和生成文本。
- 不需要 shell 执行。
- 不需要编辑文件。
- 不需要写入项目目录。

最终结果文件由本项目 Python 代码写入，避免 Gemini CLI 直接操作仓库。

## run_all.py 集成

`run_all.py` 已集成复评方式参数，并默认使用 Gemini CLI：

```bash
python run_all.py
python run_all.py --reviewer gemini-api
python run_all.py --reviewer gemini-cli
python run_all.py --skip-review
```

兼容策略：

- 默认行为使用 Gemini CLI，适配本机 Google 账号登录。
- 旧 API Key 流程通过 `--reviewer gemini-api` 显式调用。
- `--reviewer gemini-cli` 调用 `agent/gemini_cli_review.py`。
- `--skip-review` 只跑到图表导出，方便人工或 Codex 复评。

当前实现阶段先不接入 Digital Oracle。最终推荐仍以 Gemini CLI 图表复评生成的
`data/review/{pick_date}/suggestion.json` 为准，后续如果重新启用交易数据复核，
再作为独立可选步骤追加。

## 验收标准

### 环境验收

- `which gemini` 能找到 Gemini CLI。
- `gemini` 已完成 Google 账号登录。
- 最小图片探针能返回图像描述。

### 功能验收

- 给定已有 `data/candidates/candidates_latest.json` 和 `data/kline/{pick_date}/*_day.jpg`。
- 运行 `python agent/gemini_cli_review.py`。
- 每只成功复评股票生成 `data/review/{pick_date}/{code}.json`。
- 最终生成 `data/review/{pick_date}/suggestion.json`。
- `run_all.py` 能读取 `suggestion.json` 打印推荐结果。
- 小批量运行达到 `max_requests_per_run` 时能正常停止。
- 额度或限流错误不会破坏已生成结果。

### 兼容验收

- 原 `agent/gemini_review.py` API Key 模式仍可运行。
- 输出 JSON 字段保持与当前 `BaseReviewer.generate_suggestion()` 兼容。
- `skip_existing: true` 时能断点续跑。

## 主要风险

- Gemini CLI 登录态依赖本机用户环境，不适合无头服务器直接复现。
- Google AI Pro / Ultra 订阅额度和 Gemini API 额度不是同一套体系。
- CLI headless 图片输入方式需要实测确认。
- CLI 输出格式可能随版本变化，需要解析逻辑保持宽容。
- 批量调用可能触发每分钟速率限制和每日请求次数限制，需要限速、预算和断点续跑。

## 当前实施状态

已完成：

1. 新增 `config/gemini_cli_review.yaml`。
2. 新增 `agent/gemini_cli_review.py`。
3. `run_all.py` 默认调用 Gemini CLI 复评。
4. 保留 `--reviewer gemini-api` 兼容旧 API Key 模式。
5. 增加 10 图批处理、批量失败退避重试、拆批降级、单股重试、单次运行上限、每日预算、限流识别、checkpoint 和超时清理。

后续如果需要重新启用 Digital Oracle，应作为 Gemini CLI 复评之后的独立可选步骤
接入，避免侵入现有初选、图表导出和复评步骤。
