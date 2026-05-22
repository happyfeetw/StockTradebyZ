# AGY CLI 探针结果

状态：Phase 1/2 已通过，日期 2026-05-22，关联 GitHub issue：#11，关联 PR：#12。

## 运行命令

只读能力探测：

```bash
python3 scripts/agy_cli_probe.py --skip-json-probe
```

非交互 JSON 探针：

```bash
python3 scripts/agy_cli_probe.py --json-probe-timeout 60
```

底层 `agy` 命令必须使用以下参数顺序：

```bash
agy --print-timeout 2m --print 'Return exactly {"ok":true,"runner":"agy"} and nothing else.'
```

不要使用 `agy --print --print-timeout 2m ...`。当前 `agy --help` 没有写明这一点，但实测 `--print` 是带 prompt 值的 flag，`--print-timeout` 放在 `--print` 后面会被当作 prompt 内容。

## 当前结果

| 检查项 | 结果 |
| --- | --- |
| `agy --version` | `1.0.1` |
| `--print` / `--prompt` | 存在 |
| `--print-timeout` | 存在 |
| `--add-dir` | 存在 |
| `--sandbox` | 存在 |
| per-call `--model` | 未发现 |
| `--output-format json/stream-json` | 未发现 |
| settings 文件 | 可读，路径为 `~/.gemini/antigravity-cli/settings.json` |
| settings 中模型线索 | `model = Gemini 3.1 Pro (High)` |
| 非交互 JSON 探针 | 通过，返回严格 JSON |
| 图片读取探针 | 通过，能读取真实 K 线图并返回可解析 JSON |
| 本轮认证日志 | `authenticated via keyring`，`Print mode: silent auth succeeded` |
| 本轮认证阻断 | 未出现 OAuth prompt、auth timeout 或 keyring timeout |
| 历史认证日志 | 仍保留 1.0.0 的 `keyringAuth: timed out after 1s` 记录，仅作为历史诊断 |

认证专项调研见 [`docs/agy-cli-auth-research.md`](agy-cli-auth-research.md)。

## 结论

Phase 0 只读能力探测通过：当前 `agy` 具备继续探索所需的基础非交互入口，也能读取本地 Antigravity 设置文件。

Phase 1 非交互 JSON 探针通过：`agy --print-timeout ... --print ...` 可在 Codex 子进程中完成调用并返回严格 JSON。

Phase 2 图片读取探针通过：使用真实 K 线图 `301305_day.jpg`，AGY 返回 `can_read_chart=true`，并能描述图中日期区间、收盘价、均线和成交量等可见要素。

AGY 1.0.1 已解除上一轮认证硬阻断。探针脚本已区分历史日志和本轮日志，blocking 字段改为观察本轮 `current_run_keyring_auth_timeout_seen`。

即使认证修复，当前仍有两个迁移硬限制：

- 无 per-call `--model`，不能像 Gemini CLI 一样在脚本中明确指定模型。
- 无 `--output-format json/stream-json`，只能依赖 prompt 约束和 JSON 解析。

因此当前可以进入实验版单股 reviewer，但在模型可控和 JSON 稳定性通过前，不能接入生产复评入口。

## 下一步

1. 从普通终端运行修正后的最小命令，确认 prompt 传递正确：

   ```bash
   agy --print-timeout 2m --print 'Return exactly {"ok":true,"runner":"agy"} and nothing else.'
   ```

2. 从 Codex/worktree 重新运行 JSON 探针：

   ```bash
   python3 scripts/agy_cli_probe.py --json-probe-timeout 120
   ```

3. 准备一张真实 K 线图后运行图片探针：

   ```bash
   python3 scripts/agy_cli_probe.py \
     --image-path /absolute/path/to/000001_day.jpg \
     --image-code 000001 \
     --json-probe-timeout 60 \
     --image-probe-timeout 120
   ```

4. 单股实验 reviewer 已新增，可用一支股票 smoke test：

   ```bash
   python3 agent/agy_cli_review.py --limit 1
   ```

5. 每次 `agy update` 后重新运行探针，重点观察 `current_run_keyring_auth_timeout_seen` 是否保持 false：

   ```bash
   python3 scripts/agy_cli_probe.py --json-probe-timeout 120
   ```
