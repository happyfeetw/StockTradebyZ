# R7 Legacy Write Freeze

Managing issue: #152
Parent epic: #23
Status date: 2026-05-27

This document freezes legacy file-system writers as compatibility-only
entrypoints. It does not remove historical data, change business behavior, or
retire any surface by itself. The freeze creates visible notices and a product
no-read guard so later retirement PRs have a stable baseline.

## Frozen Legacy Entrypoints

| Surface | Legacy Writes | Product Replacement | Freeze Action |
| --- | --- | --- | --- |
| `pipeline.cli preselect` | `data/candidates` | `POST /api/runs/preselect` | retired by default behind `STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1` |
| `agent.gemini_review` | `data/review` | `POST /api/runs/review/provider` | emits `R7 legacy write freeze` notice |
| `agent.gemini_cli_review` | `data/review` and provider raw evidence | `POST /api/runs/review/provider` | emits `R7 legacy write freeze` notice |
| `dashboard.export_kline_charts` | `data/kline` | `POST /api/runs/chart-export` | emits `R7 legacy write freeze` notice |
| `pipeline.archive_results` | `data/history` | `POST /api/runs/archive` | retired by default behind `STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1` |
| `workbench.runner` | `data/runs` and legacy workflow outputs | React/FastAPI Run Center | emits `R7 legacy write freeze` notice |
| `dashboard/app.py` | reads legacy candidates/raw data | React workstation | shows compatibility-only warning |
| `workbench/app.py` | reads/writes legacy workbench state | React workstation | shows compatibility-only warning |
| `start_workbench` | launches Streamlit workbench | React workstation | prints compatibility-only warning |

The remaining notices are informational. They do not block legacy behavior yet
because R7 retirement still requires surface-specific parity, migration,
product replacement, and rollback proof. Surfaces already retired by default
remain available only through their documented rollback flags.

## Product No-Read Guard

New React/FastAPI product code must not directly read these legacy generated
paths:

- `data/candidates`
- `data/review`
- `data/history`
- `data/kline`
- `data/runs`
- `candidates_latest.json`
- `suggestion.json`

Allowed exceptions:

- `apps/api/stocktrade_api/services/legacy_import.py`
- `apps/api/stocktrade_api/services/legacy_verify.py`

Those services are the explicit migration/import/verify boundary. Product
routes, storage repositories, domain code, and React code must use SQLite,
DuckDB, and product artifacts instead of reading legacy generated files
directly.

## Validation

Run:

```bash
scripts/harness/check.sh r7-legacy-write-freeze
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

The `r7-legacy-write-freeze` gate checks:

- this document exists and names the frozen surfaces;
- legacy entrypoints emit or display the compatibility-only notice;
- product source under `apps/api/stocktrade_api`, `apps/web/src`, and
  `src/stocktrade` does not directly reference legacy generated paths outside
  the legacy import boundary;
- simulated trading remains out of scope.

## Rollback

Rollback for this freeze is low risk:

- remove the notice calls and this gate if a legacy entrypoint must temporarily
  become a primary path again;
- do not delete or mutate `data/` during rollback;
- keep product SQLite, DuckDB, and artifact backups intact.

Any future PR that disables or removes a legacy writer must include its own
rollback note and product replacement proof.
