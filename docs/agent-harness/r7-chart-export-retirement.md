# R7 Chart Export Retirement

Managing issue: #152
Parent epic: #23
Status date: 2026-05-27

This document retires the legacy `dashboard/export_kline_charts.py` writer as a
default workflow. The implementation remains in the repository only as a
rollback and parity oracle. The supported product path is:

```text
POST /api/runs/chart-export
```

## Decision

- `dashboard/export_kline_charts.py` must exit before reading
  `data/candidates`, `candidates_latest.json`, `data/raw`, or writing
  `data/kline` unless the rollback override is set.
- The override is explicit and narrow:
  `STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1`.
- The script must print `R7 legacy retirement` and the product replacement path
  when the override is missing.
- Product code must keep using SQLite/DuckDB state and product artifacts instead
  of legacy generated chart files.
- Simulated trading remains out of scope.

## Product Replacement Proof

The product chart workflow already has repository-backed evidence:

- FastAPI exposes the chart export run surface through `POST
  /api/runs/chart-export`.
- Product chart export writes run-scoped artifacts, including compatibility
  `code_day.jpg` and strategy-scoped `code_strategy_day.jpg` files.
- `docs/agent-harness/r7-resource-envelope.md` proves the credential-free
  product workflow covers preselect -> chart export -> provider review ->
  archive.
- `docs/agent-harness/r7-final-browser-proof.md` proves chart artifacts are
  inspectable in the React workstation at desktop and mobile widths.
- `scripts/harness/check.sh r7-legacy-write-freeze` guards product source from
  directly reading legacy generated paths.

## Rollback

Rollback is allowed only for parity or incident recovery:

```bash
STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1 python dashboard/export_kline_charts.py
```

Before using the rollback path, capture the product run id and the chart export
run evidence that failed. After rollback, keep generated `data/kline` files as
local evidence only. Do not import them back into product-owned state unless a
separate migration or restore PR proves the mapping.

## Validation

Expected command:

```bash
scripts/harness/check.sh r7-chart-export-retirement
```

The gate must prove:

- the legacy exporter exits with code `2` by default;
- the product replacement and rollback environment variable are visible;
- no legacy candidate file is read before the retirement gate;
- the retirement remains separate from simulated or paper trading.
