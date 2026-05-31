# Product Refactor Status

Managing issue: #152
Parent epic: #23
Last status sync: 2026-05-28
Baseline commit: `28dc70a`

This file is the final status map for the confirmed full product-level
refactor. It records the React/FastAPI/SQLite/DuckDB rewrite as complete for
the primary trading research workstation scope, while preserving explicit
non-goals and retained compatibility surfaces.

## Current Phase Position

The refactor is complete for the confirmed scope. R0 through R7 have
repo-backed evidence, product-owned storage and workflows are in place, strict
business behavior is protected by parity tests, and legacy surfaces are retired
by default or explicitly retained as compatibility-only rollback/oracle paths.

Default next phase focus: none for the product-level refactor. Future work
should open a new issue when it changes product scope, deletes retained legacy
surfaces, reopens paper trading, or extends beyond local-first operation.

Rationale: #112 has moved strategy selection behavior behind product-owned
domain helpers, ports, and parity tests. #113 added product settings read/write
contracts, strategy metadata, and DuckDB-backed strategy summary contracts. #114
now gives the React workstation deterministic fixture-backed browser evidence
for the core R5 surfaces. #115 now defines R6 ownership, rollback, artifact
backup/restore, provider evidence indexing, and a fixture-backed product API
write proof across preselect, chart export, provider review, and archive.
#152 owns the completed R7 plan, validation gate, resource evidence, product
launcher, runtime hardening, runtime recovery, final browser proof, Gemini API
reviewer retirement, run_all retirement, selector adapter retirement, legacy
retirement sequence, and final retirement proof audit gate.

## Phase Status

| Phase | Current Status | Evidence | Remaining Work |
| --- | --- | --- | --- |
| R0 Product charter and decision freeze | Baseline complete | #24, `product-refactor-charter.md`, `refactor-preconditions.md`, readiness gate | Keep decisions current when scope changes |
| R1 Business logic specification | Complete for confirmed strict-parity areas | #26, `tests/fixtures/golden_master/`, `tests/test_golden_master_contracts.py` | Add new parity fixtures only when future issues add or change business scope |
| R2 Target architecture and data model | Complete for confirmed stack | #29, `target-architecture-design.md`, SQLite/DuckDB migrations | Keep architecture docs current only for future scoped changes |
| R3 Core domain rewrite | Complete for in-scope primary workflows | #100 moved review suggestion logic into `src/stocktrade/domain/review/`; #112 split preselect into named ports with product-owned orchestration, CSV market loading, base market preparation, trading-date fallback, top-turnover pool construction, strategy dispatch, warmup bars, B1 pick mask, B2 pick mask and quality score, KDJ, KDJ quantile mask, ZX-line, ZXDQ ratio, ZX condition mask, weekly MA bull, max-volume filter, B2 price-action metrics, B2 volume confirmation, recent-B1 lookup, brick chart core, brick pattern, and brick pick mask parity coverage; #152 selector adapter retirement moves default product formula execution to product-owned B1/B2/brick selector classes | Legacy selector classes remain compatibility/oracle evidence; permanent deletion requires a future destructive-cleanup issue |
| R4 Backend runtime and APIs | Complete for primary research workstation APIs | #31, #33, #35, #37, #43, #44, #49, #55, #79, #102, #103, #105, #107, #108, #110, #113 settings read/write, strategy metadata, analytics summary contracts, and R7 runtime recovery/terminal integrity evidence | Future backend hardening should be scoped to new product issues |
| R5 Frontend product UI/UX | Complete for primary workflow UI | #41, #46, #52, #58, #61, #91, #94, #98, #104, #109, #114 Overview/Analytics/Settings shell consuming #113 contracts plus candidate/review/archive evidence route, archive chart-inspection refinement, result-list dense-table refinement, deterministic R5 UI smoke fixture, desktop/mobile browser screenshots, no-overflow checks, keyboard spot checks, chart artifact rendering in `r5-ui-browser-smoke.md`, and R7 final browser proof | Future polish should be filed as new UI enhancement issues |
| R6 Data migration and storage cutover | Complete for in-scope state ownership | #39, #64, #66, #70, #75, #77, #81, #83, #85, #87, #89, #96, #115 `r6-storage-cutover-plan.md`, artifact backup/restore contract, provider evidence artifact indexing contracts, and `test_product_workflow_storage_contracts.py` product API chain proof | Legacy `data/` remains migration/rollback source material |
| R7 Hardening and legacy retirement | Complete | #152, `r7-hardening-retirement-plan.md`, `r7-resource-envelope.md`, `r7-final-browser-proof.md`, `r7-final-retirement-proof.md`, `r7-legacy-write-freeze.md`, `r7-gemini-api-review-retirement.md`, `r7-gemini-cli-review-retirement.md`, `r7-dashboard-retirement.md`, `r7-chart-export-retirement.md`, `r7-archive-retirement.md`, `r7-preselect-cli-retirement.md`, `r7-product-launcher.md`, `r7-run-all-retirement.md`, `r7-selector-adapter-retirement.md`, `r7-workbench-retirement.md`, `r7-runtime-terminal-integrity.md`, `r7-runtime-recovery.md`, `scripts/harness/check.sh r7-retirement-plan`, `scripts/harness/check.sh r7-final-retirement-proof`, `scripts/harness/check.sh r7-resource-envelope`, `scripts/harness/check.sh r7-browser-proof`, `scripts/harness/check.sh r7-legacy-write-freeze`, `scripts/harness/check.sh r7-gemini-api-review-retirement`, `scripts/harness/check.sh r7-gemini-cli-review-retirement`, `scripts/harness/check.sh r7-dashboard-retirement`, `scripts/harness/check.sh r7-chart-export-retirement`, `scripts/harness/check.sh r7-archive-retirement`, `scripts/harness/check.sh r7-preselect-cli-retirement`, `scripts/harness/check.sh r7-product-launcher`, `scripts/harness/check.sh r7-run-all-retirement`, `scripts/harness/check.sh r7-selector-adapter-retirement`, `scripts/harness/check.sh r7-workbench-retirement`, `scripts/harness/check.sh r7-runtime-terminal-integrity`, `scripts/harness/check.sh r7-runtime-recovery`, and runtime cancellation/recovery contract coverage define scope, browser/resource/freeze/reviewer/dashboard/archive/preselect-cli/launcher/run-all/selector-adapter/workbench/recovery guardrails, validation, rollback, phase order, and final audit requirements | None for confirmed product-refactor scope |

