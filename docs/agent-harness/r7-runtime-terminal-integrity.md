# R7 运行时终态完整性

管理 issue：#152
父级 epic：#23
状态日期：2026-06-15

本文定义产品化运行时的终态规则。一个 run 或 step 一旦进入 `succeeded`、
`failed` 或 `cancelled`，后续运行时调用不得覆盖状态、时间戳、summary 或错误证据。

## 决策

- `RunRepository.transition_run()` 必须拒绝把终态 run 改成其他状态，或替换其
  终态 summary。
- `RunRepository.transition_step()` 必须拒绝把终态 step 改成其他状态，或替换其
  终态错误 payload。
- 迟到的取消请求必须保留原有终态 run 状态。
- 失败和取消的 run 必须继续能通过事件、summary、step 状态和错误 payload 诊断。
- 活跃 run 的 `summary.progress` 是运行证据的一部分。写入终态 summary、取消收尾、
  失败收尾和启动恢复时，如果新 summary 没有显式进度，必须保留已有 progress。
- 模拟交易仍不在范围内。

## 原因

R7 退休依赖 React/FastAPI 产品运行时成为可靠的本地执行入口。迟到的取消、重试、
恢复钩子或 handler 清理不能把已完成任务改回 `running`，不能覆盖失败 summary，
也不能擦掉 step 错误。终态不可变能让 UI 证据和回滚决策可信。

进度同样属于验收证据。长任务可能在 Tushare 请求、Gemini CLI 调用或归档写入时失败
或被取消；保留 progress 能让运行中心说明任务卡在“下载中”“复评中”或“写入归档”等
具体位置，而不是只留下一个泛化的 failed/cancelled 状态。

## 验证

预期命令：

```bash
scripts/harness/check.sh r7-runtime-terminal-integrity
```

该 gate 必须证明：

- 终态 run 覆盖尝试会抛出 `TerminalRunTransitionError`；
- 终态 step 覆盖尝试会抛出 `TerminalStepTransitionError`；
- 迟到取消仍保留已成功 run；
- 终态 summary 写入会保留已有 `summary.progress`；
- `scripts/harness/check.sh r7-retirement-plan` 把该规则纳入 R7 运行时硬化证据。
