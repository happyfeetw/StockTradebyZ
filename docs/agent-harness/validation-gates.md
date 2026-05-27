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
| ui-smoke-fixture | `scripts/harness/check.sh ui-smoke-fixture` | R5 React workstation browser-review fixture |
| storage-cutover-plan | `scripts/harness/check.sh storage-cutover-plan` | R6 source-of-truth and rollback planning |
| r7-retirement-plan | `scripts/harness/check.sh r7-retirement-plan` | R7 hardening, resource, and legacy retirement planning |
| r7-browser-proof | `scripts/harness/check.sh r7-browser-proof` | R7 desktop/mobile React workstation proof and chart artifact inspection |
| r7-legacy-write-freeze | `scripts/harness/check.sh r7-legacy-write-freeze` | R7 compatibility-only notices and product no-read guard for legacy generated files |
| r7-product-launcher | `scripts/harness/check.sh r7-product-launcher` | R7 default React/FastAPI local launcher and start_workbench replacement |
| r7-runtime-terminal-integrity | `scripts/harness/check.sh r7-runtime-terminal-integrity` | R7 run/step terminal-state immutability for product job diagnostics |
| r7-resource-envelope | `scripts/harness/check.sh r7-resource-envelope` | R7 credential-free runtime, memory, storage-growth, and artifact-growth evidence |
| r7-runtime-recovery | `scripts/harness/check.sh r7-runtime-recovery` | R7 FastAPI startup recovery and local product job concurrency semantics |
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

## UI Smoke Fixture Gate

Checks:

- `scripts/harness/seed_ui_smoke.py` and `scripts/harness/ui_smoke_app.py`
  compile;
- a temporary SQLite product database can be migrated and seeded;
- a temporary DuckDB analytics database can be seeded;
- smoke artifacts for Run Center and Archive chart evidence are written.

Expected command:

```bash
scripts/harness/check.sh ui-smoke-fixture
```

This gate is credential-free and safe in a clean clone. It does not replace the
manual/browser evidence in [r5-ui-browser-smoke.md](r5-ui-browser-smoke.md).

## Storage Cutover Plan Gate

Checks:

- the R6 source-of-truth ownership matrix exists;
- simulated/paper trading is explicitly excluded;
- candidate, review, archive/history, chart artifact, provider evidence,
  backup/restore, migration, and rollback decisions are documented;
- the next implementation target is explicit before write behavior changes.

Expected command:

```bash
scripts/harness/check.sh storage-cutover-plan
```

Run this before any PR that changes product-owned storage, legacy import,
backup/restore, or legacy write-path behavior.

## R7 Retirement Plan Gate

Checks:

- the R7 issue and scope boundary are documented;
- simulated/paper trading remains explicitly excluded;
- legacy surfaces under `pipeline/`, `agent/`, `dashboard/`, `workbench`, and
  `start_workbench` have retirement decisions;
- runtime hardening, resource envelope, final browser proof, legacy write
  freeze, retirement PRs, and rollback rules are documented.

Expected command:

```bash
scripts/harness/check.sh r7-retirement-plan
```

Run this before PRs that harden product runtime, collect resource evidence,
freeze legacy writes, or retire compatibility entrypoints.

## R7 Browser Proof Gate

Checks:

- `docs/agent-harness/r7-final-browser-proof.md` documents the reproducible
  smoke setup and desktop/mobile route matrix;
- the UI smoke fixture can be seeded mechanically;
- Overview, Run Center, Candidates, Reviews, Archive, Analytics, Settings, and
  Migrations are included in the proof matrix;
- chart artifact inspection and browser console/no-overflow evidence are
  documented;
- simulated trading remains out of scope.

Expected command:

```bash
scripts/harness/check.sh r7-browser-proof
```

This gate does not automate browser control. The browser pass is recorded in
the document and must be refreshed when UI layout, route behavior, artifact
inspection, or smoke fixture state changes.

## R7 Legacy Write Freeze Gate

Checks:

- `docs/agent-harness/r7-legacy-write-freeze.md` documents frozen legacy
  writers, product replacements, product no-read rules, and rollback;
- legacy CLI/Streamlit entrypoints emit or display `R7 legacy write freeze`
  compatibility-only notices;
- product source under `apps/api/stocktrade_api`, `apps/web/src`, and
  `src/stocktrade` does not directly reference legacy generated paths except
  through the explicit legacy import service;
- simulated trading remains out of scope.

Expected command:

```bash
scripts/harness/check.sh r7-legacy-write-freeze
```

Run this before PRs that freeze legacy writes, add new product storage paths,
or start surface-specific retirement.

## R7 Product Launcher Gate

Checks:

- `start_product` exists and is executable;
- the launcher starts `stocktrade_api.main:app` and the React/Vite workstation
  with local host/port defaults;
