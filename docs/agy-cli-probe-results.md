# AGY CLI 探针结果

状态：首次探针，日期 2026-05-21，关联 GitHub issue：#11。

## 运行命令

只读能力探测：

```bash
python3 scripts/agy_cli_probe.py --skip-json-probe
```

非交互 JSON 探针：

```bash
python3 scripts/agy_cli_probe.py --json-probe-timeout 60
```

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
| 非交互 JSON 探针 | 未通过 |
| 阻断原因 | `agy --print` 要求重新认证，等待 30 秒后认证超时 |

## 结论

Phase 0 只读能力探测通过：当前 `agy` 具备继续探索所需的基础非交互入口，也能读取本地 Antigravity 设置文件。

Phase 1 非交互 JSON 探针未通过：当前登录状态不足以让 `agy --print` 完成模型调用。需要先在本机完成 Antigravity CLI 认证，再重跑探针。

即使认证修复，当前仍有两个迁移硬限制：

- 无 per-call `--model`，不能像 Gemini CLI 一样在脚本中明确指定模型。
- 无 `--output-format json/stream-json`，只能依赖 prompt 约束和 JSON 解析。

因此在模型可确认和 JSON 稳定性通过前，不能接入生产复评入口。

## 下一步

1. 从普通终端运行 `agy` 或 `agy --print "hello"`，按提示完成浏览器认证。
2. 重新运行：

   ```bash
   python3 scripts/agy_cli_probe.py --json-probe-timeout 60
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

