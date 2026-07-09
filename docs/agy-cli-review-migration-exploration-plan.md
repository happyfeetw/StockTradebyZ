# AGY CLI 复评迁移探索设计

状态：实验 reviewer 探索中，维护分支 `explore/agy-migration`，更新日期 2026-05-22。

## 当前决策

本分支用于探索把现有 `gemini-cli` K 线图复评迁移到 Antigravity CLI（`agy`）的可行性。探索目标不是直接替换生产链路，而是用最小可验证实现回答三个问题：

1. `agy` 是否能在非交互模式下稳定读取项目导出的 K 线图。
2. `agy` 是否能稳定返回符合现有复评契约的 JSON。
3. `agy` 是否能明确控制或确认实际使用的 Gemini 模型，尤其是 Gemini 3.5 Flash。

Phase 1/2 已在 AGY 1.0.1 下通过；Phase 3 已新增单股实验 reviewer。在模型控制和小批量稳定性没有通过前，`gemini-cli` 复评仍是生产可用路径，`agy` 只作为实验 reviewer。

## 当前事实与约束

本机环境在 2026-05-22 验证到：

- `agy --version` 为 `1.0.1`。
- `agy --help` 暴露 `--print` / `--prompt`、`--print-timeout`、`--sandbox`、`--dangerously-skip-permissions`、`--add-dir` 等参数。
- `agy --help` 未暴露等价于 Gemini CLI 的 per-call `--model` 参数。
- `agy --help` 未暴露等价于 Gemini CLI 的 `--output-format json` 或 `--output-format stream-json` 参数。
- AGY 1.0.1 已修复上一轮重复登录阻断；本轮 JSON 探针和图片读取探针均通过。
- 本轮认证日志显示 `authenticated via keyring` 和 `Print mode: silent auth succeeded`，没有出现本轮 keyring 超时。
- 当前 settings 模型线索为 `model = Gemini 3.1 Pro (High)`。

官方文档当前给出的约束：

- Antigravity CLI 的 `/model` 是交互式默认推理模型选择，并会在会话中保持，而不是当前脚本可直接传入的 per-call 参数。
- Antigravity CLI 的设置文件位于 `~/.gemini/antigravity-cli/settings.json`，部分设置可被启动参数覆盖，但官方文档没有给出模型的命令行覆盖参数。
- Gemini CLI 到 Antigravity CLI 的迁移不是 100% feature parity。
- 官方文档没有给出通过命令行参数 per-call 指定模型的方案。

这些约束意味着：本分支不能把 `agy` 设计成 `gemini` 命令的直接参数替换，必须先设计探针、阻断条件和实验输出隔离。

参考资料：

- Antigravity CLI 使用文档：https://antigravity.google/docs/cli-using
- Antigravity CLI 功能文档：https://antigravity.google/docs/cli-features
- Gemini CLI 迁移文档：https://antigravity.google/docs/gcli-migration
- Antigravity CLI 产品页：https://antigravity.google/product/antigravity-cli
- Gemini 3.5 Flash in Antigravity：https://antigravity.google/blog/gemini-3-5-flash-in-google-antigravity
- AGY CLI 认证专项调研：[docs/agy-cli-auth-research.md](agy-cli-auth-research.md)
- AGY CLI changelog 1.0.1：https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md#101

## 当前实现状态

- 已新增 `scripts/agy_cli_probe.py`，用于输出 AGY CLI 能力和非交互 JSON 探针报告。
- 已新增 `agent/agy_cli_review.py`，用于单股 AGY 实验复评。
- 已新增 `config/agy_cli_review.yaml`，默认 `max_items=1`，避免误跑完整批次。
- 探针报告默认写入 `data/review/agy_cli_probe/`，该目录位于已忽略的 `data/` 下，不进入版本库。
- 探针会从临时目录运行 `agy --print`，避免 AGY 在仓库 worktree 内留下运行痕迹。
- 探针会脱敏 Google OAuth URL，并显式识别 `authentication required`、`authorization code`、`authentication timed out` 等认证阻断文本。
- 实验 reviewer 结果写入 `data/review/agy_cli_experimental/{pick_date}`，不覆盖正式复评结果。
- 实验 reviewer 会写入 `reviewer: agy-cli-experimental` 和 `model_evidence`。

