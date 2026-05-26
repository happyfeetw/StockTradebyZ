# Architecture Quality Bar

The rebuilt product should be simple enough to maintain and strong enough to
extend. Architecture decisions must serve product workflows, behavior parity,
resource limits, and long-term clarity.

## Frontend Architecture

- Use React + Vite + TypeScript.
- Use React Router for product workflow routing.
- Use TanStack Query for server state and request caching.
- Use TanStack Table for dense tabular views.
- Use Apache ECharts for charts unless a slice proves a narrower charting
  library is materially better.
- Use Tailwind CSS plus shadcn/ui conventions for restrained component styling
  and lucide-react for icons.
- Route structure follows product workflows.
- Component boundaries separate layout, data loading, forms, tables, charts,
  and domain-specific views.
- State management is explicit and scoped; avoid global state for everything.
- API clients are typed and generated or centrally defined.
- Heavy tables and charts use virtualization or pagination when needed.

## Backend Architecture

- Use FastAPI as the backend runtime.
- Keep core business logic in Python domain modules.
- Use Pydantic v2 for request/response schemas and validation boundaries.
- Use SQLAlchemy 2.x and Alembic for SQLite product-state access and
  migrations.
- Use DuckDB Python API plus versioned SQL migration files for analytical
  schema evolution.
- Separate API layer, job orchestration, domain logic, storage access, and
  external integrations.
- Core domain logic is pure where possible and independent of HTTP, CLI, model
  calls, and file IO.
- Background jobs have durable state, retry policy, cancellation, logs, and
  artifact records.
- Errors are typed enough for the frontend to show actionable feedback.
- External calls to market data or AI providers are isolated behind adapters.

## Storage Architecture

- Use SQLite as the product-state database and DuckDB as the analytical
  database.
- Keep SQLite ownership focused on durable application state: settings, runs,
  job status, candidate batches, review/archive indexes, artifact metadata, and
  migration bookkeeping.
- Keep DuckDB ownership focused on analytical state: historical scans,
  strategy/date/run comparisons, backtest-style analysis, and larger
  column-oriented datasets.
- Treat Parquet as an optional import/export or archival format, not the
  default primary database.
- Store new product-owned local state under `var/`, with
  `var/db/app.sqlite`, `var/db/analytics.duckdb`, `var/artifacts/{run_id}/`,
  and `var/backups/{timestamp}/`.
- Storage schema owns product state instead of scattering writes through the
  file system.
- Historical records are queryable by date, run, strategy, code, review status,
  and archive state.
- Large analytical data can use an analytical store or columnar files if the
  confirmed stack chooses that path.
- Imports from legacy `data/` are explicit, repeatable, and reversible.
- Schema migrations are versioned and testable.

## System Architecture

- Product shape is React web frontend plus FastAPI backend.
- Default local operation is two processes: frontend dev/build serving the web
  UI and FastAPI serving API/job endpoints.
- Avoid Redis, Celery, distributed queues, auth services, and cloud-only
  dependencies unless a future product decision requires them.
- The default shape should fit the confirmed deployment scope. Do not introduce
  distributed systems, queues, auth, or cloud services unless the product
  decision requires them.
- Prefer a modular monolith unless scale or isolation requirements justify
  more moving parts.
- Runtime resource usage should be measured: startup time, memory, job runtime,
  storage growth, and frontend bundle cost.
- Backend API startup target is under 5 seconds without heavy data imports.
- Idle backend memory target is under 300 MB before heavy jobs.
- Default heavy job concurrency is 1.
- Frontend app shell should be code-split; chart and large-table code should
  load by route or view.
- Observability is local-friendly: structured logs, job events, artifacts, and
  clear failure surfaces.

## Maintainability Requirements

- Domain models, API schemas, storage schemas, and UI view models have clear
  ownership.
- Tests cover pure business rules, API contracts, storage migrations, and key
  product workflows.
- New dependencies need a reason tied to product or maintainability value.
- Generated artifacts and personal data stay out of version control.
