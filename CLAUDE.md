# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install frontend dependencies (needed once for ./start_product)
cd apps/web && npm install

# Launch product (FastAPI API + React/Vite web UI)
./start_product
# API: http://127.0.0.1:8000 / Web: http://127.0.0.1:5173

# Run tests
python -m pytest tests/ -xvs

# Run a single test file
python -m pytest tests/test_preselect_domain_contracts.py -xvs

# Type-check frontend
cd apps/web && npm run build   # tsc -b && vite build

# Lint frontend
cd apps/web && npm run lint    # eslint

# Harness validation (contract, docs, architecture checks)
scripts/harness/check.sh quick
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh contracts
scripts/harness/check.sh python
scripts/harness/check.sh docs
```

`PYTHONPATH` must include `apps/api` and `src` when running the API. `./start_product` and `scripts/harness/check.sh` set this automatically.

## Architecture

This is a semi-automated A-share stock selection tool: fetch daily K-line data from Tushare → run deterministic strategy filters → export candidate charts → AI review (Gemini CLI) → archive recommendations.

The project is mid-refactor (issue #23 / R7). **Two stacks coexist, and a module can only be in one at a time:**

### New stack (default product path)

- **`apps/web/`** — React/Vite/TypeScript frontend (React Query + React Router)
- **`apps/api/stocktrade_api/`** — FastAPI backend with routes, Pydantic schemas, and service layer
- **`src/stocktrade/`** — Pure domain logic: `domain/selection/` (strategy indicators, preselect, selectors), `domain/review/` (suggestions), `domain/archive/`, `domain/analytics/`
- **SQLite** (`var/db/app.sqlite`) — product state: runs, candidates, reviews, archive, settings, migration audit
- **DuckDB** (`var/db/analytics.duckdb`) — analytical queries
- **`var/artifacts/{run_id}/`** — per-run generated files (charts, logs)

### Legacy stack (gated, default off)

- **`pipeline/`** — fetch_kline, B1/Brick strategy selectors, preselect CLI, archive_results
- **`agent/`** — Gemini CLI review (reads chart images via `@file`), Gemini API review
- **`dashboard/`** — chart export via Plotly/Kaleido
- **`workbench/`** — Streamlit UI (retired from product path)
- **`paper_trading/`** — local simulated trading (outside current refactor scope)
- **`data/`** — file-based storage: `raw/`, `candidates/`, `kline/{date}/`, `review/{date}/`, `history/{date}/`

### Key invariants

- Candidate identity is `(code, strategy)`, never `code` alone.
- Legacy paths are locked behind `STOCKTRADE_ALLOW_LEGACY_*` env vars — new work should use the API/product path.
- Gemini CLI reads charts through `@file` references; Python writes result JSONs. The CLI must not directly mutate repo files.
- `data/`, `var/`, `.gemini_cli_tmp/`, `.antigravitycli/` are gitignored. Never commit market data, Gemini raw logs, credentials, or personal trading artifacts.
- Never commit `TUSHARE_TOKEN`, `GEMINI_API_KEY`, or Gemini OAuth files.
- Prefer small, reversible changes. Do not rewrite strategy logic unless the task explicitly targets strategy behavior.
- All changes must go through a GitHub issue + PR. See `docs/agent-harness/issue-pr-governance.md`.

### Test conventions

Tests live in `tests/` with pytest. Two flavors:
- **Harness tests** — gate the retirement of legacy modules (e.g., `test_*_retirement_harness.py`, `test_legacy_write_freeze_harness.py`)
- **Contract tests** — verify API/domain behavior (e.g., `test_preselect_domain_contracts.py`, `test_review_api_contracts.py`, `test_selection_indicator_contracts.py`)

Tests use `from stocktrade_api.main import create_app` and `fastapi.testclient.TestClient` for API tests.

### Agent harness docs

Most agent-facing operational rules are in `AGENTS.md` and `docs/agent-harness/`. Key files:
- `product-refactor-status.md` — current refactor phase and next issue queue
- `refactor-execution.md` — active phase name and execution rules
- `business-logic-spec.md` — parity rules between legacy and new stacks
- `refactor-preconditions.md` — blocking items before broad implementation
