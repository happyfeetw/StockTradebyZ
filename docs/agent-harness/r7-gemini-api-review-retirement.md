# R7 Gemini API Review Retirement

Managing issue: #152
Status date: 2026-05-27

This document retires `agent/gemini_review.py` as a default executable legacy
writer. The supported product review path is `POST /api/runs/review/provider`,
which writes product-owned SQLite review rows, DuckDB facts, and provider
evidence artifacts.

## Decision

`agent/gemini_review.py` is the old API-key Gemini review script that writes
directly to `data/review`. In R7 it is disabled by default. It can be
temporarily re-enabled only with:

```bash
STOCKTRADE_ALLOW_LEGACY_GEMINI_API_REVIEW=1 python agent/gemini_review.py
```

Default execution exits before loading config, constructing `GeminiReviewer`,
or writing review files. The old implementation remains in place as migration,
parity, and rollback evidence.

This retirement does not change:

- review result schema or recommendation semantics;
- Gemini CLI provider behavior;
- product `/api/runs/review/provider` behavior;
- selection, chart export, archive, SQLite, or DuckDB behavior;
- simulated trading, which remains out of scope.

## Required Proof

The retirement PR must prove:

- `agent/gemini_review.py` defaults to a stopped retirement state;
- the rollback flag is explicit and documented;
- the product provider review path already has fixture-backed evidence;
- no live Gemini call is required for validation;
- no business logic module is changed.

Expected validation:

```bash
PYTHONPATH=apps/api:src python3 -m unittest tests.test_gemini_api_retirement_harness
scripts/harness/check.sh r7-gemini-api-review-retirement
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

## Rollback

Rollback is to set `STOCKTRADE_ALLOW_LEGACY_GEMINI_API_REVIEW=1` for a
temporary local run, or remove the default stop guard in a dedicated rollback
PR. Do not delete or mutate legacy `data/review` files as part of rollback.
