# 复评系统改动整理（2026-06-11）

本文整理当前分支 `refactor/strategy-review-scoring` 相对基线
`0814bf8 Checkpoint multi-model review baseline` 的主要代码改动。

整理基线 HEAD：`e373aee Route Codex CLI reviews through env provider`

## 总览

本轮改动把复评主线从单一 Gemini 复评，整理为：

```text
量化初选
-> 候选批次冻结
-> Gemini / AGY / Codex 三路命令行 reviewer
-> 策略化评分归一
-> 多模型共识
-> Z 质量裁决
-> Workbench 查看、筛选、导入通达信
```

核心原则是质量优先，不做静默模型降级：

- 正式多模型配置默认只跑配置中声明的模型。
- CLI 模型不可用、失败或认证异常时，记录失败原因。
- 首轮结束后，只按原模型重跑失败项一次。
- 已经成功写出的单股结果通过 `skip_existing` 断点复用。
- `complete=false` 或 `INCOMPLETE` 的共识结果只能排障，不能作为最终交易决策。

## 提交主题

| 提交 | 主题 | 主要影响 |
|---|---|---|
| `0c57036` | 策略化 AI 评分重构 | 新增公共 gate、策略 profile、统一归一化入口 |
| `b497b5c` | 策略化评分细化 | 调整 `b1/b2/brick` 的 PASS/WATCH 门槛和低分上限 |
| `2187a86` | Z 质量裁决层 | 共识后增加本地规则 dry-run 和 LLM 输入包 |
| `a7fea73` | AGY 认证失败处理 | 识别 AGY auth 异常，避免错误进入逐只 fallback |
| `e373aee` | Codex env provider | Codex CLI 复评通过环境变量注入 OpenAI-compatible provider |

## 主要代码面

### 评分与 prompt

- [agent/review_scoring.py](../agent/review_scoring.py) 新增默认评分体系：
  - 公共交易 gate。
  - `b1`、`b2`、`brick` 三套策略 profile。
  - hard veto、WATCH cap、PASS/WATCH 阈值。
- [agent/base_reviewer.py](../agent/base_reviewer.py) 承担复评 JSON 的统一归一化。
- [agent/prompt.md](../agent/prompt.md) 与评分字段对齐，要求模型输出公共条件、策略分和解释。
- [docs/classic-pattern-review-scoring.md](classic-pattern-review-scoring.md) 记录评分 V3 的口径。

### 多模型复评与共识

- [agent/multi_model_review.py](../agent/multi_model_review.py) 负责：
  - 冻结候选批次。
  - 按执行组调度 reviewer：不同 backend 可并行，同一 backend 串行，避免 AGY 并发争用本机 OAuth/keyring/日志状态。
  - 强制 `no_model_substitution`。
  - 首轮失败后按原模型 rerun 一次。
  - 写入进度快照、日志路径和失败摘要。
  - 共识完成后自动触发 Z 质量层。
- [pipeline/review_consensus.py](../pipeline/review_consensus.py) 生成股票粒度 `decisions` 与模型粒度 `details`。
- [config/multi_model_review.yaml](../config/multi_model_review.yaml) 当前默认模型：
  - `gemini-3.5-flash-high`：AGY CLI 执行，后端模型名 `Gemini 3.5 Flash (High)`
  - `gemini-3.1-pro-high`：AGY CLI 执行，后端模型名 `Gemini 3.1 Pro (High)`
  - `gpt-5.5-high`：Codex CLI 执行，`gpt-5.5` + `reasoning_effort=high`
- [docs/multi-model-review-consensus.md](multi-model-review-consensus.md) 是多模型流程的主说明文档。

### AGY CLI

- [agent/agy_cli_review.py](../agent/agy_cli_review.py) 增加认证异常识别与运行时恢复等待。
- AGY auth 异常不会再被误判成普通批量 JSON 失败，也不会继续逐只 fallback。
- 如果活跃 AGY 子进程正在等待 authorization code，可按 `auth_recovery_status.json` 里的 `auth_code_file` 写入 code，reviewer 会转发到子进程 stdin 并删除该文件。
- 如果 AGY 要求重新 OAuth，reviewer 会暂停当前批次，定期用同一模型执行极小 `agy --print` 探针；探针恢复后重试当前批次。
- 恢复状态写入 `auth_recovery_status.json`；超时后仍按失败退出，由多模型编排按原模型断点重跑。
- AGY 默认 `batch_size=3`、`print_timeout=6m`、`timeout_seconds=360`；批量超时时先按同一模型拆小批，单股仍超时时再按模型级失败处理，由多模型编排按原模型断点重跑。
- AGY 非交互 `--print` 默认 `stdin_mode=devnull`，避免等待 stdin EOF；`stdin_mode=pipe` 仅用于显式授权码转发。
- `dangerously_skip_permissions` 作为显式配置保留但默认关闭，不自动绕过 AGY 权限确认。
- AGY CLI 当前是 Google 订阅登录模型的默认执行后端；不可用、超时或缺失时应记录失败原因并补跑，不能从正式共识中排除。
- 相关调研见：
  - [docs/agy-cli-auth-research.md](agy-cli-auth-research.md)
  - [docs/agy-cli-review-migration-exploration-plan.md](agy-cli-review-migration-exploration-plan.md)

