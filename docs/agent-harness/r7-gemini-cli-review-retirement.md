# R7 Gemini CLI Review Retirement

Managing issue: #152
Status date: 2026-05-27

This document retires `agent/gemini_cli_review.py` as a default executable
legacy writer. The supported product review path is:

```text
POST /api/runs/review/provider
provider=gemini-cli
```

## Decision

`python agent/gemini_cli_review.py` is retired by default. It remains available
only behind:

```bash
STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1
```

Default execution must stop before:

- loading `config/gemini_cli_review.yaml` or a custom legacy config;
- reading `data/candidates/candidates_latest.json`;
- reading chart files under `data/kline`;
- writing `data/review/{pick_date}`;
- writing `suggestion.json`;
- creating or updating `data/review/{pick_date}/gemini_cli_review_checkpoint.json`.

This retirement does not change:

- product `POST /api/runs/review/provider` behavior;
- Gemini CLI model, command, output-format, or `@file` image-reference
  semantics in the product provider;
- retry/backoff, jitter, batch fallback, `skip_existing`, result cache, raw
  logs, checkpoint, usage, or provider evidence artifact behavior;
- review schema, recommendation semantics, chart export, archive, SQLite, or
  DuckDB behavior;
- simulated trading logic, which remains out of scope.

The legacy module's pure helpers and `GeminiCliReviewer` class stay importable
for parity tests and rollback evidence.

## Product Replacement Proof

The product replacement already has repository-backed evidence:

- `apps/api/stocktrade_api/services/gemini_cli_provider.py` executes
  `provider=gemini-cli` review requests from product candidate batches and
  product chart artifacts.
- `tests/test_gemini_cli_provider_contracts.py` proves retry on rate limits,
  checkpoint writes, raw prompt/stdout/stderr evidence, usage tracking,
  result-cache `skip_existing`, and batch order validation without a live
  Gemini call.
- `tests/test_product_workflow_storage_contracts.py` proves the credential-free
  product workflow can run preselect -> chart export -> Gemini CLI provider
  review -> archive while storing provider evidence as product artifacts.
- `docs/agent-harness/r7-resource-envelope.md` and
  `docs/agent-harness/r7-final-browser-proof.md` prove the product workflow and
  UI can inspect provider/archive evidence without legacy generated files.

## Validation

Expected command:

```bash
scripts/harness/check.sh r7-gemini-cli-review-retirement
```

The gate must prove:

- the legacy Gemini CLI review script exits with code `2` by default;
- the product replacement and rollback environment variable are visible;
- no missing config, candidate, chart, or review write error occurs before the
  retirement guard;
- the legacy helper class remains importable for parity tests;
- product provider evidence still covers retry, checkpoint, raw logs,
  `skip_existing`, result cache, and usage;
- simulated trading remains out of scope and is not changed by this retirement.

## Rollback

Rollback is to run:

```bash
STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1 python agent/gemini_cli_review.py --config config/gemini_cli_review.yaml
```

If using the retired Streamlit workbench to drive a full legacy flow, both
`STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1` and
`STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1` may be required. Use rollback
only for migration, parity, or incident recovery. Do not mutate `data/trading`
or delete legacy `data/` records as part of rollback.
