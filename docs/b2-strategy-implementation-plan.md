# B2 战法实现计划

## 目标

在系统中新增 `b2` 量化初选策略。B2 的定位是：**B1 低位信号出现后的 1 到 2 个交易日内，股价用实体强阳线、量能确认和 J 值拐头向上证明多头开始接管**。

B2 不是单日大阳线策略，也不是 B1 的替代策略。它是 B1 后的确认型买点，因此必须作为独立策略来源保留，后续复评、模拟交易、买卖点和持股逻辑都按 `strategy="b2"` 独立处理。

## 核心规则

对选股日 `T`，B2 必须同时满足：

1. `T-1` 或 `T-2` 满足系统 B1 条件。
2. `T` 日 KDJ 的 `J` 值相对触发 B1 的那一天向上，即 `J_T > J_B1_day`。
3. `T` 日 `J < 55`。
4. `T` 日收盘涨幅大于 4%，即 `close_T / close_T-1 - 1 > 0.04`。
5. `T` 日必须是有实体阳线，过滤假阴真阳、十字星和极小实体阳线。
6. `T` 日满足量能确认：
   - 正常放量：`volume_T > volume_T-1`。
   - 或近似平量：`volume_T >= volume_T-1 * flat_volume_ratio`，且 K 线为严格阳包阴。
7. `T` 日满足所有策略通用的基础趋势条件：`close > zxdkx`、`zxdq > zxdkx`、周线均线多头。
8. 上影线不是硬过滤条件，只进入 `Candidate.extra` 和质量评分。

## 关键设计决策

### 同股多策略保留

同一只股票可以同时命中 `b1`、`b2`、`brick`。不同策略对应不同买点、卖点和持股逻辑，因此候选去重必须按 `(code, strategy)`，不能只按 `code`。

示例：

```json
[
  {"code": "300001", "strategy": "b1"},
  {"code": "300001", "strategy": "b2"}
]
```

这两条候选都应该保留，并在复评、归档和模拟交易中按策略独立处理。

### `_b1_pick` 的含义

`_b1_pick` 是逐日布尔列：

```text
_b1_pick[date] = 该 date 是否满足系统 B1 条件
```

它不是专门筛 `T` 日，也不是专门筛 `T-1` 日。B2 判断 `T` 日时必须使用偏移后的 `_b1_pick`：

```python
b1_lag_1 = df["_b1_pick"].shift(1)
b1_lag_2 = df["_b1_pick"].shift(2)
recent_b1_ok = b1_lag_1 | b1_lag_2
```

也就是：

```text
_b1_pick.shift(1)[T] 检查 T-1 是否 B1
_b1_pick.shift(2)[T] 检查 T-2 是否 B1
```

`_b1_pick[T]` 不作为 B2 前置信号。

如果 `T-1` 和 `T-2` 都满足 B1，优先采用最近的 `T-1` 作为 `prior_b1_day`，用于计算 `prior_b1_lag` 和 `J_T > J_B1_day`。

### B1 前置定义

第一版 B2 复用系统当前 B1 的完整过滤器链生成 `_b1_pick`，包括：

- `KDJQuantileFilter`
- `ZXConditionFilter`
- `WeeklyMABullFilter`
- `MaxVolNotBearishFilter`

B2 的前置 B1 本质含义是：B1 当日 `J` 在低位，B2 当日 `J` 相对 B1 当日拐头向上。这个拐头不替代系统 B1 过滤器，而是在 B2 当日额外验证：

```python
j_turn_up = j_today > prior_b1_j
```

### 基础趋势条件

知行线两条线和周线趋势是所有策略的基础条件，不是 B1 专属条件。B2 当日也必须满足：

```text
close_T > zxdkx_T
zxdq_T > zxdkx_T
wma_bull_T = true
```

这些条件不做可选开关。

### 强阳线条件

`收盘涨幅 > 4%` 只能说明今日收盘相对昨日收盘上涨，不能证明今日 K 线本身是阳线。例如高开低走的假阴真阳也可能满足涨幅大于 4%。B2 的强阳确认必须同时检查当日实体阳线：

