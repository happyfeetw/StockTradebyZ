# R7 Run All Retirement

Managing issue: #152
Parent epic: #23
Status date: 2026-05-28

`run_all.py` is the legacy one-command orchestration wrapper. It chains market
fetch, legacy preselect, legacy chart export, legacy Gemini review, and a final
read of `data/candidates/candidates_latest.json` plus
`data/review/{pick_date}/suggestion.json`. In R7 it is retired by default
because every in-scope workflow has a product-owned React/FastAPI path.

## Supported Product Path

The supported local product entrypoint is:

```bash
./start_product
```

From the React/FastAPI Run Center, run the product-owned workflow steps:

- `POST /api/runs/preselect`
- `POST /api/runs/chart-export`
- `POST /api/runs/review/provider`
- `POST /api/runs/archive`

These paths write product SQLite, DuckDB, and artifact state. They do not depend
on `run_all.py` or on legacy generated files as a product source of truth.

## Retirement Rule

Default behavior:

- `python run_all.py` exits with `R7 run_all retirement`;
- the guard runs after argument parsing and before any `_run(...)` subprocess;
- no legacy child entrypoint is invoked by default;
- no legacy candidate or suggestion file is read by default;
- the script points users to `./start_product`, Run Center, and product APIs.

The rollback flag is:

```bash
STOCKTRADE_ALLOW_LEGACY_RUN_ALL=1
```

This flag only allows the legacy wrapper itself to continue. It must not
silently enable child legacy flags. A full rollback run must explicitly opt into
the child surfaces that are needed for the requested path.

## Rollback

Use rollback only for migration, parity, or incident investigation:

```bash
STOCKTRADE_ALLOW_LEGACY_RUN_ALL=1 \
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1 \
STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1 \
STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1 \
python run_all.py --skip-fetch
```

If the API-key reviewer path is needed, replace the Gemini CLI flag with
`STOCKTRADE_ALLOW_LEGACY_GEMINI_API_REVIEW=1` and pass
`--reviewer gemini-api`.

This rollback path does not archive by itself. If a separate legacy archive
write is required, run `pipeline.archive_results` only with
`STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1`.

Rollback rules:

- do not mutate or delete historical `data/` files as part of this retirement;
- keep product backups intact before using legacy rollback for comparison;
- do not combine simulated trading changes with this retirement.

## Validation

Run:

```bash
PYTHONPATH=apps/api:src python3 -m unittest tests.test_run_all_retirement_harness
scripts/harness/check.sh r7-run-all-retirement
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh r7-legacy-write-freeze
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

The `r7-run-all-retirement` gate proves:

- the retirement decision and rollback command are documented;
- `legacy_compat.py` owns the `STOCKTRADE_ALLOW_LEGACY_RUN_ALL` flag;
- `run_all.py` exits before subprocesses and legacy recommendation file reads;
- README and Gemini CLI notes point users to the product path first;
- simulated trading remains out of scope.