- `start_workbench` points users to `./start_product` for supported
  React/FastAPI workflows;
- simulated trading remains out of scope.

Expected command:

```bash
scripts/harness/check.sh r7-product-launcher
```

Run this before PRs that change local product startup, replacement launch docs,
or `start_workbench` retirement.

## R7 Runtime Terminal Integrity Gate

Checks:

- `docs/agent-harness/r7-runtime-terminal-integrity.md` documents the terminal
  state rule and validation command;
- `RunRepository.transition_run()` rejects attempts to overwrite a terminal
  run status or summary;
- `RunRepository.transition_step()` rejects attempts to overwrite a terminal
  step status or error payload;
- late cancellation preserves the existing terminal run state;
- simulated trading remains out of scope.

Expected command:

```bash
scripts/harness/check.sh r7-runtime-terminal-integrity
```

Run this before PRs that change product runtime status transitions,
cancellation, recovery, or error-diagnostics behavior.

## R7 Resource Envelope Gate

Checks:

- `docs/agent-harness/r7-resource-envelope.md` documents the reproducible
  command, conservative guardrails, and latest credential-free evidence;
- `scripts/harness/resource_envelope.py` runs the product-owned API workflow
  from temporary fixture state;
- the workflow covers preselect, chart export, provider review, archive,
  SQLite growth, DuckDB growth, artifact growth, and memory evidence;
- simulated trading and live Tushare/Gemini calls remain out of scope.

Expected command:

```bash
scripts/harness/check.sh r7-resource-envelope
```

Run this before PRs that claim resource envelope evidence, materially change
product workflow storage writes, or prepare final R7 browser/legacy retirement
proof.

## R7 Runtime Recovery Gate

Checks:

- `docs/agent-harness/r7-runtime-recovery.md` documents FastAPI startup
  recovery, local concurrency rules, unsupported multi-process writes, and
  rollback;
- `JobRuntime` exposes recovery and serializes product workflow jobs with an
  in-process workflow lock;
- `RunRepository` can recover interrupted `queued`, `running`, and `cancelling`
  runs into terminal states with `RuntimeRecovery` diagnostics;
- targeted job runtime tests cover startup recovery, serialized workflow calls,
  and late-cancellation terminal protection;
- simulated trading remains out of scope.

Expected command:

```bash
scripts/harness/check.sh r7-runtime-recovery
```

Run this before PRs that change FastAPI job lifecycle behavior, startup
recovery, local concurrency semantics, cancellation behavior, or final runtime
hardening evidence.

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
| storage cutover plan | storage-cutover-plan + product-refactor-readiness | quick before PR |
| R7 planning | r7-retirement-plan + product-refactor-readiness | quick before PR |
| R7 browser proof | r7-browser-proof + web build/lint | screenshots, console/API notes, no-overflow matrix |
| R7 legacy write freeze | r7-legacy-write-freeze + r7-retirement-plan | quick before PR |
| R7 product launcher | r7-product-launcher + r7-retirement-plan | bash syntax + targeted harness test + quick before PR |
| R7 runtime terminal integrity | r7-runtime-terminal-integrity + r7-retirement-plan | quick before PR |
| R7 resource evidence | r7-resource-envelope + r7-retirement-plan | quick before PR |
| R7 runtime recovery | r7-runtime-recovery + r7-retirement-plan | targeted job lifecycle tests + quick before PR |
| major product refactor | product-refactor-readiness + quick | phase-specific fixture, migration proof, rollback check |
| R5 UI browser review | ui-smoke-fixture + web build/lint | browser screenshots and no-overflow notes |

## Major Refactor Matrix

| Phase | Minimum Gate | Additional Evidence |
| --- | --- | --- |
| R0 product charter | product-refactor-readiness + quick | scope, non-goals, and decision list |
| R1 business spec | contracts + python | golden masters and behavior parity notes |
| R2 target architecture | product-refactor-readiness | stack, schema, API, resource, rollback plan, and status map |
| R3 core domain rewrite | python | golden master parity tests |
| R4 backend runtime/API | python | API contract and job lifecycle tests |
| R5 frontend UI/UX | ui-smoke-fixture + python | fixture UI smoke and screenshot/browser notes |
| R6 storage cutover | storage-cutover-plan + product-refactor-readiness + quick | migration fixture and rollback drill |
| R7 hardening/retirement | r7-retirement-plan + product-refactor-readiness + quick | r7-product-launcher for local launch changes, r7-runtime-terminal-integrity and r7-runtime-recovery for job lifecycle changes, r7-resource-envelope when runtime/storage changes, r7-browser-proof for UI changes, r7-legacy-write-freeze before retirement, rollback, and final parity |
