# AGY CLI 探针结果

状态：探针迭代，日期 2026-05-21，关联 GitHub issue：#11。

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
| `agy --version` | `1.0.0` |
| `--print` / `--prompt` | 存在 |
| `--print-timeout` | 存在 |
| `--add-dir` | 存在 |
| `--sandbox` | 存在 |
| per-call `--model` | 未发现 |
| `--output-format json/stream-json` | 未发现 |
| settings 文件 | 可读，路径为 `~/.gemini/antigravity-cli/settings.json` |
| settings 中模型线索 | `model = Gemini 3.1 Pro (High)` |
| 非交互 JSON 探针 | Codex 环境未通过；普通终端需用修正后的参数顺序重跑 |
| 阻断原因 | Codex 运行环境中的 `agy --print-timeout ... --print ...` 仍要求重新认证，等待 30 秒后认证超时 |
| 认证日志线索 | 反复出现 `keyringAuth: timed out after 1s`，随后 `silent auth failed` 并触发 OAuth |
| Keychain 元数据 | 存在 `gemini` / `antigravity` 与 `Antigravity Safe Storage` / `Antigravity Key` 条目，未读取 secret |

认证专项调研见 [`docs/agy-cli-auth-research.md`](agy-cli-auth-research.md)。

## 结论

Phase 0 只读能力探测通过：当前 `agy` 具备继续探索所需的基础非交互入口，也能读取本地 Antigravity 设置文件。

Phase 1 非交互 JSON 探针在 Codex 环境未通过：当前 Codex 运行环境中的登录状态不足以让 `agy --print-timeout ... --print ...` 完成模型调用。

用户普通终端已能完成认证并进入模型调用，但旧命令参数顺序导致模型收到错误 prompt。已根据官方文档中的 flag 覆盖说明、迁移说明和本机 `agy --help` 重新校准：`--print` 应视为带 prompt 值的 flag，后续探针统一使用 `--print-timeout <duration> --print <prompt>`。

进一步调研后，认证阻断的根因更接近 AGY 1.0.0 新进程读取系统 keyring 超时，而不是“没有登录过”或“没有 settings 文件”。本机日志显示 AGY 在 print mode 中先尝试 silent auth，再因 `keyringAuth` 1 秒超时跳过 keyring auth，最后回退到 OAuth。公开 issue `google-antigravity/antigravity-cli#24` 也有 macOS 用户报告同类现象。

即使认证修复，当前仍有两个迁移硬限制：

- 无 per-call `--model`，不能像 Gemini CLI 一样在脚本中明确指定模型。
- 无 `--output-format json/stream-json`，只能依赖 prompt 约束和 JSON 解析。

因此在模型可确认和 JSON 稳定性通过前，不能接入生产复评入口。

## 下一步

1. 从普通终端运行修正后的最小命令，确认 prompt 传递正确：

   ```bash
   agy --print-timeout 2m --print 'Return exactly {"ok":true,"runner":"agy"} and nothing else.'
   ```

2. 如果普通终端输出严格 JSON，再从 Codex/worktree 重新运行：

   ```bash
   python3 scripts/agy_cli_probe.py --json-probe-timeout 90
   ```

3. 准备一张真实 K 线图后运行图片探针：

   ```bash
   python3 scripts/agy_cli_probe.py \
     --image-path /absolute/path/to/000001_day.jpg \
     --image-code 000001 \
     --json-probe-timeout 60 \
     --image-probe-timeout 120
   ```

4. 只有 JSON 探针和图片探针都通过后，才进入 `agent/agy_cli_review.py` 实验 reviewer 实现。

5. 每次 `agy update` 后重新运行探针，重点观察 `keyring_auth_timeout_seen` 是否消失：

   ```bash
   python3 scripts/agy_cli_probe.py --json-probe-timeout 90
   ```
