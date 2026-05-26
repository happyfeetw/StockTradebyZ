# Validation Gates

Use the narrowest meaningful gate first, then broaden if the touched surface
requires it.

## Gate Summary

| Gate | Command | Use When |
| --- | --- | --- |
| docs | `scripts/harness/check.sh docs` | Docs, AGENTS, architecture, harness text |
| contracts | `scripts/harness/check.sh contracts` | Candidate/review/history contracts and legacy paper-trading guardrails |
| python | `scripts/harness/check.sh python` | Python source or tests changed |
| product-refactor-readiness | `scripts/harness/check.sh product-refactor-readiness` | Product rewrite plans, target-stack proposals, UI/UX/backend/storage redesign |
| refactor-readiness | `scripts/harness/check.sh refactor-readiness` | Alias for product-refactor-readiness |
| quick | `scripts/harness/check.sh quick` | Default before final response |

## Docs Gate

Checks:

- `AGENTS.md` exists and links to core docs.
- `ARCHITECTURE.md` exists.
- agent harness docs are present.
- markdown links to local files resolve.

This gate should stay credential-free and safe in a clean clone.

## Contracts Gate

Checks source-level invariants that future agents must not accidentally erase:

- candidate identity preserves strategy;
- same-date candidate merge still exists;
- archive status values remain represented;
- Gemini CLI review still exposes retry/checkpoint/raw-log concepts;
- legacy paper trading still has local-only account/plan/fill surfaces so
  refactor work does not accidentally damage the out-of-scope module.

This gate is not a full business test. It is an early warning that an agent may
have removed a critical affordance.

## Python Gate

Checks:

- Python files compile.
- lightweight unit tests run when the environment has required dependencies.

Expected command:

```bash
scripts/harness/check.sh python
```

If dependencies such as pandas or numba are unavailable, report the failure and
run `docs` and `contracts` as the minimum fallback.

## Product Refactor Readiness Gate

Checks:

- full product rewrite scope is documented;
- business logic rewrite uses golden master and behavior parity rules;
- refactor execution phases R0 through R7 are documented;
- stop conditions are explicit;
- UI/UX quality bar covers accessibility, responsive layout, loading/error
  states, charts, and not-overdesigned constraints;
- architecture quality bar covers frontend, backend, storage, system,
  maintainability, observability, and runtime resource expectations;
- golden contracts include candidate identity, latest candidates,
  same-date merge, review keys, Gemini checkpoint, history archive, and run
  state;
- blocking preconditions are visible before implementation.

Expected command:

```bash
scripts/harness/check.sh product-refactor-readiness
```

Run this before broad edits, new storage layers, backend/runtime scaffolding,
UI replacement, core business logic rewrite, or deleting legacy paths.

## Runtime Gates

Runtime gates are intentionally not part of `quick` because they can require
credentials, large local data, or paid model capacity.

Run these only when the task touches the relevant path:

```bash
python -m pipeline.cli preselect --config config/rules_preselect.yaml --merge-same-date
python dashboard/export_kline_charts.py
python agent/gemini_cli_review.py --config config/gemini_cli_review.yaml
python -m pipeline.archive_results
./start_workbench
```

Before invoking Gemini, confirm that the task really requires model calls and
that existing `skip_existing` output cannot satisfy the validation.

## Change-Type Matrix

| Change Type | Minimum Gate | Broaden To |
| --- | --- | --- |
| README/docs only | docs | quick if links mention code contracts |
| strategy filter logic | python | runtime preselect on fixture or local data |
| candidate persistence | contracts + python | archive fixture test |
| Gemini review prompt/config | contracts + python | one small existing candidate with `skip_existing` |
| chart export | python | chart export smoke on one date |
| workbench UI | python | local Streamlit browser smoke |
| legacy paper trading rules | python | only when user explicitly reopens that scope |
| storage migration | docs + contracts + python | import/export fixture and rollback check |
| major product refactor | product-refactor-readiness + quick | phase-specific fixture, migration proof, rollback check |

## Major Refactor Matrix

| Phase | Minimum Gate | Additional Evidence |
| --- | --- | --- |
| R0 product charter | product-refactor-readiness + quick | scope, non-goals, and decision list |
| R1 business spec | contracts + python | golden masters and behavior parity notes |
| R2 target architecture | product-refactor-readiness | stack, schema, API, resource, and rollback plan |
| R3 core domain rewrite | python | golden master parity tests |
| R4 backend runtime/API | python | API contract and job lifecycle tests |
| R5 frontend UI/UX | python | fixture UI smoke and screenshot/browser notes |
| R6 storage cutover | product-refactor-readiness + quick | migration fixture and rollback drill |
| R7 hardening/retirement | product-refactor-readiness + quick | performance/resource evidence and final parity |
