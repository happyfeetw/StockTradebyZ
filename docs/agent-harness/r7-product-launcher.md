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
- React/Vite web app on `127.0.0.1:5173` by default, launched through the
  local Vite CLI under `apps/web/node_modules`;
- the Vite dev proxy reads `STOCKTRADE_API_HOST/STOCKTRADE_API_PORT`, so
  `/api` follows launcher API port overrides instead of hard-coding `8000`;
- `PYTHONPATH=apps/api:src` so the API imports product modules without relying
  on legacy CLI paths.
- Node.js 22.x is enforced before Vite starts. Run `nvm use` when the launcher
  reports an unsupported Node.js version. Runtime launch does not depend on
  `npm run`; `npm install` is still required once to populate `node_modules`.
- SQLite Alembic migrations run during FastAPI app creation for file-backed
  product databases, so a clean `var/db/app.sqlite` starts with the product
  schema in place.

Optional local overrides:

- `PYTHON_BIN`
- `STOCKTRADE_API_HOST`
- `STOCKTRADE_API_PORT`
- `STOCKTRADE_WEB_HOST`
- `STOCKTRADE_WEB_PORT`

## Legacy Workbench Relationship

`start_workbench` is retired by default while R7 retires Streamlit/workbench
surfaces. It must point users to `./start_product` for supported React/FastAPI
workflows and may run only with the explicit
`STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1` rollback flag.

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
- the Vite proxy follows launcher API host/port overrides;
- the launcher rejects unsupported Node.js versions before invoking Vite;
- the FastAPI app can serve product state APIs against a clean SQLite path;
- `start_workbench` points to `./start_product` as the replacement;
- simulated trading remains out of scope.

## Rollback

Rollback is to use `STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1 ./start_workbench` only
while the product launcher is fixed. Do not delete or mutate legacy `data/`
records as part of launcher rollback.
