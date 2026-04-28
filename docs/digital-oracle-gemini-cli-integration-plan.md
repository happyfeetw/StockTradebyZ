# Gemini CLI 与 Digital Oracle 集成方案

本文档描述将当前 AgentTrader 主流程扩展为“量化初选 + 图表复评 + 交易数据验证”的方案。

## 目标流程

保持原始流程不被侵入，在现有步骤之间新增独立步骤：

```text
1. 拉取 K 线数据
2. 量化初选
3. 导出候选图表
4. 图表复评：Gemini CLI / Gemini API
5. Digital Oracle 独立市场验证
6. 打印最终推荐
```

核心原则：

- 每个步骤只消费上一步的稳定产物。
- 不把 Digital Oracle 逻辑塞进 `preselect`、图表导出或 Gemini 复评内部。
- 不改变现有 `suggestion.json` 的主体结构，只在推荐项里新增字段。
- 原 Gemini API 版保留，Gemini CLI 作为并行复评实现。

## 输出契约

现有 `suggestion.json` 的 `recommendations` 项保持原字段：

```json
{
  "rank": 1,
  "code": "600000",
  "verdict": "PASS",
  "total_score": 4.2,
  "signal_type": "trend_start",
  "comment": "..."
}
```

新增字段为 `digital_oracle_suggest`：

```json
{
  "rank": 1,
  "code": "600000",
  "verdict": "PASS",
  "total_score": 4.2,
  "signal_type": "trend_start",
  "comment": "...",
  "digital_oracle_suggest": {
    "verdict": "confirm",
    "risk_level": "medium",
    "position_advice": "normal",
    "market_regime": "risk_on",
    "confidence": "medium",
    "reasons": [
      "指数趋势支持",
      "资金流未出现明显背离",
      "融资融券风险中性"
    ],
    "monitor": [
      {
        "signal": "CSI300 trend",
        "threshold": "跌破20日均线",
        "meaning": "市场环境转弱，降低仓位"
      }
    ]
  }
}
```

建议枚举：

- `verdict`: `confirm` | `caution` | `reject` | `insufficient_data`
- `risk_level`: `low` | `medium` | `high` | `unknown`
- `position_advice`: `normal` | `reduced` | `watch_only` | `avoid`
- `confidence`: `low` | `medium` | `high`

## Gemini CLI 复评步骤

### 作用

替代或并行现有 `agent/gemini_review.py` 的 Gemini API Key 调用方式。

### 新增文件

```text
config/gemini_cli_review.yaml
agent/gemini_cli_review.py
```

### 配置草案

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

### 调用方式

```bash
python agent/gemini_cli_review.py
```

内部通过 `subprocess.run()` 调用：

```bash
gemini -p "<复评提示词 + 图片路径>" --output-format json
```

如果实测模型参数可用，再追加：

```bash
gemini --model "<model>" -p "<复评提示词 + 图片路径>" --output-format json
```

### 额度与限速策略

Gemini CLI 存在分钟级请求速率限制和每日请求次数限制。复评脚本必须把额度视为一等约束：

- 默认开启 `skip_existing: true` 时，只处理缺失的单股结果。
- 默认限制单次运行最大复评数量，例如 `max_requests_per_run: 20`。
- 使用 `request_delay` 控制请求间隔，不允许零间隔批量打满。
- 维护本地每日调用计数，例如 `data/review/.gemini_cli_usage.json`。
- 达到 `daily_request_budget` 后停止本次复评，保留已完成结果。
- 遇到 CLI 返回 rate limit / quota / too many requests 等错误时，不重试刷屏；记录失败原因并停止或长时间退避。
- 支持小批量续跑，优先保证已生成的 `{code}.json` 可复用。

这意味着 Gemini CLI 复评不应默认处理全部候选股。更稳妥的顺序是：

1. 先处理图表复评前 N 名或全部候选中的小批量。
2. `skip_existing` 断点续跑。
3. 如果额度耗尽，保留部分 `suggestion.json` 或明确标记未复评股票。

### 解析策略

1. 读取 Gemini CLI stdout。
2. 若 stdout 是 CLI 外层 JSON，优先读取 `response` 等文本字段。
3. 否则将 stdout 作为模型正文。
4. 调用现有 `BaseReviewer.extract_json()` 提取评分 JSON。
5. 补充 `code` 字段。
6. 继续复用 `BaseReviewer.generate_suggestion()` 生成 `suggestion.json`。

## Digital Oracle 独立市场验证步骤

### 作用

Digital Oracle 不负责图表复评，也不负责替代 B1/砖型图初选。它作为交易数据校验层，消费图表复评后的 `suggestion.json`，对最终推荐做多信号确认或降级。

它回答的问题是：

- 当前 A 股市场环境是否支持进攻？
- 推荐股是否有估值、资金流、融资融券或指数环境层面的反向信号？
- 图表复评给出的 PASS 是否应保持、降为谨慎，或剔除？

### 新增文件

```text
config/oracle_review.yaml
agent/oracle_review.py
```

### 输入

```text
data/review/{pick_date}/suggestion.json
data/candidates/candidates_latest.json
data/raw/{code}.csv
```

可选实时数据源：

- `TUSHARE_TOKEN` 或 `TS_TOKEN`
- Tushare daily/basic/moneyflow/margin_detail
- 指数：`000300.SH`、`000001.SH`、`399001.SZ`
- ETF 或行业代理：后续按需要扩展

