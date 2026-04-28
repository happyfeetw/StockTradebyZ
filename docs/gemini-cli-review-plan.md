# Gemini CLI 复评方案

本文档描述将当前项目的 Gemini API 复评环节扩展为 Gemini CLI 复评的方案。

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
model: ""
request_delay: 5
skip_existing: false
suggest_min_score: 4.0
timeout_seconds: 180
output_format: json
max_requests_per_run: 20
daily_request_budget: 80
stop_on_rate_limit: true
rate_limit_backoff_seconds: 300
```

说明：

- `gemini_bin`：Gemini CLI 可执行文件名或绝对路径。
- `model`：可为空，使用 CLI 当前默认模型；如果 CLI 支持 `--model`，再传入指定模型。
- `output_format`：优先使用 CLI 的 JSON 输出模式，方便稳定解析。
- `timeout_seconds`：防止单股复评长时间卡住。
- `max_requests_per_run`：控制单次运行最多调用多少次 Gemini CLI。
- `daily_request_budget`：项目侧每日调用预算，避免撞到订阅账号日限额。
- `stop_on_rate_limit`：遇到限流或额度错误时停止，保留已完成结果。

### 额度与限速

Gemini CLI 存在分钟级请求速率限制和每日请求次数限制。批量图表复评必须按“少量、限速、可续跑”的方式设计：

- 默认不一次性打满全部候选。
- 每次调用后 sleep `request_delay` 秒。
- 本地维护每日使用计数，例如 `data/review/.gemini_cli_usage.json`。
- 达到 `max_requests_per_run` 或 `daily_request_budget` 后停止。
- 如果 stdout/stderr 或退出码显示 rate limit、quota、too many requests 等错误，立即停止或长时间退避，不连续重试。
- `skip_existing: true` 时优先复用已有 `{code}.json`，支持隔天继续跑。

### 2. 新增 GeminiCliReviewer

新增 `agent/gemini_cli_review.py`：

- 继承 `BaseReviewer`。
- `review_stock()` 中通过 `subprocess.run()` 调用 `gemini`。
- prompt 中包含：
  - `agent/prompt.md` 的系统评分规则。
  - 股票代码。
  - 本地图片绝对路径。
  - 强制只返回评分 JSON。
- Python 只读取 CLI stdout，不允许 Gemini CLI 写入结果文件。

建议命令形态：

```bash
gemini \
  -p "<完整复评提示词，包含图片路径>" \
  --output-format json
```

如果实测支持模型参数，则使用：

```bash
gemini \
  --model "<model>" \
  -p "<完整复评提示词，包含图片路径>" \
  --output-format json
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
gemini -p "请读取这张图片并用一句话描述：/absolute/path/to/600000_day.jpg" --output-format json
```

验收标准：

- CLI 返回成功退出码。
- stdout 中能看到与图片内容相关的描述。
- 没有要求交互式确认。
- 订阅账号额度可用。

如果 CLI 无法通过纯路径读取图片，需要测试替代输入方式，例如 `@file` 引用或 CLI 支持的附件参数。

## 安全边界

建议新增 `.gemini/settings.json`，尽量限制 Gemini CLI 的工具能力。

原则：

- 复评阶段只需要读图和生成文本。
- 不需要 shell 执行。
- 不需要编辑文件。
- 不需要写入项目目录。

最终结果文件由本项目 Python 代码写入，避免 Gemini CLI 直接操作仓库。

## run_all.py 集成建议

短期不建议让 `run_all.py` 默认切换到 Gemini CLI。建议先加参数：

```bash
python run_all.py --reviewer gemini-api
python run_all.py --reviewer gemini-cli
python run_all.py --skip-review
```

兼容策略：

- 默认行为保持现状，避免破坏旧流程。
- `--reviewer gemini-cli` 调用 `agent/gemini_cli_review.py`。
- `--skip-review` 只跑到图表导出，方便人工或 Codex 复评。

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

## 推荐实施顺序

1. 安装 Gemini CLI 并完成 Google 账号登录。
2. 用一张已导出的 K 线图做图片输入探针。
3. 新增 `gemini_cli_review.yaml`。
4. 新增 `agent/gemini_cli_review.py`。
5. 用 1 到 3 只候选股做小批量复评。
6. 生成 `suggestion.json` 后用 `run_all.py --start-from 5` 或等价方式验证汇总打印。
7. 最后再考虑把 `--reviewer gemini-cli` 集成到 `run_all.py`。
