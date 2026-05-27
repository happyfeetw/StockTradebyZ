# R7 Runtime Recovery And Concurrency

Managing issue: #152
Status date: 2026-05-27

This note defines the supported local runtime behavior for the product
FastAPI job runtime after R7 hardening. It covers product-owned runs only.
simulated trading remains out of scope.

## Runtime Recovery Rule

FastAPI startup recovery must close product runs that were left active by a
previous crashed or interrupted API process. The recovery path is implemented
through `JobRuntime.recover_interrupted_runs` and
`RunRepository.recover_interrupted_active_runs`.

Supported behavior:

- `queued` and `running` runs recover to `failed`;
- `cancelling` runs recover to `cancelled`;
- active steps on recovered runs move to the same terminal status;
- recovered runs receive a `RuntimeRecovery` summary and a warning event;
- recovery is a no-op when the SQLite schema has not been migrated yet.

This makes interrupted local runs visible in Run Center instead of leaving them
forever active. It does not retry business workflows automatically because
preselect, chart export, provider review, and archive writes can create
artifacts and analytics rows; automatic replay would need a separate idempotency
proof.

## Local Concurrency Rule

The supported deployment shape is one local FastAPI process plus one React web
frontend. Inside that API process, product workflow jobs are serialized by the
shared `JobRuntime` in-process workflow lock.

This lock covers:

- diagnostic runs;
- preselect runs;
- review and provider-review runs;
- archive runs;
- chart export runs.

Cancellation requests are not serialized behind the workflow lock. They remain
repository-level status requests and must not overwrite terminal runs.

This is a local-first safety boundary, not a distributed scheduler. Multiple API processes
writing the same SQLite/DuckDB/artifact roots remain unsupported for R7 unless a later PR adds
cross-process locking and proves it with tests.

## Acceptance Evidence

Runtime recovery and concurrency changes must prove:

- active stale runs are recovered on `create_app` startup;
- recovered active steps become terminal and diagnosable;
- a cancelling stale run recovers as `cancelled`;
- two concurrent product workflow calls on the same `JobRuntime` serialize
  before service execution;
- late cancellation still cannot overwrite terminal runs;
- no live Tushare or Gemini call is required.

Expected validation:

```bash
PYTHONPATH=apps/api:src python3 -m unittest tests.test_job_runtime_contracts
scripts/harness/check.sh r7-runtime-recovery
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

## Rollback

Rollback for this slice is to remove startup recovery and the in-process
workflow lock, then keep the older late-cancellation guard. If recovery causes
unexpected local state transitions, restore from a product backup and inspect
the run warning event before re-running the workflow manually.
