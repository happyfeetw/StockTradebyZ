# StockTradebyZ Architecture Map

This file is the high-level map for agents. Keep implementation details in the
module docs and source files; keep durable cross-cutting decisions here.

## Product Boundary

StockTradebyZ is a local, personal A-share research workflow:

1. Fetch local daily K-line data from Tushare.
2. Run deterministic strategy filters.
3. Export candidate charts.
4. Ask Gemini to review chart images.
5. Archive final recommendations.
6. Optionally simulate low-frequency paper trading from archived signals.

It is not a broker integration, live trading terminal, high-frequency engine, or
multi-user SaaS product.

## Domain Modules

### `pipeline/`

Owns market data ingestion, strategy filtering, candidate identity, and
candidate JSON persistence.

Important contracts:

- Candidate identity is `(code, strategy)`, not just `code`.
- `pipeline/pipeline_io.py` writes dated candidate files plus
  `candidates_latest.json`.
- Workbench preselect runs should use `--merge-same-date` when multiple
  strategies can be run on the same pick date.

### `dashboard/`

Owns chart preparation and export. Review agents consume exported chart images,
so chart output changes are review-contract changes.

### `agent/`

Owns AI review execution and review JSON normalization.

Important contracts:

- Gemini CLI reads chart images through `@file` references.
- Python writes result JSON files; the Gemini CLI must not directly mutate repo
  files.
- Batch review must validate output length and stock order.
- Retry/backoff/checkpoint behavior is part of the product reliability surface.

### `workbench/`

Owns the local browser UI, run configuration snapshots, background process
launching, log display, and result browsing.

Current implementation is Streamlit. It is useful as an MVP shell, but the agent
harness treats it as a product surface with observable states, not as a place to
hide business logic.

### `paper_trading/`

Owns simulated account, positions, plans, fills, snapshots, and equity curve.

Important contract:

- Simulated trading is local-only and never places real orders.
- Plan status changes and fills should be auditable.
- Position identity follows `(code, strategy)`.

## Current Storage Model

The repo currently uses local files as the source of truth:

- `data/raw/*.csv`: daily market data.
- `data/candidates/*.json`: strategy candidates.
- `data/kline/{date}/*_day.jpg`: exported charts.
- `data/review/{date}/*.json`: Gemini review results and raw logs.
- `data/history/{date}/*.json`: archived daily results.
- `data/trading/`: paper trading account files.
- `data/runs/{run_id}/`: run snapshots, logs, and state.

Agents should assume these directories may be absent in a clean clone and may be
large in a local working copy.

## Agent-Friendly Target Shape

Future productization should move toward explicit layers:

```text
Types -> Config -> Repo -> Service -> Runtime -> UI
```

For the current Python codebase, interpret that as:

- Types: dataclasses, Pydantic models, or documented JSON shapes.
- Config: YAML parsing and defaults.
- Repo: file or database access.
- Service: strategy/review/trading use cases.
- Runtime: subprocesses, queues, timers, retries, external CLIs.
- UI: Streamlit pages or future frontend/API adapters.

New code should keep business decisions out of UI functions and subprocess
wrappers wherever practical.