## 迁移设计原则

- 不污染生产结果：AGY 实验结果必须写入独立目录或明确标记 `reviewer: agy-cli-experimental`，不能覆盖现有 `gemini-cli` 结果。
- 不假设模型 per-call 可控：当前只能读取 settings 中的持久模型线索，实验结果必须写入 `model_evidence`。
- 不修改用户全局设置：脚本默认只读 `~/.gemini/antigravity-cli/settings.json`，不自动改写 `/model`、权限、sandbox 或登录状态。
- 不依赖自由文本：即使 `agy` 没有 JSON 输出参数，也必须通过现有 JSON schema 严格解析模型正文；解析失败视为实验失败。
- 不扩大权限面：默认使用 `--sandbox` 或最小权限运行；只有探针明确需要并经过人工确认时，才允许 `--dangerously-skip-permissions`。
- 保留现有路径：`run_all.py --reviewer gemini-cli` 和 `agent/gemini_cli_review.py` 不因本分支探索而退化。

## 探索阶段

### Phase 0：只读能力探测

目标：确认当前 `agy` 是否具备继续探索的最低命令行能力。

需要实现一个只读探针脚本或命令清单，检查：

- `agy --version`
- `agy --help`
- 是否存在 `--print` / `--prompt`
- 是否存在 `--print-timeout`
- 是否存在 per-call `--model`
- 是否存在结构化输出参数
- 是否存在 `~/.gemini/antigravity-cli/settings.json`
- 如果设置文件可读，只记录当前模型字段是否存在，不输出账号、token、凭证或敏感配置

阻断条件：

- 如果没有 `--print` / `--prompt`，停止迁移探索。
- 如果无法确认模型来源，后续只能做“运行机制实验”，不能做“模型能力对比结论”。
- 如果本轮 `current_run_keyring_auth_timeout_seen=true`，停止批量 reviewer 实现并回退到认证调研。

### Phase 1：最小非交互 JSON 探针（已通过）

目标：验证 `agy` 是否能在非交互模式下返回可解析 JSON。

建议命令形态。注意 `--print` 是带 prompt 值的 flag，`--print-timeout` 必须放在 `--print` 之前，否则 `--print-timeout` 可能被模型当成 prompt 内容：

```bash
agy --print-timeout 5m --print "Return exactly {\"ok\":true,\"runner\":\"agy\"} and nothing else."
```

验收：

- 进程退出码为 0。
- stdout 能提取出严格 JSON。
- stderr 不包含认证失败、权限等待、交互阻塞或超时。
- 当前工作目录没有留下需要提交的临时文件；如果 `agy` 创建 `.antigravitycli` 等本地运行痕迹，脚本必须明确清理或加入本地忽略策略，但不能提交。
- 同一 shell 中连续多次运行不要求重新登录。

### Phase 2：图片读取探针（已通过）

目标：验证 `agy` 能否读取 K 线图。

探针使用一张真实导出的日线图，构造最小 prompt：

- 输入股票代码。
- 输入 `@{image_file}` 或官方支持的文件引用方式。
- 要求模型只返回 JSON，字段包含 `ok`、`code`、`can_read_chart`、`observations`。

验收：

- `can_read_chart` 为 true。
- `observations` 能描述图中可见 K 线结构，而不是泛泛回答。
- JSON 解析成功。
- 单图耗时和 stdout/stderr 被记录到实验日志。

阻断条件：

- 如果 `agy --print` 不支持 `@file` 或无法读取图片，本轮不继续实现批量 reviewer。

### Phase 3：单股实验 reviewer（已实现 smoke test）

目标：实现最小 `AgyCliReviewer` 单股复评路径，但不进入生产默认入口。

设计：

