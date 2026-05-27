# R7 Runtime Terminal Integrity

Managing issue: #152
Parent epic: #23
Status date: 2026-05-27

This document defines the R7 rule for product job terminal states. Once a run or
step reaches `succeeded`, `failed`, or `cancelled`, later runtime calls must not
overwrite that state, timestamps, summary, or error evidence.

## Decision

- `RunRepository.transition_run()` must reject attempts to mutate a terminal
  run into another status or replace its terminal summary.
- `RunRepository.transition_step()` must reject attempts to mutate a terminal
  step into another status or replace its terminal error payload.
- Late cancellation must keep the previous terminal run state.
- Failed and cancelled runs must remain diagnosable through stored events,
  summaries, step status, and error payloads.
- Simulated trading remains out of scope.

## Why This Matters

R7 retirement depends on the React/FastAPI product being the reliable local
runtime. A late cancellation, retry, recovery hook, or handler cleanup must not
turn a completed job back into `running`, overwrite a failed summary, or erase a
step error. Terminal-state immutability keeps UI evidence and rollback decisions
trustworthy.

## Validation

Expected command:

```bash
scripts/harness/check.sh r7-runtime-terminal-integrity
```

The gate must prove:

- terminal run overwrite attempts raise `TerminalRunTransitionError`;
- terminal step overwrite attempts raise `TerminalStepTransitionError`;
- late cancellation still preserves a succeeded run;
- `scripts/harness/check.sh r7-retirement-plan` includes this rule as R7
  runtime hardening evidence.
