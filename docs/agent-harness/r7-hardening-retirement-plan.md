# R7 Hardening And Legacy Retirement Plan

Managing issue: #152
Parent epic: #23
Status date: 2026-05-27

This document defines how R7 turns the rebuilt React/FastAPI/SQLite/DuckDB
product into the supported local product while retiring legacy compatibility
surfaces. R7 is not permission to delete historical `data/` records. Legacy
files remain migration inputs and rollback evidence until a PR proves otherwise.

## Scope Boundary

In scope:

- product runtime hardening for FastAPI job execution, cancellation,
  concurrency, runtime recovery, error recovery, and backup/restore drills;
- final React workstation browser proof across primary research workflows;
- resource and storage-growth evidence for local runs;
- legacy write freeze and retirement sequencing for `pipeline/`, `agent/`,
  `dashboard/`, `workbench/`, and `start_workbench`;
- final parity and migration evidence before disabling legacy entrypoints.

Out of scope:

- simulated or paper trading under `data/trading/`;
- deleting historical `data/` files without a dedicated retirement PR and
  rollback proof;
- changing strict business behavior without golden-master or product-contract
  evidence;
- live Tushare or Gemini calls as required acceptance evidence.

## Legacy Surface Matrix

| Surface | Current Role | R7 Decision | Required Proof Before Retirement |
| --- | --- | --- | --- |
| `pipeline/cli.py` | Legacy preselect CLI writes `data/candidates/` | Freeze as compatibility CLI, then retire after product API run proof covers local workflow docs | Golden parity, product preselect API proof, rollback note |
| `pipeline/pipeline_io.py` | Candidate JSON writer/reader for `candidates_latest.json` | Keep import/read compatibility; block new product writes from depending on it | Legacy import verify, product UI/API no-read proof |
| `agent/gemini_review.py` | Legacy Gemini API review writer under `data/review/` | Mark legacy-only; do not expand | Review parity fixtures and product provider evidence proof |
| `agent/gemini_cli_review.py` | Legacy Gemini CLI review, retry, raw logs, checkpoints | Freeze semantics as behavior oracle; retire after product provider reaches equivalent operational proof | Retry/checkpoint parity, product provider evidence artifacts, rollback note |
| `pipeline/archive_results.py` | Legacy archive writer under `data/history/` | Keep as migration source until final archive retirement PR | Product archive API proof, history import verify, rollback note |
| `dashboard/export_kline_charts.py` | Legacy chart export to `data/kline/` | Keep as chart oracle; product chart export is supported path | Product chart artifact proof and visual smoke |
| `dashboard/app.py` | Legacy Streamlit single-stock dashboard | Mark legacy UI; no new product work | React browser smoke covers replacement workflow |
| `workbench/app.py` and `workbench/runner.py` | Streamlit local workbench and background orchestration | Freeze after product workstation covers primary flows | Product browser smoke, cancellation/error-state proof, user workflow notes |
| `start_workbench` | Legacy launch script | Keep as compatibility launcher until final retirement | `start_product` React/FastAPI local launch docs and smoke |
| `data/candidates`, `data/review`, `data/history`, `data/kline`, `data/runs` | Legacy generated state and migration input | Do not delete in R7 planning; retire reads/writes by surface-specific PR | Backup, migration verify, product no-read proof |
| `data/trading` | Paper/simulated trading state | Excluded from product refactor | Explicit exclusion remains in gates |

## R7 Sequence

1. R7 plan and gate.
   - Add this document as the source-of-truth plan for #152.
   - Add `r7-retirement-plan` as a harness gate.
   - Include the R7 gate in `product-refactor-readiness`.

2. Runtime hardening.
   - Define supported local concurrency behavior for product jobs.
   - Add cancellation and recovery tests for long-running or staged jobs.
   - Ensure failed jobs leave enough SQLite events and summaries for UI
     diagnosis.
   - Use `docs/agent-harness/r7-runtime-recovery.md` and
     `scripts/harness/check.sh r7-runtime-recovery` as the reproducible
     recovery/concurrency proof path.
   - Preserve terminal run and step states so late cancellation, retry, or
     recovery code cannot overwrite `succeeded`, `failed`, or `cancelled`
     evidence. Guard this with
     `scripts/harness/check.sh r7-runtime-terminal-integrity`.

