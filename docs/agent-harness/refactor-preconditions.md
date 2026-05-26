# Refactor Preconditions

This file records decisions that control product-level rewrite work. Most
foundational decisions are now confirmed. Agents should follow them unless the
user explicitly changes the architecture.

## Confirmed Decisions

1. Product shape.
   - The target product is a React web frontend plus a FastAPI backend.
   - The default deployment model is local-first: a browser UI served by the
     frontend build/dev server and a local FastAPI API process.
   - Cloud deployment, multi-user auth, mobile apps, and real trading remain
     out of scope until explicitly approved.

2. Core business language.
   - Core business logic remains Python.
   - Rewrite business logic into clean Python domain modules with behavior
     parity instead of preserving old module structure.
   - Pure domain logic must stay independent of HTTP, UI, storage, model calls,
     and file IO where practical.

3. Frontend stack.
   - React + Vite + TypeScript is the confirmed frontend stack.
   - Use React Router for workflow routing, TanStack Query for server state,
     TanStack Table for dense tabular views, Apache ECharts for charts, Tailwind
     CSS plus shadcn/ui conventions for restrained product UI, and lucide-react
     for icons unless implementation evidence shows a better fit.
   - Use Vitest and Testing Library for component/unit tests, and Playwright for
     product-flow smoke tests and screenshots.

4. Backend stack.
   - FastAPI is the confirmed backend runtime.
   - Use Pydantic v2 for request/response schemas and domain-facing validation.
   - Use SQLAlchemy 2.x for SQLite product-state access and Alembic for SQLite
     migrations.
   - Use the DuckDB Python API for analytical queries and a small versioned SQL
     migration runner for DuckDB schema changes.
   - Use FastAPI TestClient or HTTPX plus pytest for API tests.

5. Database engines.
   - SQLite is the confirmed product-state database for local durable state:
     settings, run records, job state, candidate batches, review/archive
     indexes, artifact metadata, and migration bookkeeping.
   - DuckDB is the confirmed analytical database for historical scans,
     strategy/date/run comparisons, backtest-style analysis, and larger
     column-oriented datasets.
   - Parquet is not a confirmed primary database. It may be used later as an
     optional import/export or archival file format if a specific migration or
     analytics workflow needs it.

6. Local state layout and artifacts.
   - New product-owned local state should live under `var/`:
     `var/db/app.sqlite`, `var/db/analytics.duckdb`,
     `var/artifacts/{run_id}/`, and `var/backups/{timestamp}/`.
   - Legacy `data/` is source material for migration and parity, not the target
     write path.
   - Generated `var/` state must stay out of version control.

7. Backup and restore.
   - A backup contains SQLite state, DuckDB state, artifact manifest, migration
     versions, and product version metadata.
   - Restore must recreate a usable local product state without mutating legacy
     `data/`.

8. Data migration tolerance.
   - Migrate every readable legacy record needed by in-scope workflows.
   - Malformed or unsupported records are quarantined with a report; they are
     not silently discarded or rewritten in place.

9. UI acceptance baseline.
   - The default visual direction is a restrained modern research workstation:
     useful, attractive, friendly, and not overdesigned.
   - Required viewports are desktop `1440x900`, desktop `1280x800`, and mobile
     inspection width `390px`.
   - Primary flows require Playwright smoke and screenshot evidence.

10. Runtime resource envelope.
   - Backend API startup should avoid heavy market-data imports and target
     under 5 seconds on a normal local workstation.
   - Idle backend memory should target under 300 MB before heavy jobs.
   - Default heavy job concurrency is 1.
   - Frontend app shell should be code-split; charts and large tables should
     load by route or view.

11. External integration policy.
   - Default validation is fixture/offline.
   - Tushare and Gemini calls require task-specific justification.
   - No real broker/trading integration unless explicitly approved.

12. Simulated trading exclusion.
   - Simulated trading / paper trading is out of scope for the product-level
     refactor.
   - Need explicit approval before rewriting, migrating, or adding UI for that
     module.

## Remaining Confirmation Triggers

Stop and ask before implementation when a task would:

- deviate from React/Vite/TypeScript, FastAPI, Python domain logic, SQLite, or
  DuckDB;
- introduce Redis, Celery, PostgreSQL, cloud services, auth, mobile, or
  multi-user architecture;
- change strict-parity business behavior such as strategy selection,
  `(code, strategy)`, same-date merge, review matching, or archive semantics;
- discard or rewrite legacy `data/` records instead of importing or
  quarantining them;
- reopen simulated trading / paper trading scope;
- consume live Gemini/Tushare capacity for broad validation.

## Harness-Safe Work Before Decisions

- write the product charter;
- capture behavior specs and golden master cases;
- inventory legacy `data/` artifacts;
- design UI information architecture and quality bar;
- design SQLite and DuckDB schema options;
- add credential-free validation gates.

## First Refactor Slice

The first implementation slice should be R0/R1, not a new app scaffold:

1. confirm or document blocking decisions;
2. freeze current behavior contracts;
3. capture representative fixtures;
4. define golden master parity rules;
5. finalize SQLite/DuckDB table ownership and migration files;
6. run `scripts/harness/check.sh product-refactor-readiness` and
   `scripts/harness/check.sh quick`.