- 新增 `agent/agy_cli_review.py`，复用 `BaseReviewer` 的候选读取、图表查找、结果汇总能力。
- 新增 `config/agy_cli_review.yaml`，配置 `agy_bin`、`print_timeout`、`request_delay`、`skip_existing`、`raw_log_dir`、`suggest_min_score`、`max_items`、`expected_model_label`。
- 构造 prompt 时复用 `agent/prompt.md` 和现有 JSON 输出契约。
- 调用 `agy --print-timeout <duration> --print <prompt>`。
- 输出结果写入实验目录 `data/review/agy_cli_experimental/{pick_date}/{code}.json`，避免和正式结果混淆。
- 保存 prompt、stdout、stderr、returncode、duration、resolved model evidence。

验收：

- 单股复评能输出现有 schema 兼容 JSON。
- 输出包含 `code`、`reviewer`、`model_evidence`。
- 失败不会覆盖现有 `data/review/{pick_date}/{code}.json`。

已验证：

```bash
python3 agent/agy_cli_review.py --limit 1 \
  --candidates /Users/wangxinduo/Development/Code/Personal/StockTradebyZ/data/candidates/candidates_latest.json \
  --kline-dir /Users/wangxinduo/Development/Code/Personal/StockTradebyZ/data/kline \
  --output-dir data/review/agy_cli_experimental
```

结果：单股 `300475` 成功输出 `reviewer=agy-cli-experimental`、`total_score=2.7`、`verdict=FAIL`，并生成实验汇总。

### Phase 4：小批量与回退策略

目标：验证 `agy` 是否适合批量 K 线复评。

设计：

- 先只支持单股循环，不直接实现多图批量。
- 等单股成功率稳定后，再尝试 2 到 5 张图的小批量。
- 复用 Gemini CLI 的退避思想：先 retry 原请求，再拆分，最后逐只处理。
- 因 `agy` 当前无 `stream-json` 参数，不以流式事件作为进度判断，只以总超时和进程退出为准。

验收：

- 5 支样本全部输出 JSON。
- 中断后可通过 `skip_existing` 续跑。
- 失败股票有单独失败记录，成功结果不被覆盖。

### Phase 5：接入运行入口（可选）

只有 Phase 1 到 Phase 4 通过后，才考虑接入项目入口。

可选接入：

- `run_all.py --reviewer agy-cli-experimental`
- Workbench 复评方式新增 `Antigravity CLI（实验）`
- UI 中明确展示实验状态、当前 `agy --version`、模型确认状态和输出隔离目录

接入前阻断条件：

- 无法确认模型时，不能在 UI 中宣称使用 Gemini 3.5 Flash。
- JSON 成功率低于 100% 时，不能作为默认 reviewer。
- 图片读取探针不稳定时，不能进入批量复评入口。

## 验证矩阵

| 场景 | 命令或入口 | 通过标准 |
| --- | --- | --- |
| CLI 参数探测 | `agy --help` | 能找到 `--print` 和 `--print-timeout` |
| JSON 探针 | `agy --print ...` | 已通过，stdout 可解析为 JSON |
| 图片探针 | 单图 prompt + K 线图 | 已通过，返回图像相关 JSON |
| 单股复评 | `agent/agy_cli_review.py --limit 1` | 输出 schema 兼容结果 |
| 小批量复评 | 5 支候选 | 全部成功或失败可追踪 |
| 断点续跑 | 中断后重跑 | 已成功结果不重复 |
| 现有 CLI 回归 | `python run_all.py --reviewer gemini-cli` | 原路径不受影响 |

## 交付物

本探索分支的目标交付物按顺序推进：

1. 本设计文档。
2. AGY 能力探针脚本或文档化命令清单。
3. 单股图片读取 smoke test 记录。
4. 实验版 `agent/agy_cli_review.py` 与配置文件。
5. 小批量实验报告。
6. 是否继续迁移的决策结论。

## 当前不做

- 不把 `agy` 设置为默认 reviewer。
- 不删除或重写 `agent/gemini_cli_review.py`。
- 不把 API key 路径作为本探索分支的主线。
- 不自动修改 Antigravity 全局模型设置。
- 不把无法确认模型的结果用于“3.5 Flash 强弱”结论。