### Codex CLI

- [agent/codex_cli_review.py](../agent/codex_cli_review.py) 默认固定：
  - `model: gpt-5.5`
  - `reasoning_effort: high`
  - `speed_tier: standard`
  - `ignore_user_config: false`
- [config/codex_cli_review.yaml](../config/codex_cli_review.yaml) 默认使用本机 Codex CLI OAuth 登录态，不注入 API key provider：
  - `auth_mode: local_oauth`
  - `env_provider_enabled: false`
  - 默认读取 `~/.codex/config.toml` / `~/.codex/auth.json`
  - 默认从子进程环境剥离 `CODEX_OPENAI_*` / `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_API_BASE`
- Workbench 复评配置页按模型提供选择：
  - 选择 `gpt-5.5-high`：显示完整“Codex 调用模式”
  - 三模型共识：显示“Codex 子模型调用模式”，用于写入本次 run snapshot
- 认证错误会归类为 `CodexCliAuthError`，避免表现成长期卡在 0% 或 1%。

旧的 OpenAI-compatible 本地代理路径仍保留为显式兼容模式：

```yaml
auth_mode: env_provider
env_provider_enabled: true
```

```bash
export CODEX_OPENAI_BASE_URL=http://127.0.0.1:8317/v1
export CODEX_OPENAI_API_KEY=<ccswitch-api-key>
```

除非明确启用该模式，否则 Workbench 或后台命令不应依赖这些环境变量。

### Z 质量层

- [agent/z_quality_review.py](../agent/z_quality_review.py) 新增共识后的质量裁决流程。
- [pipeline/z_features.py](../pipeline/z_features.py) 从本项目 K 线数据提取本地特征。
- [config/z_quality_rules.yaml](../config/z_quality_rules.yaml) 定义 v1 本地规则。
- [agent/z_quality_prompt.md](../agent/z_quality_prompt.md) 保存后续接入 LLM 裁决时使用的输入说明。
- 输出目录为 `data/z_quality/{batch_id}`。
- 当前版本是 `local_rules_dry_run`，只作为精选池/观察池辅助，不是自动买入信号。

主说明见 [docs/z-quality-layer.md](z-quality-layer.md)。

### Workbench 与通达信

- [workbench/app.py](../workbench/app.py) 增加：
  - 多模型复评配置入口。
  - AGY 模型列表手动加载/刷新。
  - 共识结果页面。
  - Z 质量裁决页签和决策表 Z 裁决/Z分/硬否决/观察限制列。
  - 模型评分明细视图。
  - 共识结果导入通达信弹窗，支持 Z 精选、Z 观察、Z 复盘等筛选。
- [pipeline/tdx_export.py](../pipeline/tdx_export.py) 支持从共识筛选结果生成通达信板块。
- 共识导入支持按策略分板块，并支持共同推荐、多模型推荐、单模型推荐、观察、分歧样本、Z 裁决、Z 分、硬否决和观察限制等筛选。

## 运行与结果边界

判断当前有效批次时，以：

- `data/review_consensus/latest.json`
- 对应 `summary.json` 中的 `models`
- `complete`
- `generated_at`

为准，不以目录里残留的历史模型文件为准。

多模型探索阶段可能存在旧模型、旧日志或部分失败目录。只要 summary 中
`complete=false`，就应通过断点重跑补齐 Gemini、AGY、Codex 三路正式模型。

## 建议验证

代码变更后至少跑：

```bash
python3 -m pytest \
  tests/test_review_contracts.py \
  tests/test_review_batch_consensus.py \
  tests/test_z_quality_review.py \
  tests/test_agy_cli_review.py \
  tests/test_codex_cli_review.py \
  tests/test_tdx_export.py \
  -q

python3 -m py_compile \
  agent/multi_model_review.py \
  agent/gemini_cli_review.py \
  agent/agy_cli_review.py \
  agent/codex_cli_review.py \
  agent/z_quality_review.py \
  pipeline/z_features.py \
  pipeline/tdx_export.py \
  workbench/app.py
```

本机 Python 通过 `python3` 调用。如果当前环境没有安装 `pytest`，可用
`python3 -m unittest` 跑同一批 `tests.test_*` 模块做合同验证。
如果只改文档，可以不重跑全流程，但仍建议跑上述单测确认合同未漂移。

## 当前残余风险

- AGY CLI 仍依赖本机登录态和上游非交互输出稳定性；认证恢复等待只能处理可人工恢复的 OAuth/Keychain 抖动，不能保证 AGY 服务端容量或模型可用，因此失败时必须显式暴露为三路共识不完整，而不是降成两路共识。
- Codex CLI env provider 依赖启动进程继承环境变量；Workbench 从别的 shell 启动时要重新确认。
- Z 质量层 v1 是本地规则 dry-run，后续如接入 LLM 裁决，需要继续固化输出 contract。
- 共识层的回测评估还在后续计划中；当前共识分不能替代真实持有期收益验证。