## Implemented Product Stack Slices

These areas are implemented enough to be used as current-state architecture
evidence:

- FastAPI app factory and route grouping under `apps/api/stocktrade_api/`.
- SQLite product-state models and Alembic migrations for runs, jobs,
  artifacts, candidate batches, reviews, archive rows, migration audit,
  backup/restore metadata, and chart export run kind.
- DuckDB migration runner and analytics fact writes for candidates, reviews,
  archive rows, and strategy run metrics.
- Job runtime for diagnostic, preselect, review, provider review, archive, and
  chart export workflows.
- Legacy import dry-run/import/verify paths for candidates, reviews, history,
  chart artifact references, and quarantine/audit reporting.
- Product-owned artifact service for generated or imported artifacts.
- Backup and restore APIs for local SQLite/DuckDB/artifact state.
- React/Vite app shell with runs, candidates, reviews, archive, and migrations
  views wired to typed API calls.
- React workstation IA now includes Overview, Analytics, and Settings routes
  wired to typed settings, strategy metadata, and DuckDB strategy-summary
  contracts.
- React workstation evidence flow now surfaces candidate batch readiness across
  candidate, chart export, review, and archive steps, and candidate/review/archive
  detail panels include deep links across the product evidence chain.
- Archive detail now promotes chart evidence inspection near the top of the
  selected row, with distinct states for product-owned chart artifacts, legacy
  chart references, and missing chart evidence.
- Candidate, review, and archive result lists now use desktop scan headers,
  selected/filter summary chips, and a primary comparison row plus secondary
  lineage strip for long run, batch, review-key, and chart-path evidence.
- A deterministic R5 UI browser-smoke fixture seeds SQLite, DuckDB, and product
  artifacts under ignored `var/ui-smoke/` state so Overview, Run Center,
  Candidates, Reviews, Archive, Analytics, Settings, and Migrations can be
  inspected without credentials.
- Product review provider boundary plus Gemini CLI provider adapter preserving
  checkpoint, retry/backoff, raw logs, skip cache, batch order validation, and
  chart lineage.
- Product chart export workflow that creates both compatibility `code_day.jpg`
  chart artifacts and strategy-scoped `code_strategy_day.jpg` artifacts.
- Product backend contracts for safe local settings metadata, SQLite-backed
  product preferences, strategy metadata, and DuckDB-backed strategy summary
  analytics are exposed through FastAPI for the future React workstation.
- R6 storage cutover now has a repo-backed source-of-truth ownership matrix and
  implementation sequence. Candidate, review, archive, chart artifact, provider
  evidence, migration, backup/restore, and rollback decisions are documented in
  `r6-storage-cutover-plan.md`.
- Product backup/restore copies SQLite, DuckDB, and product artifact files,
  writes `artifacts_manifest.json`, restores the configured artifact root, and
  has an API contract proving restored SQLite artifact rows still serve files
  through `/api/artifacts/{artifact_id}`.
