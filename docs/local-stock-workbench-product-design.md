# 本地选股工作台产品设计方案

## 1. 产品定位

本地选股工作台是一个独立入口，用于把当前项目的命令行选股流程产品化：

- 不改造现有 `dashboard/app.py`。
- 不替代当前 CLI 脚本。
- 不改变现有数据产物格式。
- 在本地浏览器中提供策略选择、参数配置、流程启动、日志查看、结果筛选和单票复盘能力。

目标用户是本项目操作者本人，核心诉求不是多用户系统，而是减少命令行切换和 YAML 手改成本。

## 2. 独立入口

建议新增独立目录：

```text
workbench/
  app.py
  pages/
    run_center.py
    strategy_config.py
    review_config.py
    result_center.py
    stock_view.py
  services/
    config_builder.py
    run_manager.py
    process_runner.py
    result_loader.py
  assets/
    style.css
```

启动脚本建议：

```text
start_workbench
```

启动命令：

```bash
./start_workbench
```

脚本职责：

- 进入项目根目录。
- 加载 `~/.zshrc`。
- 如果存在 `TS_TOKEN` 且不存在 `TUSHARE_TOKEN`，自动映射。
- 检查 `.venv`、`streamlit`、`gemini` CLI。
- 启动 `workbench/app.py`，默认端口可设为 `8601`，避免和旧 dashboard 的 `8501` 混用。

## 3. 核心用户流程

### 3.1 首次进入

工作台先展示环境状态：

- Tushare Token：已配置 / 未配置。
- Gemini CLI：已安装 / 未安装。
- Gemini 登录态：可选探测，失败时只给提示，不阻断非复评流程。
- 原始数据目录：是否存在 `data/raw`。
- 最新候选文件：是否存在 `data/candidates/candidates_latest.json`。
- 最新复评汇总：是否存在 `data/review/{pick_date}/suggestion.json`。

用户可以直接进入运行中心，也可以进入策略配置。

### 3.2 配置策略

用户选择一个策略预设：

- B1 策略。
- 砖型图策略。
- B1 + 砖型图。
- 自定义。

选择预设后，页面展示可编辑参数。用户点击“保存为本次运行配置”，生成运行快照，不直接改默认 YAML。

### 3.3 启动流程

用户在运行中心选择运行模式：

- 完整流程：抓数据 -> 初选 -> 导图 -> Gemini CLI 复评 -> 汇总。
- 跳过抓取：初选 -> 导图 -> Gemini CLI 复评 -> 汇总。
- 只跑初选。
- 只导出图表。
- 只跑 Gemini CLI 复评。
- 只查看已有结果。

点击开始后，工作台展示：

- 当前步骤。
- 子进程日志。
- 已耗时。
- 候选数量。
- Gemini CLI 已用请求数。
- 是否命中限流、每日预算或单次请求上限。

### 3.4 查看结果

运行完成后进入结果中心：

- 候选股票列表。
- Gemini 推荐列表。
- 按策略筛选：`b1`、`brick`。
- 按结论筛选：`PASS`、`WATCH`、`FAIL`。
- 按分数排序。
- 点击股票进入单票复盘。

## 4. 页面设计

### 4.1 运行中心

定位：工作台首页。

主要模块：

- 环境状态条。
- 运行模式选择。
- 策略预设摘要。
- Gemini CLI 额度摘要。
- 开始 / 停止按钮。
- 实时日志区域。
- 最近运行记录。

关键交互：

- 运行前显示预计动作，而不是直接执行。
- 点击开始后锁定本次配置，避免运行过程中修改参数造成不可复现。
- 停止按钮只终止当前子进程，不删除已生成文件。
- 如果已有未完成 Gemini CLI 复评，提示可以断点续跑。

### 4.2 策略配置

定位：让用户不用手改 `rules_preselect.yaml`。

B1 常用参数：

