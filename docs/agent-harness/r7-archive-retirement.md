# R7 Archive Writer Retirement

Managing issue: #152
Status date: 2026-05-27

This document retires the legacy `pipeline.archive_results` writer as a
default workflow. The supported product path is:

```text
POST /api/runs/archive
```

## Decision

`python -m pipeline.archive_results` is retired by default. It remains
available only behind:

```bash
STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1
```

Default execution must stop before:

- reading `data/candidates/candidates_latest.json`;
- reading `data/review/{pick_date}/suggestion.json`;
- reading `data/kline/{pick_date}`;
- writing `data/history/{pick_date}`;
- updating `data/history/index.json`.

This retirement does not change:

- archive row ordering or status semantics;
- same-day multi-strategy archive identity;
- product `/api/runs/archive` behavior;
- candidate, review, chart export, SQLite, or DuckDB behavior;
- simulated trading logic, which remains out of scope.

Pure helper functions such as `build_rows`, `build_summary`, and
`review_matches_strategy` stay importable for parity tests and migration
evidence.

## Product Replacement Proof

The product replacement already has repository-backed evidence:

- `POST /api/runs/archive` records archive snapshots and rows in product-owned
  SQLite state and writes archive facts to DuckDB.
- `docs/agent-harness/r7-resource-envelope.md` proves the credential-free
  product workflow covers preselect -> chart export -> provider review ->
  archive.
- `docs/agent-harness/r7-final-browser-proof.md` proves Archive route evidence
  and chart artifacts are inspectable in the React workstation at desktop and
  mobile widths.
- `docs/agent-harness/r7-legacy-write-freeze.md` guards product code from
  directly reading legacy generated history files outside explicit migration
  and import paths.

## Validation

Expected command:

```bash
scripts/harness/check.sh r7-archive-retirement
```

The gate must prove:

- the legacy archive writer exits with code `2` by default;
- the product replacement and rollback environment variable are visible;
- no missing candidate, review, chart, or history write error occurs before the
  retirement guard;
- helper functions remain importable for parity tests;
- simulated trading remains out of scope and is not changed by this retirement.

## Rollback

Rollback is to run:

```bash
STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1 python -m pipeline.archive_results
```

Use this only for migration, parity, or incident recovery. Do not mutate
`data/trading` or delete legacy `data/` records as part of rollback.