- Product provider review runs index Gemini/provider evidence files as
  run-scoped SQLite `artifacts` rows, serve copied evidence through the artifact
  API, and preserve provider evidence artifact ids in review payload lineage.
- Fixture-backed product API chain proof covers preselect, chart export,
  provider review, and archive writes through SQLite, DuckDB, and product
  artifact storage without live Tushare/Gemini calls.
- R7 resource envelope evidence now runs the same credential-free product API
  path through `scripts/harness/resource_envelope.py`, recording startup time,
  workflow runtime, memory, SQLite/DuckDB growth, and artifact growth under
  `scripts/harness/check.sh r7-resource-envelope`.
- R7 final browser proof now covers Overview, Run Center, Candidates, Reviews,
  Archive, Analytics, Settings, and Migrations at desktop and mobile widths
  using deterministic UI smoke data, no-overflow checks, console checks, API
  200 logs, and chart artifact inspection.
- R7 legacy write freeze now marks legacy file-system writers and Streamlit
  surfaces as compatibility-only and adds a product no-read guard for legacy
  generated paths outside the explicit migration/import service.
- R7 Gemini API reviewer retirement now stops `agent/gemini_review.py` by
  default before loading legacy review config or writing `data/review`; rollback
  requires `STOCKTRADE_ALLOW_LEGACY_GEMINI_API_REVIEW=1`.
- R7 Gemini CLI reviewer retirement now stops `agent/gemini_cli_review.py` by
  default before loading legacy review config, reading legacy candidates/charts,
  writing `data/review`, or updating `gemini_cli_review_checkpoint.json`;
  rollback requires `STOCKTRADE_ALLOW_LEGACY_GEMINI_CLI_REVIEW=1`. Product
  `provider=gemini-cli` review keeps retry, checkpoint, raw evidence,
  `skip_existing`, result-cache, and usage semantics.
- R7 chart export retirement now disables `dashboard/export_kline_charts.py` by
  default before legacy file reads, points users to `POST
  /api/runs/chart-export`, and keeps `STOCKTRADE_ALLOW_LEGACY_CHART_EXPORT=1`
  as a rollback-only override.
- R7 preselect CLI retirement now stops `python -m pipeline.cli preselect` by
  default before config/data loading, selection execution, `data/candidates`
  writes, or `candidates_latest.json` updates; rollback requires
  `STOCKTRADE_ALLOW_LEGACY_PRESELECT_CLI=1`.
- R7 archive writer retirement now stops `python -m pipeline.archive_results`
  by default before legacy candidate/review/chart reads, `data/history` writes,
  or `data/history/index.json` updates; rollback requires
  `STOCKTRADE_ALLOW_LEGACY_ARCHIVE_RESULTS=1`.
- R7 runtime recovery now closes interrupted active FastAPI product runs on app
  startup, records `RuntimeRecovery` diagnostics, preserves late-cancellation
  terminal protection, and serializes product workflow jobs inside the local
  API process.
- R7 product launcher now provides `./start_product` as the local React/FastAPI
  entrypoint, enforces Node.js 22.x before Vite starts, initializes clean
  SQLite product schema through Alembic, and points `start_workbench` users to
  the product path while preserving the legacy workbench as compatibility-only.
- R7 run_all retirement now stops `run_all.py` by default before it invokes
  legacy subprocesses or reads `candidates_latest.json`/`suggestion.json`;
  rollback requires `STOCKTRADE_ALLOW_LEGACY_RUN_ALL=1` plus any child legacy
  flags needed for the selected legacy path.
- R7 runtime terminal integrity now prevents terminal product run and step state
  from being overwritten by late cancellation, retry, or recovery calls.
- R7 dashboard retirement now stops `dashboard/app.py` by default before it
  loads legacy chart components or reads legacy generated files; rollback
  requires the explicit `STOCKTRADE_ALLOW_LEGACY_DASHBOARD=1` flag.
- R7 workbench retirement now stops `start_workbench`, `workbench/app.py`, and
  `workbench/runner.py` by default before token lookup, Streamlit launch,
  legacy chart imports, `paper_trading.core` imports, or `data/runs` reads;
  rollback requires `STOCKTRADE_ALLOW_LEGACY_WORKBENCH=1`.
- R7 final retirement proof records the completion verdict for the confirmed
  product-refactor scope, names the closure evidence needed for #152/#23, and
  keeps permanent deletion of compatibility surfaces separate from default
  retirement guards.
- Product preselect execution boundary with named ports for market loading,
  preparation, pick-date resolution, liquidity-pool construction, and strategy
  execution; CSV market loading, base market preparation, pick-date fallback,
  top-turnover pool construction, strategy dispatch, and preselect warmup bars
  now have product-owned implementations covered by legacy parity tests.
  Residual legacy selector class wrappers are isolated behind
  `ProductStrategyFormulaFactoryPort` and product-owned B1/B2/brick selector
  classes under `src/stocktrade/domain/selection/selectors.py`. The legacy
  `LegacyStrategyFormulaFactoryPort` remains available only as an explicit
  compatibility/oracle adapter.