```python
daily_return = close_t / close_prev - 1
today_body_pct = (close_t - open_t) / open_t

price_confirm_ok = (
    daily_return > min_return
    and close_t > open_t
    and today_body_pct >= min_today_body_pct
)
```

默认参数：

```yaml
min_return: 0.04
min_today_body_pct: 0.003
```

`min_today_body_pct = 0.003` 表示今日阳线实体至少为开盘价的 0.3%，用于过滤十字星、小实体和假阴真阳。后续如果需要更强调中大阳线，可以调高到 `0.005`。

### 量能确认

放量是核心条件，平量只是极端环境下的退让条件。两者不是等价关系。

量能确认逻辑：

```python
volume_ratio = volume_t / volume_prev

volume_confirm_ok = (
    volume_ratio > volume_ratio_min
    or (
        volume_ratio >= flat_volume_ratio
        and strict_yang_bao_yin
    )
)
```

默认参数：

```yaml
volume_ratio_min: 1.0
flat_volume_ratio: 0.98
```

解释：

- `volume_ratio > 1.0`：正常放量，通过量能确认。
- `0.98 <= volume_ratio <= 1.0`：近似平量，必须额外满足严格阳包阴。
- `volume_ratio < 0.98`：缩量，不符合 B2。

`flat_volume_ratio` 第一版建议使用 `0.98`。`0.90` 会把缩量 10% 也纳入平量，过松；`0.99` 对真实盘面误差较敏感。`0.98` 允许 2% 内的近似平量，符合“退而求其次但不明显放松”的要求。

### 严格阳包阴

阳包阴只在近似平量分支中启用。正常放量路径不要求阳包阴，但仍必须满足“当日有实体阳线”和“收盘涨幅大于 4%”。

严格阳包阴定义：

```python
prev_body_pct = (prev_open - prev_close) / prev_close
today_body_pct = (close_t - open_t) / open_t

prev_bear_body_ok = (
    prev_close < prev_open
    and prev_body_pct >= min_yang_bao_yin_body_pct
)

today_bull_body_ok = (
    close_t > open_t
    and today_body_pct >= min_today_body_pct
)

strict_yang_bao_yin = (
    prev_bear_body_ok
    and today_bull_body_ok
    and open_t <= prev_close
    and close_t >= prev_open
)
```

默认参数：

```yaml
min_yang_bao_yin_body_pct: 0.003
```

该定义要求昨日是有实体阴线、今日是有实体阳线，并且今日阳线实体包住昨日阴线实体。这样可以避免把十字星、小实体、靠影线形成的弱包裹误判为阳包阴。

### 上影线

无上影线是加分项，不是硬过滤。

计算：

```python
upper_shadow_ratio = (high_t - close_t) / max(high_t - low_t, eps)
```

建议写入 `Candidate.extra`，同时参与质量评分：

- `upper_shadow_ratio <= 0.03`：接近无上影，质量较高。
- `0.03 < upper_shadow_ratio <= 0.15`：可接受。
- `upper_shadow_ratio > 0.15`：冲高回落明显，降低质量分，但不直接剔除。

## 配置设计

在 `config/rules_preselect.yaml` 的 `b1:` 之后、`brick:` 之前新增：

```yaml
# ── B2 策略参数（B1 后强阳确认） ────────────────────────────────────
b2:
  enabled: false

  # 前置 B1 窗口：T-1 或 T-2 需满足 B1
  b1_lookback: 2

  # 当日强阳确认
  min_return: 0.04
  min_today_body_pct: 0.003

  # KDJ 安全区和拐头确认
  j_ceiling: 55.0
  require_j_turn_up: true

  # 量能确认
  volume_ratio_min: 1.0
  flat_volume_ratio: 0.98
  min_yang_bao_yin_body_pct: 0.003

  # 质量评分，不做硬过滤
  upper_shadow_soft_limit: 0.15
```

B2 的知行线、KDJ、周线参数继承 `b1` 段配置，确保 B1 前置和 B2 当日基础趋势使用同一套指标口径。

## Selector 设计

在 `pipeline/Selector.py` 中新增以下过滤器和 `B2Selector`，放在 `B1Selector` 之后、`BrickChartSelector` 之前。

### `DailyReturnFilter`

