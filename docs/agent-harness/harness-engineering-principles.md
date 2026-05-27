# Harness Engineering Principles for StockTradebyZ

## 1. Human Sets Direction, Agent Executes

Humans decide product boundaries, trading risk posture, and whether a workflow
should exist. Agents should turn those decisions into small, reviewable patches,
tests, docs, and validation evidence.

The current product-level refactor boundaries are fixed unless the user changes
them: Python domain logic, React/Vite/TypeScript frontend, FastAPI backend,
SQLite product state, DuckDB analytical data, and no simulated trading rewrite.
Within those boundaries, agents own routine implementation decisions and should
record durable choices in issues, PRs, docs, tests, or harness checks.

For this repo, humans should explicitly decide before agents:

- change strategy semantics;
- change Gemini scoring prompts or thresholds;
- change simulated trading rules, which are outside the product-level refactor
  unless explicitly reopened;
- add production dependencies outside the confirmed stack;
- migrate storage contracts.

## 2. Repository Is the Record System

If a product rule matters, it should live in this repo. Chat history and local
intuition are not reliable inputs for future agents.

Durable rules belong in:

- `README.md`: user-facing setup and operation.
- `ARCHITECTURE.md`: high-level domain and storage map.
- `docs/*`: product, strategy, and workflow design.
- `docs/agent-harness/*`: agent operating rules.
- `scripts/harness/*`: executable checks.
- `tests/*`: behavior regressions.

## 3. Agent Readability Beats Cleverness

Agents must be able to infer behavior from local files. Prefer explicit
contracts, stable filenames, typed structures, and small helper functions.

Avoid:

- hidden state outside the repo;
- one-off shell snippets that are not documented;
- broad UI functions that also mutate business state;
- ad hoc parsing when structured JSON/YAML is available;
- unbounded scans through local `data/` during normal checks.

## 4. Constraints Should Be Mechanical

The harness should enforce important invariants rather than relying on long
prompts. Current mechanical gates include:

- `docs`: docs links and required harness docs exist.
- `contracts`: key repo contracts are still discoverable in source.
- `python`: compile source files and run lightweight tests when dependencies
  are available.
- `quick`: docs + contracts + python.

Future gates should cover:

- candidate JSON schema fixtures;
- review JSON schema fixtures;
- workbench run-state fixtures;
- legacy paper trading plan/fill state transitions, only when that
  out-of-scope module is explicitly touched;
- UI smoke checks for future product frontend.

## 5. Observability Is a Product Surface

Agents need readable state to debug local workflows. Existing useful surfaces:

- `data/runs/{run_id}/run_state.json`
- `data/runs/{run_id}/run.log`
- `data/review/{pick_date}/gemini_cli_review_checkpoint.json`
- `data/review/{pick_date}/gemini_cli_runs/*/meta.json`
- `data/history/index.json`
- `data/trading/equity_curve.csv` for legacy simulated trading only

New runtimes should expose machine-readable status before visual polish.

## 6. Garbage Collection Is Continuous

Agent-generated code can copy old weaknesses. Treat repeated drift as harness
debt. Good cleanup tasks are small and mechanical:

- split files that exceed their ownership boundary;
- move durable decisions from chat into docs;
- convert repeated review comments into lints or tests;
- prune stale docs or mark them superseded;
- keep validation commands current.

## 7. Local Autonomy Has Guardrails

Agents may run local read-only checks and lightweight tests. Agents must not:

- call real trading APIs;
- transmit secrets or market artifacts to external services except through the
  explicitly configured Gemini review path;
- delete local `data/` artifacts unless the user asks;
- overwrite candidate/review/history contracts without preserving migration
  and rollback evidence.
