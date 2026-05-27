# StockTradebyZ Agent Map

This repository is optimized for agent-assisted development. Keep this file
short: it is a map, not the full manual. Deeper rules live in versioned docs.

## Read First

- Product and CLI overview: [README.md](README.md)
- Architecture map: [ARCHITECTURE.md](ARCHITECTURE.md)
- Agent harness index: [docs/agent-harness/index.md](docs/agent-harness/index.md)
- Issue/PR governance:
  [docs/agent-harness/issue-pr-governance.md](docs/agent-harness/issue-pr-governance.md)
- Product refactor charter:
  [docs/agent-harness/product-refactor-charter.md](docs/agent-harness/product-refactor-charter.md)
- Product refactor status:
  [docs/agent-harness/product-refactor-status.md](docs/agent-harness/product-refactor-status.md)
- Business logic specification harness:
  [docs/agent-harness/business-logic-spec.md](docs/agent-harness/business-logic-spec.md)
- Refactor execution harness:
  [docs/agent-harness/refactor-execution.md](docs/agent-harness/refactor-execution.md)
- Refactor compatibility contracts:
  [docs/agent-harness/refactor-contracts.md](docs/agent-harness/refactor-contracts.md)
- Refactor preconditions:
  [docs/agent-harness/refactor-preconditions.md](docs/agent-harness/refactor-preconditions.md)
- UI/UX quality bar:
  [docs/agent-harness/uiux-quality-bar.md](docs/agent-harness/uiux-quality-bar.md)
- Architecture quality bar:
  [docs/agent-harness/architecture-quality-bar.md](docs/agent-harness/architecture-quality-bar.md)
- Target architecture design:
  [docs/agent-harness/target-architecture-design.md](docs/agent-harness/target-architecture-design.md)
- R7 hardening and retirement plan:
  [docs/agent-harness/r7-hardening-retirement-plan.md](docs/agent-harness/r7-hardening-retirement-plan.md)
- R7 chart export retirement:
  [docs/agent-harness/r7-chart-export-retirement.md](docs/agent-harness/r7-chart-export-retirement.md)
- R7 preselect CLI retirement:
  [docs/agent-harness/r7-preselect-cli-retirement.md](docs/agent-harness/r7-preselect-cli-retirement.md)
- R7 product launcher:
  [docs/agent-harness/r7-product-launcher.md](docs/agent-harness/r7-product-launcher.md)
- R7 workbench retirement:
  [docs/agent-harness/r7-workbench-retirement.md](docs/agent-harness/r7-workbench-retirement.md)
- R7 runtime terminal integrity:
  [docs/agent-harness/r7-runtime-terminal-integrity.md](docs/agent-harness/r7-runtime-terminal-integrity.md)
- Validation gates: [docs/agent-harness/validation-gates.md](docs/agent-harness/validation-gates.md)
- Core workflows: [docs/agent-harness/workflows.md](docs/agent-harness/workflows.md)

## Project Shape

- `pipeline/`: market data loading, strategy selection, candidate contracts.
- `agent/`: Gemini API/CLI review pipeline and review result contracts.
- `dashboard/`: chart rendering and chart export helpers.
- `workbench/`: Streamlit local product shell and background process launcher.
- `paper_trading/`: legacy local simulated trading account, currently outside
  the product-level refactor scope.
- `config/`: default YAML configuration.
- `docs/`: product designs, strategy specs, and agent-facing knowledge.
- `tests/`: fast contract and strategy tests.

## Operating Rules

- Prefer small, reversible changes. Do not rewrite strategy logic unless the task
  explicitly targets strategy behavior.
- Treat `data/` as generated local state. Do not commit real market snapshots,
  Gemini raw logs, credentials, account state, or personal trading artifacts.
- Never commit `TUSHARE_TOKEN`, `GEMINI_API_KEY`, Gemini OAuth files, or terminal
  logs that could contain secrets.
- Preserve the same-day multi-strategy contract: `(code, strategy)` is the
  identity for candidates, reviews, and history rows. Legacy simulated trading
  still uses this identity, but it is not part of the product-level refactor.
- Keep Gemini CLI review retry, checkpoint, raw-log, and `skip_existing`
  semantics intact unless a task is specifically about that path.
- Workbench changes must preserve background task visibility and cancellation
  semantics.
- For product-level refactors, preserve business behavior while rewriting the
  implementation in the confirmed new stack. The legacy system is the behavior
  oracle, not the target architecture.
- Do not include simulated trading or paper-trading UI/data flows in the
  product-level refactor unless the user explicitly reopens that scope.
- Every task must be managed by a GitHub issue and every repository change must
  land through a PR. Follow
  `docs/agent-harness/issue-pr-governance.md`.
- Name the active phase from `docs/agent-harness/refactor-execution.md`, use
  `docs/agent-harness/product-refactor-status.md` for current progress and
  next issue selection, use `docs/agent-harness/business-logic-spec.md` for
  parity rules, and confirm blocking items in
  `docs/agent-harness/refactor-preconditions.md` before broad implementation.
- Do not remove file-based storage, Streamlit/workbench paths, or legacy CLI
  handoffs until migration evidence and rollback notes exist.

## Before Editing

1. Read the relevant docs listed above.
2. Inspect current git status; this repo often has generated or local data.
3. Identify the smallest meaningful validation gate before making changes.
4. If a task touches file contracts under `data/`, read the writer and reader.

## Validation

Use the harness entrypoint:

```bash
scripts/harness/check.sh quick
```

For a refactor plan or implementation slice:

```bash
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh r7-dashboard-retirement
scripts/harness/check.sh r7-browser-proof
scripts/harness/check.sh r7-gemini-api-review-retirement
scripts/harness/check.sh r7-legacy-write-freeze
scripts/harness/check.sh r7-preselect-cli-retirement
scripts/harness/check.sh r7-chart-export-retirement
scripts/harness/check.sh r7-product-launcher
scripts/harness/check.sh r7-workbench-retirement
scripts/harness/check.sh r7-runtime-terminal-integrity
scripts/harness/check.sh r7-resource-envelope
scripts/harness/check.sh r7-runtime-recovery
scripts/harness/check.sh quick
```

For narrower checks:

```bash
scripts/harness/check.sh docs
scripts/harness/check.sh contracts
scripts/harness/check.sh python
```

If optional runtime data or credentials are missing, report that honestly and
run the narrow non-runtime checks instead.