职责：判断 `T` 日相对 `T-1` 的收盘涨幅是否大于阈值。

```python
daily_return = close_t / close_prev - 1
daily_return_ok = daily_return > min_return
```

注意使用严格大于 `>`，对应“涨幅大于 4%”。

### `BullBodyFilter`

职责：判断 `T` 日是否为有实体阳线，过滤假阴真阳、十字星和极小实体。

```python
today_body_pct = (close_t - open_t) / open_t
today_bull_body_ok = close_t > open_t and today_body_pct >= min_today_body_pct
```

该过滤器是 B2 硬条件，不只用于阳包阴分支。

### `VolumeConfirmFilter`

职责：实现“放量优先，平量需阳包阴”的量能确认。

```python
vol_up = volume_t > volume_prev * volume_ratio_min
vol_flat = volume_t >= volume_prev * flat_volume_ratio
volume_confirm_ok = vol_up | (vol_flat & strict_yang_bao_yin)
```

`strict_yang_bao_yin` 只在 `vol_up` 不成立但 `vol_flat` 成立时具有实际意义。

### `JValueCeilingFilter`

职责：判断 `J_T < j_ceiling`。

```python
j_safe = J_T < 55
```

### `RecentB1PickFilter`

职责：从 `_b1_pick` 中找出 `T-1` 或 `T-2` 的 B1 前置信号，并返回触发 B1 的滞后天数。

向量化实现中需要生成：

```text
_b2_prior_b1_lag
_b2_prior_b1_j
_b2_j_turn_up
```

优先级：

```text
如果 T-1 满足 B1，用 T-1。
否则如果 T-2 满足 B1，用 T-2。
否则无前置 B1。
```

### `B2Selector.prepare_df()`

流程：

1. 预计算 `zxdq`、`zxdkx`。
2. 预计算 `K`、`D`、`J`。
3. 预计算 `wma_bull`。
4. 用 B1 过滤器链生成 `_b1_pick`。
5. 生成 B2 中间列：
   - `_b2_daily_return`
   - `_b2_today_body_pct`
   - `_b2_volume_ratio`
   - `_b2_strict_yang_bao_yin`
   - `_b2_prior_b1_lag`
   - `_b2_prior_b1_j`
   - `_b2_j_turn_up`
   - `_b2_upper_shadow_ratio`
6. 生成 `_vec_pick`。

`_vec_pick` 逻辑：

```python
_vec_pick = (
    current_zx_ok
    & current_weekly_ok
    & recent_b1_ok
    & j_turn_up_ok
    & j_safe_ok
    & daily_return_ok
    & today_bull_body_ok
    & volume_confirm_ok
)
```

## 选股调度

在 `pipeline/select_stock.py` 中新增 `run_b2()`。

签名：

```python
def run_b2(
    prepared: Dict[str, pd.DataFrame],
    pick_date: pd.Timestamp,
    pool_codes: List[str],
    cfg_b2: dict,
    cfg_b1: dict,
) -> List[Candidate]:
```

输出候选：

```python
Candidate(
    code=code,
    date=date_str,
    strategy="b2",
    close=float(row["close"]),
    turnover_n=float(row["turnover_n"]),
    extra={
        "daily_return": float(row["_b2_daily_return"]),
        "today_body_pct": float(row["_b2_today_body_pct"]),
        "volume_ratio": float(row["_b2_volume_ratio"]),
        "prior_b1_lag": int(row["_b2_prior_b1_lag"]),
        "prior_b1_j": float(row["_b2_prior_b1_j"]),
        "j": float(row["J"]),
        "j_turn_up": bool(row["_b2_j_turn_up"]),
        "strict_yang_bao_yin": bool(row["_b2_strict_yang_bao_yin"]),
        "upper_shadow_ratio": float(row["_b2_upper_shadow_ratio"]),
        "b2_quality_score": float(row["_b2_quality_score"]),
    },
)
```

### 去重调整

`run_preselect()` 当前按 `code` 去重，需要改成按 `(code, strategy)` 去重：

```python
seen: set[tuple[str, str]] = set()
deduped = []
for candidate in all_candidates:
    key = (candidate.code, candidate.strategy)
    if key in seen:
        continue
    seen.add(key)
    deduped.append(candidate)
```

