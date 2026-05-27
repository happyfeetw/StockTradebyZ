# Refactor Contracts

These contracts are the behavior and migration surface for a major product
rewrite. A new frontend, backend, database, job runtime, or core domain
implementation may change implementation details, but it must preserve,
intentionally replace, or explicitly migrate these behaviors.

The target is not to wrap the current code forever. Compatibility adapters are
temporary tools for golden masters, imports, exports, and rollback. The new
stack should own the final implementation.

## Candidate Identity

- Contract: `(code, strategy)` is the identity for candidates.
- Current writer: `pipeline/pipeline_io.py`.
- Current readers: archive, Gemini review, workbench, and legacy paper trading.
- Migration rule: a new storage model must store both fields as first-class
  fields, not infer strategy from file name or run context.
- Evidence: fixture with the same stock code selected by two strategies.

## Candidate Files

- Contract: `candidates_latest.json` and dated candidate files are current
  handoff artifacts.
- Current writer: `pipeline/pipeline_io.py`.
- Current readers: review, legacy archive rollback, workbench, migration
  import, and manual inspection.
- Migration rule: adapters must support reading legacy files until imported
  data is verified.
- Evidence: import fixture that preserves candidate count, code, strategy,
  selected date, score, and reason fields.

## Same-Date Merge

- Contract: `--merge-same-date` and merge-same-date behavior preserve
  multi-strategy candidates for the same trading date.
- Current owner: `pipeline/pipeline_io.py`.
- Migration rule: a new job runtime cannot collapse records by code alone.
- Evidence: fixture with two strategies for one date and one repeated code.

## Review Key and Status

- Contract: `review_key` matches review output to `(code, strategy)` and review
  status values include `recommended` and `unreviewed`.
- Product owner: `apps/api/stocktrade_api/services/archive_runs.py` and
  `apps/api/stocktrade_api/storage/archive_repository.py`.
- Legacy compatibility owner: `pipeline/archive_results.py`, retired by default
  behind `STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1`.
- Migration rule: review records must be joinable to candidate records without
  ambiguity.
- Evidence: archive fixture with matched, unmatched, recommended, and unreviewed
  rows.

## Gemini Review Output

- Contract: Gemini review produces normalized review artifacts, including
  `suggestion.json` style content and raw evidence when configured.
- Product owner: `apps/api/stocktrade_api/services/review_provider_runs.py`
  with `apps/api/stocktrade_api/services/gemini_cli_provider.py`.
- Legacy compatibility owner: `agent/gemini_cli_review.py`, retired by default
  behind `STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1`.
- Migration rule: any new service boundary must keep Python-owned final writes,
  parser validation, and reproducible raw input/output logs.
- Evidence: parser test with no network call and one optional tiny model smoke
  only when explicitly needed.

## Gemini Reliability State

- Contract: `gemini_cli_review_checkpoint.json`, retry backoff, batch order
  validation, `skip_existing`, and raw CLI log retention protect quota and
  debuggability.
- Product owner: `GeminiCliReviewProviderExecutor`.
- Legacy compatibility owner: `agent/gemini_cli_review.py`, retired by default
  behind `STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1`.
- Migration rule: queue or backend retries must not remove checkpoint semantics.
- Evidence: retry/checkpoint fixture and resume behavior test.

## History Archive

- Contract: product archive records in SQLite/DuckDB are durable product
  records; archived results under `data/history` are durable legacy
  migration/rollback records.
- Product owner: `ArchiveRunService` with `archive_snapshots`, `archive_rows`,
  and DuckDB `archive_facts`.
- Legacy compatibility owner: `pipeline/archive_results.py`, retired by default
  behind `STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1`.
- Current readers: product archive APIs, migration import/verify, legacy
  workbench rollback, manual review, and future analytics.
- Migration rule: product storage must import or intentionally supersede legacy
  history before deleting old readers or files.
- Evidence: product archive API fixture plus legacy history import/export
  fixture for summary, all rows, and strategy partitions.

## Run State

- Contract: `run_state.json`, per-run logs, and snapshots expose progress and
  failure context.
- Current owner: workbench orchestration.
- Migration rule: new backend jobs must provide machine-readable status with
  step, state, start time, end time, command, and error fields.
- Evidence: successful run fixture and failed run fixture.

## Legacy Paper Trading State

Simulated trading / paper trading is outside the product-level refactor scope.
Do not rewrite, migrate, or add UI for `data/trading` as part of the product
rewrite unless the user explicitly reopens that scope.

The generic contracts gate still checks `paper_trading/core.py` so refactor work
does not accidentally damage the legacy module.

## Chart and Evidence Assets

- Contract: chart exports and review evidence remain inspectable by date,
  code, and strategy.
- Current owners: `dashboard/` and review pipeline.
- Migration rule: UI asset storage may move, but links from candidate and review
  records must stay resolvable.
- Evidence: chart fixture or sampled local export with stable paths.
