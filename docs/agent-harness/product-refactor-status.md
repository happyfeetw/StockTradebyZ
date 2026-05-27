# Product Refactor Status

Managing issue: #111
Parent epic: #23
Last status sync: 2026-05-27
Baseline commit: `28dc70a`

This file is the working status map for the full product-level refactor. It is
not a completion claim. It tells agents where the React/FastAPI/SQLite/DuckDB
rewrite currently stands, which evidence proves that status, and which issue
should be picked next.

## Current Phase Position

The refactor is no longer only in architecture design. R0 through R2 have
repo-backed baselines, and implementation has advanced across R3, R4, R5, and
R6. The remaining work is still substantial because the final goal requires a
product-quality UI, product-owned storage cutover, core business logic rewritten
behind parity tests, and legacy path retirement.

Default next phase focus: R3 core domain isolation, tracked by #112.

Rationale: backend, storage, import, artifact, review, archive, and scaffolded
web surfaces already exist, but the highest correctness risk is still preserving
strategy behavior while moving core logic out of legacy-shaped modules.

## Phase Status

| Phase | Current Status | Evidence | Remaining Work |
| --- | --- | --- | --- |
| R0 Product charter and decision freeze | Baseline complete | #24, `product-refactor-charter.md`, `refactor-preconditions.md`, readiness gate | Keep decisions current when scope changes |
| R1 Business logic specification | Partial and active | #26, `tests/fixtures/golden_master/`, `tests/test_golden_master_contracts.py` | Add parity fixtures before each remaining domain rewrite |
| R2 Target architecture and data model | Baseline complete, needs drift control | #29, `target-architecture-design.md`, SQLite/DuckDB migrations | Keep this file and architecture design in sync with implementation |
| R3 Core domain rewrite | Partial | #100 moved review suggestion logic into `src/stocktrade/domain/review/`; #112 split preselect into named ports with product-owned orchestration, CSV market loading, base market preparation, trading-date fallback, top-turnover pool construction, strategy dispatch, warmup bars, KDJ, KDJ quantile mask, ZX-line, ZXDQ ratio, ZX condition mask, weekly MA bull, max-volume filter, B2 price-action metrics, B2 volume confirmation, recent-B1 lookup, brick chart core, and brick pattern parity coverage | #112 still must replace remaining legacy selector formulas behind parity-tested ports |
| R4 Backend runtime and APIs | Substantial partial implementation | #31, #33, #35, #37, #43, #44, #49, #55, #79, #102, #103, #105, #107, #108, #110 | #113 must add settings, strategy metadata, and analytics summary contracts |
| R5 Frontend product UI/UX | Scaffold plus workflow views | #41, #46, #52, #58, #61, #91, #94, #98, #104, #109 | #114 must productize IA, dense tables, chart evidence, and state coverage |
| R6 Data migration and storage cutover | Migration/import tooling partial | #39, #64, #66, #70, #75, #77, #81, #83, #85, #87, #89, #96 | #115 must define and execute source-of-truth cutover for in-scope workflows |
| R7 Hardening and legacy retirement | Not started | No retirement PR has landed | Requires parity, migration, UI smoke, rollback, and resource evidence first |

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
- Product review provider boundary plus Gemini CLI provider adapter preserving
  checkpoint, retry/backoff, raw logs, skip cache, batch order validation, and
  chart lineage.
- Product chart export workflow that creates both compatibility `code_day.jpg`
  chart artifacts and strategy-scoped `code_strategy_day.jpg` artifacts.
- Product preselect execution boundary with named ports for market loading,
  preparation, pick-date resolution, liquidity-pool construction, and strategy
  execution; CSV market loading, base market preparation, pick-date fallback,
  top-turnover pool construction, and strategy dispatch now have product-owned
  implementations covered by legacy parity tests. Preselect warmup bars
  calculation is also product-owned and covered by legacy parity tests, while
  formula classes remain isolated behind a legacy factory.
- Product-owned KDJ indicator helper is covered by formula reference tests and
  used by the legacy selector compatibility wrapper when the product package is
  available.
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
- Product-owned brick chart core helpers are covered by formula reference tests
  and used by legacy brick chart compatibility wrappers when the product package
  is available.
- Product-owned brick pattern helpers for green-run counts, growth ratios, and
  vector masks are covered by formula reference tests and used by legacy brick
  pattern compatibility wrappers when the product package is available.

## Current Architectural Gaps

Do not treat these as optional polish. They are still required for the user's
full objective:

- Remaining strategy selector formulas still need deeper product-domain
  isolation and parity coverage before legacy-shaped modules can be retired.
- Backend route coverage is incomplete for settings, strategy metadata, and
  analytics summary workflows named in the target architecture.
- The frontend has a usable scaffold but not yet the final product-grade
  workstation UI/UX quality bar.
- File-system state is still a live compatibility and migration source. The
  product-owned storage cutover is not complete.
- Runtime cancellation and concurrency are simple and local-first; this is
  acceptable for now but must be hardened before final retirement.
- Resource envelope, browser smoke, screenshot evidence, and legacy retirement
  proof are not yet R7-ready.

## Next Issue Queue

Use these issues unless a newer issue supersedes them:

1. #112: R3 isolate strategy selection domain behind parity-tested ports.
2. #113: R4 add product settings, strategy metadata, and analytics summary API
   contracts.
3. #114: R5 productize React workstation UI information architecture and core
   surfaces.
4. #115: R6 define and execute product storage cutover plan for in-scope
   workflows.

The default next issue is #112 because it protects the "do not change business
logic" requirement before more UI/storage replacement work depends on behavior
that is still partly legacy-shaped.

## Completion Boundary

The full objective is not complete until all of these are proven:

- Primary research workflows run through the React/FastAPI product.
- Strict-parity business areas pass golden master tests without live
  credentials.
- SQLite/DuckDB are the product-owned source of truth for in-scope state.
- Legacy file-system and Streamlit/workbench paths are retired or explicitly
  documented as legacy-only with rollback notes.
- Product UI/UX passes the documented quality bar with browser/screenshot
  evidence.
- `scripts/harness/check.sh product-refactor-readiness` and
  `scripts/harness/check.sh quick` pass at the final cutover point.
