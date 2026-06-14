# Z Quality Layer

Z 精选池质量层位于多模型共识之后，用于把 `zettaranc-perspective` 中和选股质量相关的规则沉淀为本项目自己的裁决层。

## Position

```text
量化初选
-> 多模型复评
-> 共识汇总
-> Z 质量裁决
-> Z 精选池 / 观察池
```

第一版只处理已经经过完整多模型复评的候选全集，不直接绕过多模型复评处理原始量化初选池。这样可以同时利用：

- 多模型看图结论和分歧。
- 本项目 `data/raw` 的客观 K 线特征。
- Z 体系中的质量审美、硬伤排除和条件化次日预案。

## Data Boundary

本层不依赖 `zettaranc-perspective` skill 自己的数据层，不调用 `zt analyze`、`zt screen`、`zt backtest`，也不读取 skill 的 SQLite 数据库。

运行时输入来自本项目：

- `data/review_consensus/{batch_id}/summary.json`
- `data/review_consensus/{batch_id}/decisions.json`
- `data/review_consensus/{batch_id}/details.json`
- 各模型单股 review JSON
- `data/raw/{code}.csv`
- `data/kline/{pick_date}/{code}_day.jpg`
- `config/z_quality_rules.yaml`
- `agent/z_quality_prompt.md`

`zettaranc-perspective` 在这里只作为知识来源。已提炼的规则放入项目自己的配置和 prompt，不作为运行时依赖。

第一版主要参考的知识文件：

- `knowledge/trading-core.md`：四层交易模块、B1 入场三问、B1/B2/B3、砖型节奏。
- `knowledge/breathing-theory.md`：量价呼吸与蜈蚣图过滤。
- `knowledge/advanced-patterns.md`：异动、缩量回踩、B2/B3 和高级形态。
- `knowledge/sell-discipline.md`：买后持有/失效纪律。

## Output

多模型流程默认会在共识完成后自动运行：

```bash
python agent/multi_model_review.py --config config/multi_model_review.yaml
```

也可以单独重跑 Z 层：

```bash
python agent/z_quality_review.py --config config/z_quality_rules.yaml
```

默认读取 `data/review_consensus/latest.json`，输出：

- `data/z_quality/{batch_id}/summary.json`
- `data/z_quality/{batch_id}/decisions.json`
- `data/z_quality/{batch_id}/decisions.csv`
- `data/z_quality/{batch_id}/llm_inputs.json`
- `data/z_quality/{batch_id}/items/{review_key}.json`
- `data/z_quality/{batch_id}/items/{review_key}.input.json`

第一版是 `local_rules_dry_run`：先用本地规则给出 deterministic 预裁决，同时写出每只票可喂给大模型的输入包。后续接入 Gemini/Codex 时，应使用这些 input JSON 和 `agent/z_quality_prompt.md` 生成最终解释。

共识 summary 会在自动后处理后增加 `z_quality` 索引，指向对应的 Z summary 和 decisions 文件。

## Verdicts

- `A_SELECT`：高质量精选候选，只表示值得进入重点观察池，不表示直接买入。
- `B_WATCH`：有结构亮点但不够完美，进入观察池。
- `C_REVIEW_ONLY`：有分歧或证据不足，只适合复盘研究。
- `REJECT`：硬伤明显，不进入 Z 精选池。

Z 质量层使用自己的二次质量分阈值，不等同于模型复评的 `suggest_min_score=4.0`，也不直接替代策略 profile 的 PASS/WATCH 门槛。默认配置中：

- `z_select_min_quality_score: 4.2`：Z 精选阈值，且不能触发观察上限。
- `z_watch_min_quality_score: 3.4`：Z 观察阈值。
- 低于 Z 观察阈值但仍有正面证据时，归入 `C_REVIEW_ONLY` 作为复盘样本。

## First-Version Rules

本地硬规则优先处理：

- 共识不完整。
- 多数模型 common gate 失败。
- 多数模型触发 hard veto。
- 放量阴线。
- 放量长上影。
- 蜈蚣图倾向。
- 近 20 日最大量出现在下跌 K 线且明显放量。

本地观察上限包括：

- 信号日前的左侧 20 日高点压力贴近。
- 距离最近可见支撑过远。
- 模型分歧过大。
- 本地 K 线数据不可用。

本地加分证据包括：

- 多模型全票或多数推荐。
- 多数模型公共交易资格通过。
- 经典图形完成度高。
- 前期有效资金异动。
- 贴近可见支撑。
- 回调缩量。
- 量价呼吸健康。
- 近 20 日最大量在上涨 K 线。

## Operational Boundary

Z 质量层会给出条件化次日预案和买后观察纪律，但它不是自动买入指令。输出中的 `next_day_plan` 和 `hold_plan` 只供人工决策参考。
