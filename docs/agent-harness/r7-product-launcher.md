# R7 Product Launcher

Managing issue: #152
Status date: 2026-05-27

This document defines the default local launch path for the rebuilt
React/FastAPI product. It is the replacement launch proof required before
`start_workbench` can be retired in a later PR.

## Default Product Entry

Use:

```bash
./start_product
```

The launcher starts:

- FastAPI app: `stocktrade_api.main:app` on `127.0.0.1:8000` by default;
- React/Vite web app on `127.0.0.1:5173` by default;
- `PYTHONPATH=apps/api:src` so the API imports product modules without relying
  on legacy CLI paths.

Optional local overrides:

- `PYTHON_BIN`
- `STOCKTRADE_API_HOST`
- `STOCKTRADE_API_PORT`
- `STOCKTRADE_WEB_HOST`
- `STOCKTRADE_WEB_PORT`

## Legacy Workbench Relationship

`start_workbench` remains available only as a compatibility launcher while R7
retires Streamlit/workbench surfaces. It must point users to `./start_product`
for supported React/FastAPI workflows.

This launcher does not change:

- selection formulas or candidate identity;
- review scoring behavior;
- archive/history semantics;
- SQLite/DuckDB ownership;
- simulated trading, which remains out of scope.

## Validation

Run:

```bash
python3 -m py_compile scripts/harness/check.py tests/test_product_launcher_harness.py
bash -n start_product start_workbench
PYTHONPATH=apps/api:src python3 -m unittest tests.test_product_launcher_harness
scripts/harness/check.sh r7-product-launcher
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

The `r7-product-launcher` gate checks that:

- `start_product` exists and is executable;
- the launcher starts the FastAPI and Vite dev servers with local host/port
  defaults;
- `start_workbench` points to `./start_product` as the replacement;
- simulated trading remains out of scope.

## Rollback

Rollback is to keep using `start_workbench` as the compatibility path while the
product launcher is fixed. Do not delete or mutate legacy `data/` records as
part of launcher rollback.