3. Resource envelope.
   - Capture credential-free fixture evidence for API startup, product workflow
     runtime, memory envelope, artifact size, and SQLite/DuckDB growth.
   - Keep thresholds conservative and local-first.
   - Use `docs/agent-harness/r7-resource-envelope.md` and
     `scripts/harness/check.sh r7-resource-envelope` as the reproducible
     evidence path.

4. Final browser proof.
   - Use deterministic fixtures, not live Tushare or Gemini.
   - Recheck Overview, Run Center, Candidates, Reviews, Archive, Analytics,
     Settings, Migrations, and error states at desktop and mobile widths.
   - Verify no horizontal overflow and that chart/provider evidence artifacts
     are inspectable.
   - Record evidence in `docs/agent-harness/r7-final-browser-proof.md` and run
     `scripts/harness/check.sh r7-browser-proof`.

5. Legacy write freeze.
   - Add warnings or documentation to legacy entrypoints before removal.
   - Prevent new React/FastAPI code from reading legacy generated files except
     through explicit migration/import services.
   - Preserve compatibility tests for old file formats.
   - Record the frozen surfaces in
     `docs/agent-harness/r7-legacy-write-freeze.md` and run
     `scripts/harness/check.sh r7-legacy-write-freeze`.

6. Retirement PRs.
   - Retire one legacy surface per PR.
   - Each PR must include rollback notes, parity evidence, migration proof, and
     product replacement proof.
   - Do not combine paper-trading changes with product retirement work.
   - Product launcher proof uses `docs/agent-harness/r7-product-launcher.md` and
     `scripts/harness/check.sh r7-product-launcher` as the replacement launch
     proof before retiring `start_workbench`.

## Validation Requirements

Minimum gates for R7 planning PRs:

```bash
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

Implementation PRs that harden runtime or retire legacy surfaces must also
prove the touched path:

- runtime hardening: targeted job lifecycle/cancellation tests plus `quick`;
- runtime recovery: `scripts/harness/check.sh r7-runtime-recovery`, targeted
  job lifecycle tests, and `quick`;
- runtime terminal integrity: `scripts/harness/check.sh
  r7-runtime-terminal-integrity` plus targeted job lifecycle tests;
- resource evidence: fixture command output or checked-in report with
  reproducible command, plus `scripts/harness/check.sh r7-resource-envelope`;
- browser proof: deterministic fixture setup, desktop/mobile screenshots, and
  no-overflow notes, plus `scripts/harness/check.sh r7-browser-proof`;
- legacy write freeze: product API no-read proof, compatibility import test,
  and `scripts/harness/check.sh r7-legacy-write-freeze`;
- product launcher: `start_product` replacement path, bash syntax check, and
  `scripts/harness/check.sh r7-product-launcher`;
- retirement: rollback note, parity fixture, migration verify, and product
  replacement proof.

## Rollback Rules

- Before disabling any legacy write path, create a product backup that includes
  SQLite, DuckDB, and artifacts.
- Restore must never mutate legacy `data/`.
- Rollback for a failed retirement is: re-enable the legacy entrypoint, restore
  the product backup if product state was mutated, and keep migration quarantine
  evidence.
- Legacy files can be ignored by product flows, but deletion requires a separate
  user-visible decision and rollback note.

## Completion Boundary

R7 is complete only when:

- product smoke passes from a clean fixture state;
- product runtime hardening tests cover cancellation, failure, and recovery;
- resource and storage growth evidence is documented;
- legacy file-system and Streamlit/workbench paths are retired or explicitly
  documented as compatibility-only with rollback notes;
- final parity evidence proves business behavior is unchanged;
- `scripts/harness/check.sh r7-retirement-plan`,
  `scripts/harness/check.sh product-refactor-readiness`, and
  `scripts/harness/check.sh quick` pass.
