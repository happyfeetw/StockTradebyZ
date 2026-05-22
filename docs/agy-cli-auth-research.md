# AGY CLI 认证专项调研

状态：认证阻断已解除，日期 2026-05-22，关联 GitHub issue：#11，关联 PR：#12。

## 调研问题

用户普通终端和 Codex 子进程运行 `agy --print` 时反复要求浏览器登录并粘贴授权码。需要判断：

1. 这是不是 AGY 的预期行为。
2. 是否存在官方支持的免交互方案。
3. 该问题对 K 线图批量复评迁移是否构成阻断。

## 2026-05-22 更新结论

AGY CLI 1.0.1 已修复上一轮认证阻断。依据：

- 本机 `agy --version` 为 `1.0.1`。
- 上游 `CHANGELOG.md#1.0.1` 明确写明修复 OAuth token persistence 和 authentication hangs。
- `google-antigravity/antigravity-cli#85` 已在 2026-05-21 关闭，维护者说明通过 `agy update` 获取 1.0.1。
- 本机 `scripts/agy_cli_probe.py --json-probe-timeout 120` 在 Codex 子进程中通过，`authentication_required=false`。
- 真实 K 线图图片探针通过，且本轮日志显示 `authenticated via keyring` 和 `Print mode: silent auth succeeded`，没有出现本轮 `keyringAuth` 超时。

因此，重复登录不再作为当前 AGY 迁移的硬阻断条件。它改为回归观察项：每次 AGY 升级后仍需跑探针，确认本轮 `current_run_keyring_auth_timeout_seen=false`。

当前剩余硬限制是：

1. AGY CLI 1.0.1 仍未暴露 per-call `--model` 参数。
2. AGY CLI 1.0.1 仍未暴露 `--output-format json/stream-json` 参数。
3. 当前只能通过持久 settings 中的 `/model` 选择确认模型，不能像 Gemini CLI 一样在每次命令中强制指定 Gemini 3.5 Flash。

## 官方文档结论

官方当前给出的认证机制是：

- AGY CLI 会优先尝试通过操作系统安全钥匙串静默认证。
- 如果找不到已保存会话，会回退到浏览器 Google Sign-In。
- 本机环境会自动打开浏览器；远程或 SSH 环境会打印授权 URL，并要求把浏览器返回的授权码粘回 CLI。
- `/logout` 会清除已保存凭证。

官方文档仍没有给出以下能力：

- 通过 API key 让 AGY CLI 完成登录。
- 通过环境变量传入 OAuth token 或 refresh token。
- 通过参数指定认证缓存文件。
- 通过参数显式选择 per-call 模型。

相关官方资料：

- Getting Started / Authentication: https://antigravity.google/docs/cli-getting-started
- Using AGY CLI / Settings: https://antigravity.google/docs/cli-using
- CLI Features / `/model` 与 `/logout`: https://antigravity.google/docs/cli-features
- Gemini CLI migration: https://antigravity.google/docs/gcli-migration
- CLI announcement: https://antigravity.google/blog/introducing-google-antigravity-cli
- AGY CLI changelog: https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md#101
- macOS auth issue fixed in 1.0.1: https://github.com/google-antigravity/antigravity-cli/issues/85

## 本机证据

上一轮 1.0.0 验证环境：

| 项 | 结果 |
| --- | --- |
| `agy --version` | `1.0.0` |
| `agy changelog` | `1.0.0: Initial release of the Antigravity CLI.` |
| `agy --help` | 暴露 `--print`、`--prompt`、`--print-timeout`、`--add-dir`、`--sandbox`、`--dangerously-skip-permissions` |
| per-call `--model` | 未发现 |
| `--output-format json/stream-json` | 未发现 |
| settings 文件 | `~/.gemini/antigravity-cli/settings.json` 可读 |
| settings 中模型线索 | `model = Gemini 3.1 Pro (High)` |
| Codex 子进程环境线索 | `TERM=dumb`，`__CFBundleIdentifier=com.openai.codex` |

本机 macOS Keychain 元数据检查显示存在 AGY/Gemini 相关条目：

- service `gemini`，account `antigravity`
- service `Antigravity Safe Storage`，account `Antigravity Key`