这项调整会影响所有策略，但符合“同股不同策略独立持仓逻辑”的系统设计。

### warmup

`_calc_warmup()` 增加 B2 分支。B2 至少需要 B1 的 warmup，并额外需要 `b1_lookback` 天窗口。实际可以按 B1 最大均线周期加 buffer 处理：

```python
if cfg_b2.get("enabled", False):
    warmup = max(warmup, int(cfg_b1.get("zx_m4", 114)) + buffer + int(cfg_b2.get("b1_lookback", 2)))
```

## CLI 集成

在 `pipeline/cli.py` 的 `_enabled_strategies()` 中增加：

```python
if cfg.get("b2", {}).get("enabled", False):
    strategies.append("b2")
```

这样 `CandidateRun.meta.executed_strategies` 和 `strategy_candidate_counts` 能正确记录 B2。

## Workbench UI

在 `workbench/app.py` 中做三类修改：

1. `strategy_preset()` 增加 `b2` 默认键。
2. 策略预设增加：
   - `B2 策略`
   - `B1 + B2`
   - `B1 + B2 + 砖型图`
3. 策略配置页新增 B2 参数面板：
   - 启用 B2
   - `b1_lookback`
   - `min_return`
   - `min_today_body_pct`
   - `j_ceiling`
   - `volume_ratio_min`
   - `flat_volume_ratio`
   - `min_yang_bao_yin_body_pct`
   - `upper_shadow_soft_limit`

结果中心和历史结果已经按 `strategy` 字段筛选，新增 `b2` 后应自动出现；CSS 需要补充 `b2` 标签样式。

## 模拟交易

`paper_trading/daily_flow.py` 的 `strategy_config()` 增加 `b2`：

```python
cfg.setdefault("b2", {})
cfg["b2"]["enabled"] = strategy == "b2"
```

如果每日自动流程需要运行 B2，则增加独立 B2 初选、导图、复评和归档步骤。不要把 B2 混入 B1 的结果中。

`paper_trading/core.py` 当前完整归档检查硬编码 `("b1", "brick")`。新增 B2 后不要简单保持不变，也不要无条件加入 B2。正确方向是改为“按交易流程配置要求的策略集合判断完整性”。如果 B2 被纳入每日自动交易流程，就必须要求当天归档包含 B2；如果只是手动研究策略，可以不要求。

## 复评与归档

候选 JSON 中 `strategy="b2"` 和 `extra` 字段会被复评器读取。需要确认 prompt 中能看到以下字段：

- `daily_return`
- `today_body_pct`
- `volume_ratio`
- `prior_b1_lag`
- `prior_b1_j`
- `j`
- `j_turn_up`
- `strict_yang_bao_yin`
- `upper_shadow_ratio`
- `b2_quality_score`

归档层已经按策略生成 `{strategy}.json`，新增 B2 后应生成 `b2.json`。

## 质量评分

B2 初选只负责硬条件，不应把评分作为是否入选的必要条件。评分用于候选排序、复评提示和后续统计。

建议第一版评分：

```text
基础分 100

J 位置：
- J < 25，加 5
- 25 <= J < 45，不调整
- 45 <= J < 55，减 5

J 拐头：
- J_T - J_B1_day >= 10，加 5
- 0 < J_T - J_B1_day < 10，不调整

量能：
- volume_ratio > 1.2，加 8
- 1.0 < volume_ratio <= 1.2，加 3
- 平量阳包阴，不调整

实体：
- today_body_pct >= 0.03，加 5
- 0.003 <= today_body_pct < 0.01，减 5

上影线：
- upper_shadow_ratio <= 0.03，加 5
- 0.03 < upper_shadow_ratio <= 0.15，不调整
- upper_shadow_ratio > 0.15，减 10
```

## 文件变更总览

