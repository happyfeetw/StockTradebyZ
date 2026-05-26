# Product Refactor Execution Harness

This harness exists for a full product-level rewrite. The goal is to preserve
core business behavior while replacing the implementation, UI/UX, frontend
architecture, backend architecture, system architecture, and storage model with
a more productized technical foundation.

This is not an adapter-only migration. The legacy system is the behavior
oracle, not the target architecture. Existing code can be read, tested, and used
to generate golden masters, but the new product should own its implementation
in the confirmed new stack.

## Refactor Doctrine

- Preserve observable business behavior, not old source structure.
- Rewrite core business logic in the new stack after behavior specs and golden
  masters exist.
- Treat the old file-system layout as source data and compatibility evidence,
  not the final storage design.
- Build product workflows first: configure, run, monitor, inspect, review,
  archive, and analyze.
- Keep simulated trading / paper trading outside the product-level refactor
  unless the user explicitly reopens that scope.
- Make UI/UX acceptance measurable, not subjective.
- Keep the architecture boring, modular, observable, and resource-conscious.
- Do not begin destructive replacement until blocking decisions in
  [refactor-preconditions.md](refactor-preconditions.md) are resolved.

## Phase Model

### R0 Product Charter and Decision Freeze

Goal: define what the rebuilt product is before choosing implementation shape.

Required artifacts:

- [product-refactor-charter.md](product-refactor-charter.md);
- confirmed or explicitly deferred decisions from
  [refactor-preconditions.md](refactor-preconditions.md);
- product workflow inventory for current and target surfaces;
- baseline `scripts/harness/check.sh product-refactor-readiness` output.

Exit gate:

- no blocking precondition is hidden;
- scope, non-goals, and implementation constraints are explicit.

### R1 Business Logic Specification

Goal: convert legacy behavior into testable product rules.

Required artifacts:

- [business-logic-spec.md](business-logic-spec.md);
- golden master cases for candidate selection, same-date merge, Gemini review
  normalization, history archive, and chart evidence;
- fixture strategy for no-credential and optional live-data checks.

Exit gate:

- business behavior parity can be tested without reading old code manually;
- intentional behavior changes are listed separately from regressions.

### R2 Target Architecture and Data Model

Goal: define the new frontend, backend, system, and storage architecture before
large implementation begins.

Required artifacts:

- [architecture-quality-bar.md](architecture-quality-bar.md);
- target module boundaries and API contracts;
- SQLite/DuckDB storage schema and migration plan;
- runtime resource envelope for local development and normal operation.

Exit gate:

- React/Vite/TypeScript, FastAPI, Python domain logic, SQLite, and DuckDB
  decisions remain in force;
- data ownership, job ownership, and API ownership are explicit;
- rollback and import/export strategy exists.

### R3 Core Domain Rewrite

Goal: reimplement core business logic in the new stack.

Rules:

- Use legacy behavior and golden masters as the oracle.
- Keep `(code, strategy)` as a first-class identity unless the user approves a
  replacement identity and migration.
- Avoid calling Gemini or external data providers in unit-level parity tests.
- Separate pure domain logic from IO, storage, jobs, and UI.

Exit gate:

- golden master parity tests pass;
- business logic is isolated, typed, and documented;
- any intentional deviation has user approval.

### R4 Backend Runtime and APIs

Goal: build a maintainable product backend around the rewritten domain logic.

Rules:

- Jobs expose status, progress, logs, artifacts, error state, retry, and
  cancellation.
- APIs are stable enough for frontend and test automation.
- Runtime code owns storage transactions and avoids hidden file writes.
- Resource usage stays reasonable for local operation.

Exit gate:

- API contract tests pass;
- job lifecycle tests cover success, failure, cancellation, and resume;
- observability is machine-readable.

### R5 Frontend Product UI/UX

Goal: build a practical, attractive, modern UI with friendly UX and no
overdesign.

Required artifact:

- [uiux-quality-bar.md](uiux-quality-bar.md).

Rules:

- UI structure follows real user workflows, not implementation modules.
- Data-heavy screens prioritize scanability, comparison, and repeated action.
- Loading, empty, error, long-running, and partial-result states are designed.
- Accessibility, keyboard navigation, responsive layout, and chart readability
  are acceptance requirements.

Exit gate:

- Playwright or browser smoke covers primary flows;
- desktop and mobile screenshots show usable layout;
- UI quality bar is reviewed against the touched screens.

### R6 Data Migration and Storage Cutover

Goal: move from file-system state to the approved storage architecture.

Rules:

- Legacy files are imported through explicit migration code.
- New writes go through the new storage layer after cutover.
- Historical results remain traceable by date, strategy, code, run, and review.
- Rollback restores a usable product state.

Exit gate:

- import/export fixture passes;
- sampled local migration passes if data is available;
- old writes are disabled only after new writes are verified.

### R7 Hardening, Performance, and Legacy Retirement

Goal: make the rebuilt product dependable before old paths are removed.

Rules:

- Retire legacy code only after parity, migration, and product-flow evidence.
- Track startup time, job runtime, memory, and storage growth.
- Keep operational docs and user-facing workflows current.

Exit gate:

- product smoke passes from clean state;
- quick and product-refactor-readiness gates pass;
- legacy removal has rollback notes and final parity evidence.

## Stop Conditions

Stop and ask for confirmation when any of these happen:

- a task would deviate from React/Vite/TypeScript, FastAPI, Python domain
  logic, SQLite, or DuckDB;
- business behavior would change without a named product decision;
- `(code, strategy)` identity would be removed or weakened;
- historical `data/` records would be discarded or rewritten;
- UI scope expands into unrelated features before primary workflows work;
- backend introduces real trading, broker integration, multi-user auth, or
  cloud deployment without approval;
- Gemini calls would be consumed without a task-specific reason;
- runtime resource usage becomes unreasonable for local use;
- test evidence cannot distinguish intentional behavior change from regression.

## Agent Loop

For every product refactor task:

1. Name the active phase.
2. Confirm the managing GitHub issue and PR plan from
   [issue-pr-governance.md](issue-pr-governance.md).
3. Read the product charter, business spec, quality bar, and relevant contract.
4. State the behavior oracle and parity evidence.
5. State whether any precondition blocks implementation.
6. Patch the smallest phase slice.
7. Run the phase gate and `scripts/harness/check.sh quick`.
8. Update the harness docs when a decision becomes durable.