检查只读取 Keychain 元数据，没有读取或输出 secret/token。

AGY 日志中反复出现的认证链路：

```text
Print mode: not authenticated, trying silent auth
keyringAuth: timed out after 1s, skipping keyring auth
Print mode: silent auth failed, triggering OAuth
Print mode: auth timed out
```

这说明当前失败不是简单的“没有设置文件”或“没有登录过”。更准确的判断是：AGY 1.0.0 的新进程未能在 1 秒内从系统 keyring 完成静默认证，于是回退到 OAuth；在 Codex 子进程中无人完成浏览器交互时，最终认证超时。

1.0.1 复测结果：

| 项 | 结果 |
| --- | --- |
| `agy --version` | `1.0.1` |
| 非交互 JSON 探针 | 通过，返回 `{"ok":true,"runner":"agy"}` |
| 图片读取探针 | 通过，能读取真实 K 线图并返回可解析 JSON |
| 本轮 keyring 结果 | `authenticated via keyring` |
| 本轮认证阻断 | 未出现 OAuth prompt 或 auth timeout |
| 当前模型线索 | settings 中 `model = Gemini 3.1 Pro (High)` |

## 公开可靠渠道交叉验证

`google-antigravity/antigravity-cli` 公开 issue #24 “Not remembering credentials” 在 2026-05-20 打开，描述了相同类别问题：每次启动都要求认证。issue 内 macOS 用户的复现结论和本机一致：token/keychain 条目存在，但 `agy` 新进程仍不能稳定读取，日志出现 `keyringAuth: timed out after 1s` 后回退 OAuth。

参考：

- https://github.com/google-antigravity/antigravity-cli/issues/24

该 issue 的评论里有人提出过修改二进制常量延长 keyring 超时的临时做法。这个做法仍不进入本项目方案：1.0.1 已提供官方修复路径，二进制 patch 会破坏签名/升级可维护性，也不适合用于股票复评生产链路。

## 对 K 线图复评迁移的影响

当前 AGY 迁移风险已从三个降为两个主要风险：

1. 模型不可 per-call 指定：当前官方文档和 `agy --help` 都没有显示 `--model` 参数。即使能运行，也不能像 Gemini CLI 一样在每次调用中明确指定 Gemini 3.5 Flash。
2. 输出不可结构化指定：当前未发现 `--output-format json/stream-json`，只能依赖 prompt 和后处理解析，失败率需要单独验证。

因此，AGY CLI 目前仍不能替换生产 `gemini-cli` 复评链路，但已经具备继续实现实验 reviewer 的条件。实验结果必须带上 `reviewer=agy-cli-experimental` 和 `model_evidence`，并保持输出目录隔离。

## 当前可行策略

### 短期生产路径：继续用 API key 跑 Gemini 3.5 Flash 复评

这是当前最稳的短期路径。它与 AGY 迁移探索解耦，适合先完成用户当前目标：用最新 Gemini 3.5 Flash 对候选股票 K 线图进行复评。

### AGY 探索路径：进入实验 reviewer

AGY 分支已满足进入单股实验 reviewer 的前置条件：

- `scripts/agy_cli_probe.py --json-probe-timeout 120` 的 `json_probe_failed=false`。
- `scripts/agy_cli_probe.py --image-path <真实K线图>` 的 `image_probe_failed=false`。
- 本轮认证日志 `current_run_keyring_auth_timeout_seen=false`。
- 能确认实际模型，或至少能把模型证据写入结果并在 UI 中标记为“未确认”。

### 不建议：授权码转发、手工 token、二进制 patch

这些方案都不适合作为项目方案：

- 授权码是一次性、绑定当前 OAuth 流程的临时凭据，不能跨进程或跨命令稳定复用。
- 手工提取/保存 OAuth token 没有官方文档支持，安全和合规风险高。
- 修改 AGY 二进制不是官方支持路径，会影响签名、升级和问题复现。

## 后续跟踪项

- 关注 `google-antigravity/antigravity-cli` issue #24 是否被修复。
- 每次 `agy update` 后重新运行 `scripts/agy_cli_probe.py --json-probe-timeout 90`。
- 如果官方新增 `--model`、API key、token cache 或 keyring timeout 参数，更新本方案并重新评估迁移可行性。
