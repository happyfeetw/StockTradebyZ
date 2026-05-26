# Agent Workflows

## Workflow 1: Small Code Change

1. Read `AGENTS.md` and the relevant source file.
2. Identify the contract touched by the change.
3. Make the smallest patch.
4. Run the narrow gate from [validation-gates.md](validation-gates.md).
5. Update docs only when behavior or operation changed.

## Workflow 2: Strategy Change

1. Read the matching strategy design doc in `docs/`.
2. Inspect `pipeline/Selector.py`, `pipeline/select_stock.py`, and existing tests.
3. Preserve `(code, strategy)` identity.
4. Add or update focused tests with small DataFrame fixtures.
5. Run `scripts/harness/check.sh python`.
6. If local data exists and the user wants runtime validation, run preselect with
   a specific date and record candidate count deltas.

## Workflow 3: Gemini Review Change

1. Read `docs/gemini-cli-review-plan.md` and
   `docs/classic-pattern-review-scoring.md`.
2. Preserve Python-owned result writes, raw logs, checkpoint, `skip_existing`,
   retry/backoff, and batch order validation.
3. Avoid real Gemini calls unless required for the task.
4. Validate parser/normalization with unit tests first.
5. If model validation is required, use a tiny candidate set and document model,
   command, output path, and whether quota was consumed.

## Workflow 4: Workbench Change

1. Read `docs/local-stock-workbench-product-design.md`.
2. Identify whether the change is UI-only, run orchestration, result loading, or
   config mutation.
3. Preserve run snapshots under `data/runs/{run_id}`.
4. Preserve visible background status, log display, and cancellation.
5. Run `scripts/harness/check.sh python`.
6. For UI behavior, start `./start_workbench` and capture the page/path tested.

## Workflow 5: History or Storage Change

1. Read `docs/history-results-archive-design.md`.
2. Trace both writer and reader before editing:
   - candidates writer: `pipeline/pipeline_io.py`
   - archive writer: `pipeline/archive_results.py`
   - workbench readers: `workbench/app.py`
   - legacy paper trading readers: `paper_trading/core.py` only if the user
     explicitly reopens that out-of-scope module
3. Preserve same-day multi-strategy semantics.
4. Add fixture-based tests before using local `data/`.
5. Run `scripts/harness/check.sh contracts` and relevant tests.

## Workflow 6: Product Refactor

1. Confirm the managing GitHub issue and planned PR.
2. Read [issue-pr-governance.md](issue-pr-governance.md),
   [product-refactor-charter.md](product-refactor-charter.md),
   [business-logic-spec.md](business-logic-spec.md),
   [uiux-quality-bar.md](uiux-quality-bar.md),
   [architecture-quality-bar.md](architecture-quality-bar.md),
   [refactor-execution.md](refactor-execution.md),
   [refactor-contracts.md](refactor-contracts.md), and
   [refactor-preconditions.md](refactor-preconditions.md).
3. Name the active phase: R0, R1, R2, R3, R4, R5, R6, or R7.
4. Resolve or record any precondition that affects target stack, correctness,
   migration safety, or product scope.
5. Define the behavior oracle and golden master evidence for every rewritten
   domain behavior.
6. Define the compatibility or migration rule for every touched `data/`
   artifact and CLI handoff.
7. Rewrite business logic in the confirmed new stack only after parity evidence
   exists.
8. Create rollback instructions before deleting or replacing behavior.
9. Run `scripts/harness/check.sh product-refactor-readiness` before
   implementation slices and `scripts/harness/check.sh quick` before
   finalizing.

## Workflow 7: Review Before Finalizing

Ask:

- Did the patch preserve the contract identity `(code, strategy)`?
- Did it avoid committing local data, secrets, or generated raw logs?
- Is the validation evidence tied to the changed surface?
- If a doc changed, is there a check or owner to prevent drift?
- If a repeated failure was fixed, did it become a test, check, or doc rule?
