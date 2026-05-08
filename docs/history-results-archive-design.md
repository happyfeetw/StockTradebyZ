# 每日最终结果归档设计

## 目标

为本地选股工作台增加一个轻量的历史结果层，方便按日期和策略回看每天最终选出的股票。

本功能只做归档和回看，不做跨日追踪、多 run 对比、手动标记、导出或每日复盘备注。

## 数据结构

归档根目录为 `data/history/`：

```text
data/history/
  index.json
  2026-05-06/
    summary.json
    all.json
    b1.json
    brick.json
```

文件职责：

- `index.json`：历史日期索引，供工作台快速列出可回看的日期。
- `summary.json`：单日概要，包括候选数、已复评数、推荐数和策略分布。
- `all.json`：当天所有策略合并结果。
- `{strategy}.json`：当天某个策略的结果，例如 `b1.json`、`brick.json`。

单条结果保留这些核心字段：

```json
{
  "date": "2026-05-06",
  "run_id": "2026-05-07_091405",
  "code": "600105",
  "strategy": "b1",
  "close": 43.68,
  "turnover_n": 123456789.0,
  "brick_growth": null,
  "review": {
    "verdict": "PASS",
    "total_score": 4.5,
    "signal_type": "trend_start",
    "comment": "..."
  },
  "rank": 1,
  "chart": "data/kline/2026-05-06/600105_day.jpg",
  "status": "recommended"
}
```

`status` 只表达归档当时的最终状态：

- `recommended`：进入 `suggestion.json` 的推荐列表。
- `reviewed`：有复评结果，但未进入推荐列表。
- `unreviewed`：没有复评结果。

## 流程位置

归档作为主流程最后的独立步骤执行：

```text
拉取 K 线数据
量化初选
导出候选图表
Gemini CLI 复评
归档当日结果
```

归档脚本只读取现有产物，不反向影响初选、图表导出或 Gemini CLI 复评：

- `data/candidates/candidates_latest.json`
- `data/review/{pick_date}/suggestion.json`
- `data/review/{pick_date}/{code}.json`
- `data/kline/{pick_date}/{code}_day.jpg`

## 使用方式

自动归档：

- workbench 的 `完整流程`
- workbench 的 `跳过抓取`
- workbench 的 `只跑复评`

手动补归档：

```bash
python -m pipeline.archive_results
```

指定日期：

```bash
python -m pipeline.archive_results --date 2026-05-06
```

## 同日多策略运行

如果同一天先跑 `B1`，再跑 `brick`，后一次初选不能覆盖前一次策略结果。

workbench 执行初选时会调用：

```bash
python -m pipeline.cli preselect --merge-same-date
```

这个参数的语义是：

- 同一 `pick_date` 下，按策略合并 `data/candidates/candidates_{date}.json` 和 `candidates_latest.json`。
- 重跑某个策略时，替换该策略旧结果。
- 保留当天其他策略已经产生的候选。
- 如果一次运行启用 `B1 + 砖型图`，会同时替换这两个策略的旧结果。

这样复评、结果中心和历史归档仍然读取同一个候选契约文件，但文件内容会保留当天已跑过的各策略最终结果。

## UI

workbench 新增 `历史结果` 页面。

页面能力：

- 选择归档日期。
- 选择策略：全部 / b1 / brick。
- 查看当天概要指标。
- 查看结果表格。
- 选择单票查看图表和 Gemini 复评 JSON。

结果中心仍保留“最新结果”视角；历史结果页只读取 `data/history/`，用于回看已经归档的最终结果。
