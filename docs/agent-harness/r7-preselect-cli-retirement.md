# R7 Preselect CLI Retirement

Managing issue: #152
Status date: 2026-05-27

This document retires the legacy `pipeline.cli preselect` writer as a default
workflow. The supported product path is:

```text
POST /api/runs/preselect
```

## Decision

`python -m pipeline.cli preselect` is retired by default. It remains available
only behind:

```bash
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1
```

Default execution must stop before:

- loading market data;
- loading preselect config;
- running selection formulas;
- writing `data/candidates`;
- updating `candidates_latest.json`.

This retirement does not change:

- selection formulas or candidate identity;
- same-date multi-strategy merge semantics;
- product `/api/runs/preselect` behavior;
- chart export, review, archive, SQLite, or DuckDB behavior;
- simulated trading logic, which remains out of scope.

## Product Replacement Proof

The product replacement already has repository-backed evidence:

- `POST /api/runs/preselect` records run, step, candidate batch, and candidate
  rows in product-owned SQLite state.
- `docs/agent-harness/r7-resource-envelope.md` proves the credential-free
  product workflow covers preselect -> chart export -> provider review ->
  archive.
- `docs/agent-harness/r7-final-browser-proof.md` proves Run Center and
  Candidates are inspectable in the React workstation at desktop and mobile
  widths.
- `docs/agent-harness/r7-legacy-write-freeze.md` guards product code from
  directly reading legacy generated candidate files outside explicit migration
  and import paths.

## Validation

Expected command:

```bash
scripts/harness/check.sh r7-preselect-cli-retirement
```

The gate must prove:

- the legacy preselect CLI exits with code `2` by default;
- the product replacement and rollback environment variable are visible;
- no missing config, data directory, or candidate write error occurs before the
  retirement guard;
- the rollback flag is explicit and documented;
- simulated trading remains out of scope and is not changed by this retirement.

## Rollback

Rollback is to run:

```bash
STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1 python -m pipeline.cli preselect
```

Use this only for migration, parity, or incident recovery. Do not mutate
`data/trading` or delete legacy `data/` records as part of rollback.
