# R7 Workbench Retirement

Managing issue: #152
Status date: 2026-05-27

This document retires the legacy Streamlit workbench and launcher as default
product entrypoints. The supported product path is:

```bash
./start_product
```

## Decision

`start_workbench`, `workbench/app.py`, and `workbench/runner.py` are retired by
default. They remain available only behind:

```bash
STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1
```

Default execution must stop before:

- reading shell-sourced Tushare or Gemini credentials;
- launching Streamlit;
- importing legacy chart components;
- importing `paper_trading.core`;
- reading `data/runs` run configs or writing legacy workflow output.

This retirement does not change:

- selection formulas or candidate identity;
- review scoring behavior;
- chart export, archive, SQLite, or DuckDB behavior;
- product React/FastAPI runtime behavior;
- simulated trading logic, which remains out of scope.

## Product Replacement Proof

The product replacement is covered by existing R7 evidence:

- `docs/agent-harness/r7-product-launcher.md` defines `./start_product` as the
  default local React/FastAPI launcher.
- `docs/agent-harness/r7-final-browser-proof.md` covers the primary product
  workstation routes at desktop and mobile widths.
- `docs/agent-harness/r7-runtime-recovery.md` covers product runtime recovery
  and local in-process concurrency.
- `docs/agent-harness/r7-legacy-write-freeze.md` prevents product code from
  depending on legacy generated files outside explicit migration/import paths.

## Validation

Expected command:

```bash
scripts/harness/check.sh r7-workbench-retirement
```

The gate must prove:

- `start_workbench` exits with code `2` by default before token lookup or
  Streamlit launch;
- `workbench/app.py` stops before loading legacy workbench dependencies;
- `workbench/runner.py` exits before reading `run_config.json`;
- the rollback flag is explicit and documented;
- simulated trading remains out of scope and is not changed by this retirement.

## Rollback

Rollback is to run:

```bash
STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1 ./start_workbench
```

Use this only for migration, parity, or incident recovery. Do not mutate
`data/trading` or delete legacy `data/` records as part of rollback.
