# AGY CLI 探针结果

状态：历史探针记录，日期 2026-06-05；AGY 已升级到 1.0.5 并支持 per-call `--model`。

> 2026-06-12 当前口径：AGY 已成为默认 Google 订阅登录复评路径，默认多模型中的两个 Gemini 模型都通过 AGY 执行。本文保留早期探针证据。

## 运行命令

只读能力探测：

```bash
python3 scripts/agy_cli_probe.py --skip-json-probe
```

非交互 JSON 探针：

```bash
python3 scripts/agy_cli_probe.py --model "Gemini 3.5 Flash (Low)" --json-probe-timeout 60
```

底层 `agy` 命令必须使用以下参数顺序：

```bash
agy --model "Gemini 3.5 Flash (Low)" --print-timeout 2m --print 'Return exactly {"ok":true,"runner":"agy"} and nothing else.'
```

不要使用 `agy --print --print-timeout 2m ...`。当前 `agy --help` 没有写明这一点，但实测 `--print` 是带 prompt 值的 flag，`--print-timeout` 放在 `--print` 后面会被当作 prompt 内容。

## 当前结果

| 检查项 | 结果 |
| --- | --- |
| `agy --version` | `1.0.5` |
| `--print` / `--prompt` | 存在 |
| `--print-timeout` | 存在 |
| `--add-dir` | 存在 |
| `--sandbox` | 存在 |
| per-call `--model` | 存在，最小 JSON 探针已通过 |
| `--output-format json/stream-json` | 未发现 |
| `--format` / `--json` / `--output` / `--raw-output` | 均未发现，1.0.5 下会报 `flags provided but not defined` |
| `agy models` | 可列出 Gemini 3.5 Flash Low/Medium/High 等模型 |
| settings 文件 | 可读，路径为 `~/.gemini/antigravity-cli/settings.json` |
| 非交互 JSON 探针 | `--model "Gemini 3.5 Flash (Low)"` 通过，返回严格 JSON |
| 图片读取探针 | 通过，能读取真实 K 线图并返回可解析 JSON |
| 本轮认证日志 | `authenticated via keyring`，`Print mode: silent auth succeeded` |
| 本轮认证阻断 | 未出现 OAuth prompt、auth timeout 或 keyring timeout |
| 历史认证日志 | 仍保留 1.0.0 的 `keyringAuth: timed out after 1s` 记录，仅作为历史诊断 |

认证专项调研见 [`docs/agy-cli-auth-research.md`](agy-cli-auth-research.md)。

## 结论

Phase 0 只读能力探测通过：当前 `agy` 具备继续探索所需的基础非交互入口，也能读取本地 Antigravity 设置文件。

Phase 1 非交互 JSON 探针通过：`agy --print-timeout ... --print ...` 可在 Codex 子进程中完成调用并返回严格 JSON。

Phase 2 图片读取探针通过：使用真实 K 线图 `301305_day.jpg`，AGY 返回 `can_read_chart=true`，并能描述图中日期区间、收盘价、均线和成交量等可见要素。

AGY 1.0.1 已解除上一轮认证硬阻断；1.0.5 需要继续用探针观察本轮 `current_run_keyring_auth_timeout_seen`。

当前剩余迁移限制：

- 无 `--output-format json/stream-json`，只能依赖 prompt 约束、JSON 抽取、本地 schema 校验和一次 JSON repair。
- repair 只是模型正文修复，不是 AGY CLI 原生结构化输出；结果会标记 `json_output_mode=prompt-json`。
- 小批量 JSON 成功率仍需用真实候选继续验证。

因此当前 AGY 已可作为正式 Google 模型执行后端，并能通过 `--model` 控制模型；结果仍需保留 prompt-json 元数据，便于排查非原生结构化输出带来的解析风险。

## 下一步

1. 从普通终端运行修正后的最小命令，确认 prompt 传递正确：

   ```bash
   agy --model "Gemini 3.5 Flash (Low)" --print-timeout 2m --print 'Return exactly {"ok":true,"runner":"agy"} and nothing else.'
   ```

2. 从 Codex/worktree 重新运行 JSON 探针：

   ```bash
   python3 scripts/agy_cli_probe.py --model "Gemini 3.5 Flash (Low)" --json-probe-timeout 120
   ```

3. 准备一张真实 K 线图后运行图片探针：

   ```bash
   python3 scripts/agy_cli_probe.py \
     --image-path /absolute/path/to/000001_day.jpg \
     --image-code 000001 \
     --model "Gemini 3.5 Flash (Low)" \
     --json-probe-timeout 60 \
     --image-probe-timeout 120
   ```

4. AGY reviewer 已新增，可用一支股票 smoke test：

   ```bash
   python3 agent/agy_cli_review.py --limit 1 --model "Gemini 3.5 Flash (Medium)"
   ```

   输出结果应包含 `json_schema_valid=true`。若首次输出不合格但 repair 成功，会看到 `json_repair_attempted=true` 和 `json_repair_used=true`。

   2026-06-05 hardening 后回归命令：

   ```bash
   python3 agent/agy_cli_review.py --limit 1 \
     --model "Gemini 3.5 Flash (Medium)" \
     --output-dir data/review/agy_cli_contract_smoke
   ```

   结果：`300802_brick` 成功，`json_output_mode=prompt-json`，`json_schema_valid=true`，`json_repair_attempted=false`，`json_repair_used=false`。

5. 每次 `agy update` 后重新运行探针，重点观察 `current_run_keyring_auth_timeout_seen` 是否保持 false：

   ```bash
   python3 scripts/agy_cli_probe.py --model "Gemini 3.5 Flash (Low)" --json-probe-timeout 120
   ```
