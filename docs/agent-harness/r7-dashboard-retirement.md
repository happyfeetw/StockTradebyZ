# R7 Dashboard Retirement

Managing issue: #152
Status date: 2026-05-27

This document retires `dashboard/app.py` as a default user-facing surface. The
single-stock Streamlit dashboard remains available only behind
`STOCKTRADE_ALLOW_LEGACY_DASHBOARD=1` for migration, parity, or rollback checks.

## Decision

`dashboard/app.py` is no longer a supported product entrypoint. The supported
product surface is the React/FastAPI workstation covered by
`docs/agent-harness/r7-final-browser-proof.md`.

This retirement does not change:

- selection formulas or candidate identity;
- review scoring behavior;
- archive/history semantics;
- SQLite/DuckDB product storage;
- simulated trading remains out of scope.

## Default Behavior

Launching `streamlit run dashboard/app.py` now shows the R7 compatibility notice
and stops before importing chart components or reading legacy generated files.

Temporary rollback behavior:

```bash
STOCKTRADE_ALLOW_LEGACY_DASHBOARD=1 streamlit run dashboard/app.py
```

The rollback flag is intentionally explicit so future agents do not treat this
Streamlit surface as the primary product path.

## Required Proof

Before or with this retirement PR, agents must prove:

- `r7-final-browser-proof` exists for the React/FastAPI workstation;
- `r7-legacy-write-freeze` marks the legacy dashboard as compatibility-only;
- `dashboard/app.py` stops by default unless `STOCKTRADE_ALLOW_LEGACY_DASHBOARD=1`;
- the rollback flag remains documented;
- no business logic modules are changed by the retirement.

Expected validation:

```bash
PYTHONPATH=apps/api:src python3 -m unittest tests.test_dashboard_retirement_harness
scripts/harness/check.sh r7-dashboard-retirement
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

## Rollback

Rollback is to run the legacy dashboard with
`STOCKTRADE_ALLOW_LEGACY_DASHBOARD=1`, or remove the default stop guard in a
dedicated rollback PR. Do not delete or mutate legacy `data/` records during
rollback.