| 文件 | 变更内容 |
| --- | --- |
| `pipeline/Selector.py` | 新增 B2 相关 Filter 和 `B2Selector` |
| `pipeline/select_stock.py` | 新增 `run_b2()`、接入 `run_preselect()`、按 `(code, strategy)` 去重 |
| `config/rules_preselect.yaml` | 新增 `b2:` 配置段 |
| `pipeline/cli.py` | `_enabled_strategies()` 增加 `b2` |
| `workbench/app.py` | 策略预设、B2 参数面板、B2 标签展示 |
| `workbench/assets/style.css` | 新增 `.tag.b2` 样式 |
| `dashboard/assets/style.css` | 新增 `.strategy-b2` 样式 |
| `paper_trading/daily_flow.py` | 支持独立运行 B2 |
| `paper_trading/core.py` | 完整归档检查改为配置驱动 |
| `agent/prompt.md` 或复评构造逻辑 | 确认 B2 extra 字段会进入复评上下文 |

## 验证计划

### 单元测试

构造小型 OHLCV 数据，覆盖：

1. `T-1` 满足 B1，`T` 满足 B2。
2. `T-2` 满足 B1，`T` 满足 B2。
3. `T-3` 满足 B1，`T` 不应满足 B2。
4. `J_T <= J_B1_day` 不应满足 B2。
5. `J_T >= 55` 不应满足 B2。
6. 收盘涨幅大于 4%，但 `close_T <= open_T`，不应满足 B2。
7. 收盘涨幅大于 4%，但实体小于 `min_today_body_pct`，不应满足 B2。
8. 放量且实体阳线，通过量能确认，不要求阳包阴。
9. 近似平量且严格阳包阴，通过量能确认。
10. 近似平量但非严格阳包阴，不应满足 B2。
11. 缩量低于 `flat_volume_ratio`，不应满足 B2。
12. 同一股票同时命中 B1 和 B2，候选中保留两条 `(code, strategy)`。

### CLI 验证

默认配置中 B2 关闭，跑一次确认不影响现有行为：

```bash
python -m pipeline.cli preselect --config config/rules_preselect.yaml
```

复制一份临时配置打开 B2 后验证：

```bash
cp config/rules_preselect.yaml /tmp/rules_preselect_b2.yaml
# 将 /tmp/rules_preselect_b2.yaml 中 b2.enabled 改为 true
python -m pipeline.cli preselect --config /tmp/rules_preselect_b2.yaml --date 2025-07-08 --merge-same-date
```

### 图例回放

用 `docs/经典图形/B2完美图形` 中的样例股票和日期做定点回放，检查是否命中：

- 平行重炮类
- 灾后重建类
- 跃跃欲试类

每个样例至少记录：

```text
是否命中 B2
未命中原因
prior_b1_lag
J_B1_day
J_T
daily_return
today_body_pct
volume_ratio
strict_yang_bao_yin
upper_shadow_ratio
```

### Workbench 验证

1. 策略配置页能启用 B2 并保存到运行快照。
2. 运行中心选择 B2 后能完成初选、导图、复评。
3. 结果中心能按 `b2` 筛选。
4. 历史归档生成 `b2.json`。
5. 同一股票命中多个策略时，结果中心能展示多条策略记录。

## 实施顺序

1. 修改配置和 CLI 策略识别。
2. 修改 `Selector.py`，实现 B2 过滤器和 `B2Selector`。
3. 修改 `select_stock.py`，接入 `run_b2()` 并调整去重为 `(code, strategy)`。
4. 增加单元测试，先验证量化规则边界。
5. 接入 Workbench 和样式。
6. 接入模拟交易流程和完整归档判断。
7. 用 B2 图例做定点回放，整理命中和未命中原因。

## 风险与待确认

- 当前系统 B1 是完整 B1 过滤器链，不只是单独的 J 拐头。B2 第一版按系统 B1 执行，后续如果 B1 本身定义调整，B2 会自动继承。
- `flat_volume_ratio = 0.98` 是第一版默认值，需要用图例和历史回放验证命中率。如果漏掉太多有效样例，可评估降到 `0.97`。
- `min_today_body_pct = 0.003` 和 `min_yang_bao_yin_body_pct = 0.003` 是防止伪实体的最低门槛，不应调得过高，避免把真实强势高开阳线误杀。
- 完整归档检查不能继续硬编码固定策略集合，应按每日流程配置判断，否则新增策略后模拟交易状态会失真。