- `enabled`
- `j_threshold`
- `j_q_threshold`
- `zx_m1`
- `zx_m2`
- `zx_m3`
- `zx_m4`

砖型图常用参数：

- `enabled`
- `daily_return_threshold`
- `brick_growth_ratio`
- `min_prior_green_bars`
- `zxdq_ratio`
- `require_zxdq_gt_zxdkx`
- `require_weekly_ma_bull`

砖型图高级参数折叠展示：

- `n`
- `m1`
- `m2`
- `m3`
- `t`
- `shift1`
- `shift2`
- `sma_w1`
- `sma_w2`
- `sma_w3`
- `zxdkx_m1`
- `zxdkx_m2`
- `zxdkx_m3`
- `zxdkx_m4`
- `wma_short`
- `wma_mid`
- `wma_long`

配置原则：

- 默认只暴露常用参数。
- 高级参数必须折叠。
- 每个数值参数需要显示默认值、当前值和简短含义。
- 用户点击“恢复预设”可以回到 B1、砖型图或组合策略默认值。

### 4.3 复评配置

定位：管理 Gemini CLI 复评成本和节奏。

主要参数：

- `batch_size`：默认 5，最大 5。
- `request_delay`：默认 10 秒。
- `max_requests_per_run`：默认 50。
- `daily_request_budget`：默认 80。
- `skip_existing`：默认开启。
- `fallback_to_single_on_batch_error`：默认开启。
- `suggest_min_score`：默认 4.0。

页面要明确提示：

- `batch_size` 降低每分钟请求数，但不等于降低每日额度消耗。
- 批量失败会使用同一模型自动拆批并最终逐只复评。
- 达到每日预算会停止，保留已完成结果。

### 4.4 结果中心

定位：选股结果的工作台，而不是只打印终端摘要。

主要模块：

- 最新运行概览。
- 候选列表。
- Gemini 推荐列表。
- 未达标列表。
- 未复评 / 跳过列表。
- 单股 JSON 原始结果查看。

列表字段：

- 股票代码。
- 来源策略。
- 收盘价。
- `brick_growth`。
- Gemini `verdict`。
- Gemini `total_score`。
- `signal_type`。
- 评论。

### 4.5 单票复盘

定位：替代用户在结果和图表之间来回找文件。

内容：

- 日线图。
- 周线图。
- 候选信息。
- Gemini 评分拆解。
- Gemini 评论。
- 原始 JSON 折叠区。

说明：

- 可以复用现有 `dashboard/components/charts.py` 的图表函数。
- 但入口、页面结构和状态管理属于 `workbench/`，不要修改旧 `dashboard/app.py`。

## 5. 配置与运行快照

不要让工作台直接覆盖默认配置。每次运行生成快照：

```text
data/runs/
  2026-04-29_153000/
    run_config.json
    fetch_kline.yaml
    rules_preselect.yaml
    gemini_cli_review.yaml
    run.log
    run_state.json
```

`run_config.json` 记录：

- run_id。
- 创建时间。
- 运行模式。
- 策略预设名称。
- 是否跳过抓取。
- 是否跳过复评。
- 各配置文件路径。
- 输出目录。

`run_state.json` 记录：

- 当前状态：`idle`、`running`、`success`、`failed`、`stopped`。
- 当前步骤。
- 子进程 PID。
- 开始时间。
- 结束时间。
- 错误信息。

## 6. 与现有流程的衔接

工作台不重写核心逻辑，优先调用现有脚本：

- 抓取：`python -m pipeline.fetch_kline`
- 初选：`python -m pipeline.cli preselect --config <run_rules_yaml>`
- 导图：`python dashboard/export_kline_charts.py`
- 复评：`python agent/gemini_cli_review.py --config <run_gemini_yaml>`

如果现有脚本暂时不支持某个配置覆盖，则后续编码阶段再做最小改造。原则是让核心脚本保持 CLI 可用，工作台只是更方便的本地操作层。

## 7. 技术选型建议