### 配置草案

```yaml
suggestion: data/review/latest
output_mode: update_suggestion

token_env:
  primary: TUSHARE_TOKEN
  fallback: TS_TOKEN

max_recommendations: 10
request_delay: 0.5

market_indices:
  - ts_code: 000300.SH
    asset: index
    name: CSI300
  - ts_code: 000001.SH
    asset: index
    name: SSE Composite
  - ts_code: 399001.SZ
    asset: index
    name: SZSE Component

stock_signals:
  price_history: true
  daily_basic: true
  moneyflow: true
  margin_detail: true

fallback_to_local_raw: true
```

### 输出

主要输出是更新后的：

```text
data/review/{pick_date}/suggestion.json
```

每个 `recommendations[]` 项增加 `digital_oracle_suggest` 字段。

可选调试产物：

```text
data/oracle/{pick_date}/oracle_report.md
data/oracle/{pick_date}/oracle_summary.json
data/oracle/{pick_date}/{code}.json
```

## Digital Oracle 信号设计

### 市场环境层

建议使用 3 个指数做环境确认：

- `000300.SH` 沪深 300：大盘核心风险偏好。
- `000001.SH` 上证指数：主板环境。
- `399001.SZ` 深证成指：成长风格环境。

基础判断：

- 最近 20/60 日趋势。
- 是否跌破关键均线。
- 近期波动是否异常放大。
- 指数与候选股方向是否共振。

### 个股交易数据层

对每只推荐股读取：

- 本地 `data/raw/{code}.csv`：作为最低依赖的价格与成交量信号。
- Tushare `daily_basic`：估值、市值、换手、量比。
- Tushare `moneyflow`：大单/特大单净流入。
- Tushare `margin_detail`：融资融券风险。

注意：Tushare 权限可能不足。若 `daily_basic`、`moneyflow`、`margin_detail` 报权限错误，脚本应降级为本地 K 线与可用指数信号，并输出 `insufficient_data` 或降低 `confidence`。

### 建议判定

`confirm`：

- 图表复评 PASS。
- 市场环境不弱。
- 个股资金流或成交结构没有明显背离。
- 估值/融资风险没有极端异常。

`caution`：

- 图表复评 PASS，但指数环境转弱。
- 个股资金流分歧。
- 融资或波动风险偏高。

`reject`：

- 市场环境明显 risk-off。
- 个股出现强烈反向资金流或高杠杆风险。
- 交易数据与图表结论明显冲突。

`insufficient_data`：

- Tushare 权限不足且本地数据不足以做判断。
- 数据日期与 `pick_date` 明显不匹配。

## run_all.py 集成建议

原始脚本当前只有 1 到 4 步加最终打印。后续可扩展：

```bash
python run_all.py --reviewer gemini-cli --oracle
python run_all.py --reviewer gemini-api --oracle
python run_all.py --skip-review
python run_all.py --skip-oracle
```

实施顺序建议：

1. 先保证原始项目在本地跑通。
2. 再新增 Gemini CLI 复评脚本。
3. 再新增 Oracle 独立脚本。
4. 最后修改 `run_all.py`，把两个新能力作为可选步骤接入。

## 本地跑通优先级

在实施新能力前，先验证原始项目：

1. `fetch_kline.py` 能用 Tushare token 拉取真实数据。
2. `pipeline.cli preselect` 能生成 candidates。
3. `export_kline_charts.py` 能生成候选日线图。
4. 原 Gemini API 复评如果无 API Key，可暂时不作为本地跑通阻塞；Gemini CLI 探针单独验证。

当前代码读取 `TUSHARE_TOKEN`，而用户本地变量名为 `TS_TOKEN`。运行时需要做兼容：

```bash
TUSHARE_TOKEN="$TS_TOKEN" python -m pipeline.fetch_kline
```

后续可以在代码里正式支持 `TS_TOKEN` fallback。

## 验收标准

### 原始项目跑通

- `data/raw/*.csv` 有真实行情数据。
- `data/candidates/candidates_latest.json` 生成成功。
- `data/kline/{pick_date}/*_day.jpg` 生成成功。
- 至少能完成一条候选链路的图表导出。

### Gemini CLI 跑通

- `which gemini` 可找到命令。
- `gemini --version` 可输出版本。
- `gemini -p "..." --output-format json` 能成功返回。
- Gemini CLI 能读取本地 K 线图片并输出图表描述。
- 小批量复评遵守 `request_delay`。
- 达到 `max_requests_per_run` 或每日预算后能正常停止并支持续跑。

### Oracle 跑通

- `agent/oracle_review.py` 能读取 `suggestion.json`。
- 每条推荐项新增 `digital_oracle_suggest`。
- 原字段不被破坏。
- 权限不足时能降级输出，而不是中断全流程。

## 风险

- 全量 Tushare 拉取所有股票从 2019 年至今可能耗时较长，也可能触发限流。
- 当前 `.venv` 依赖不完整，缺少 `PyYAML`、`streamlit`、`google-genai`、`kaleido` 等运行依赖。
- `requirements.txt` 未包含 Gemini CLI 方案需要的 Python 侧脚本依赖调整。
- Gemini CLI 登录态依赖本机用户环境，不适合直接迁移到服务器。
- Gemini CLI 有每分钟和每日请求限制，批量复评必须做限速、预算和断点续跑。
- Digital Oracle 的部分 Tushare 接口取决于账号权限，必须支持降级。