- Product-owned KDJ indicator helper is covered by formula reference tests and
  used by the legacy selector compatibility wrapper when the product package is
  available.
- Product-owned B1 pick mask helper is covered by formula reference tests and
  used by the legacy B1 selector compatibility wrapper when the product package
  is available.
- Product-owned KDJ quantile mask helper is covered by formula reference tests
  and used by the legacy selector compatibility wrapper when the product package
  is available.
- Product-owned ZX-line indicator helper is covered by formula reference tests
  and used by the legacy selector compatibility wrapper when the product package
  is available.
- Product-owned ZXDQ ratio mask helper is covered by formula reference tests and
  used by the legacy selector compatibility wrapper when the product package is
  available.
- Product-owned ZX condition mask helper is covered by formula reference tests
  and used by the legacy selector compatibility wrapper when the product package
  is available.
- Product-owned weekly close and weekly MA bull helpers are covered by formula
  reference tests and used by the legacy selector compatibility wrapper when
  the product package is available.
- Product-owned max-volume-not-bearish vector helper is covered by formula
  reference tests and used by the legacy selector compatibility wrapper when
  the product package is available.
- Product-owned B2 price-action metric helpers for daily return, body
  percentage, and upper-shadow ratio are covered by formula reference tests and
  used by the legacy selector compatibility wrapper when the product package is
  available.
- Product-owned B2 volume confirmation and recent-B1 lookup helpers are covered
  by formula reference tests and used by the legacy selector compatibility
  wrapper when the product package is available.
- Product-owned B2 pick mask and quality score helpers are covered by formula
  reference tests and used by the legacy B2 selector compatibility wrapper when
  the product package is available.
- Product-owned brick chart core helpers are covered by formula reference tests
  and used by legacy brick chart compatibility wrappers when the product package
  is available.
- Product-owned brick pattern helpers for green-run counts, growth ratios, and
  vector masks are covered by formula reference tests and used by legacy brick
  pattern compatibility wrappers when the product package is available.
- Product-owned brick pick mask helper is covered by formula reference tests
  and used by the legacy brick selector compatibility wrapper when the product
  package is available.

## Retained Compatibility And Non-Goals

The confirmed product-refactor objective is complete with these retained
boundaries:

- Legacy selector classes still exist in `pipeline/Selector.py` for legacy CLI
  rollback and behavior-oracle parity. The default product preselect formula
  path no longer depends on `LegacyStrategyFormulaFactoryPort`; removing
  legacy selector files entirely is a future destructive cleanup decision with
  separate rollback proof.
- Backend route coverage for primary research workflows is complete for the
  confirmed local-first scope. Broader backend features should be driven by new
  product issues instead of extending #23/#152.
- The frontend has deterministic R5/R7 browser evidence for the current core
  workflow surfaces. Future import/provider polish should be filed as separate
  UI enhancement issues.
- The legacy file-system state remains a compatibility and migration source.
  The legacy Gemini API reviewer, Gemini CLI reviewer, chart exporter, legacy
  preselect CLI, legacy archive writer, run_all wrapper, and Streamlit
  workbench are retired by default; permanent deletion is intentionally outside
  this completion claim.
- Final-cutover closure requires #152 and #23 issue comments with the exact
  main commit, row-by-row `r7-final-retirement-proof.md` verdict, validation
  output, and rollback notes for every remaining compatibility flag.
- Runtime cancellation and startup recovery have R7 coverage for the supported
  single-process local product deployment; multi-process writes to the same
  SQLite/DuckDB/artifact roots remain unsupported.

## Next Issue Queue

Use new issues for future scope changes. There is no default next issue for the
confirmed product-level refactor after #152/#23 closure.

The remaining open Antigravity exploration issue is independent of the
React/FastAPI product-refactor completion boundary.

## Completion Boundary

The full confirmed objective is complete when all of these are proven:

- Primary research workflows run through the React/FastAPI product.
- Strict-parity business areas pass golden master tests without live
  credentials.
- SQLite/DuckDB are the product-owned source of truth for in-scope state.
- Legacy file-system and Streamlit/workbench paths are retired or explicitly
  documented as legacy-only with rollback notes.
- Product UI/UX passes the documented quality bar with browser/screenshot
  evidence.
- `scripts/harness/check.sh r7-final-retirement-proof` passes with a completion
  verdict and final-cutover validation packet.
- `scripts/harness/check.sh product-refactor-readiness` and
  `scripts/harness/check.sh quick` pass at the final cutover point.