第一阶段建议继续使用 Streamlit：

- 项目已有 Streamlit 依赖。
- 能快速完成本地工作台。
- 适合日志、表格、配置表单和图表。
- 不需要额外前端构建链。

但工作台必须是独立入口：

- 新增 `workbench/app.py`。
- 不修改 `dashboard/app.py`。
- 可以复用 `dashboard/components/charts.py`。

## 8. UI/UX 设计方向

整体气质：

- 本地量化工作台。
- 信息密度较高。
- 安静、专业、偏工具型。
- 避免营销风格首页。
- 避免大面积装饰图。

布局建议：

- 左侧主导航。
- 顶部环境状态条。
- 主区域按任务组织。
- 运行日志使用固定高度面板。
- 配置表单分组清晰，默认折叠高级项。

颜色建议：

- 中性色为主。
- 上涨/通过/成功使用克制红色或绿色，但不能让页面变成单一红绿配色。
- 警告、限流、额度状态需要明显但不刺眼。

关键体验：

- 运行前让用户知道会执行哪些步骤。
- 配置修改后能看到“未保存”状态。
- 每次运行都有可追溯配置快照。
- 结果列表能快速筛选和跳转单票。

## 9. 给 Gemini 做 UI/UX 设计的输入

可以把下面这段发给 Gemini：

```text
请为一个本地 A 股选股工作台设计 UI/UX。它不是网页产品，不需要营销首页，
而是一个本地浏览器运行的工具型应用。用户是单人操作者。

核心流程：
1. 检查本地环境：Tushare Token、Gemini CLI、数据目录、候选文件。
2. 选择运行模式：完整流程、跳过抓取、只跑初选、只导图、只复评、只看结果。
3. 选择策略：B1、砖型图、B1 + 砖型图、自定义。
4. 编辑策略参数：B1 常用参数、砖型图常用参数，高级参数折叠。
5. 编辑 Gemini CLI 复评参数：batch_size、request_delay、max_requests_per_run、daily_request_budget、skip_existing。
6. 点击开始，查看实时日志和进度。
7. 查看候选列表、推荐列表、未达标列表。
8. 点击股票进入单票复盘，展示日线、周线、Gemini 评分和评论。

设计要求：
- 信息密度高，专业工具风格。
- 左侧导航，顶部环境状态条。
- 不要营销 hero。
- 不要复杂动画。
- 配置表单要清晰，常用参数优先，高级参数折叠。
- 运行日志和结果表格是核心。
- 页面要适合长时间使用。

请输出：
1. 信息架构。
2. 主要页面 wireframe。
3. 每个页面的关键组件。
4. 状态设计：空状态、运行中、成功、失败、限流、部分完成。
5. 视觉风格建议。
```

## 10. 分阶段实施计划

### 阶段 1：独立入口与只读结果

- 新增 `workbench/app.py`。
- 新增启动脚本 `start_workbench`。
- 展示环境状态。
- 展示候选列表和复评结果。
- 单票复盘复用现有图表组件。

验收标准：

- 不影响旧 dashboard。
- `./start_workbench` 能启动新入口。
- 能读取现有 `candidates_latest.json` 和 `suggestion.json`。

### 阶段 2：策略配置与运行快照

- 增加策略预设。
- 增加配置表单。
- 生成 `data/runs/{run_id}/` 配置快照。

验收标准：

- 不覆盖默认 YAML。
- 能生成可复现运行配置。

### 阶段 3：流程启动与日志

- 从工作台启动抓取、初选、导图、复评。
- 展示实时日志。
- 支持停止当前运行。

验收标准：

- 至少支持“跳过抓取”完整跑通。
- 停止后不删除已生成结果。

### 阶段 4：结果筛选与复盘增强

- 结果表格筛选、排序。
- 单票评分详情。
- 未完成复评提示和断点续跑入口。

验收标准：

- 用户不需要打开 JSON 文件也能完成一次筛选和复盘。
