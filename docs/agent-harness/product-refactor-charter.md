# Product Refactor Charter

## Scope Statement

This refactor is a full product-level rewrite. It covers UI/UX, frontend
architecture, backend architecture, system architecture, data architecture,
storage, job runtime, and core business logic implementation.

The goal is not to polish the current Streamlit/file-system product. The goal
is to rebuild StockTradebyZ as a product-quality trading research workstation
while preserving the business behavior that makes the current project valuable.
Simulated trading / paper trading is excluded from this product-level refactor
unless the user explicitly reopens that scope.

The confirmed product shape is a React/Vite/TypeScript web frontend backed by a
FastAPI API. Core business logic remains Python and is rewritten into clean
domain modules with behavior parity.

## Target Qualities

- Frontend interface is practical, attractive, modern, and friendly.
- UX is workflow-oriented, clear, fast to operate, and not overdesigned.
- Target frontend architecture supports maintainable routes, components,
  client state, charts, typed API access, React Router, TanStack Query,
  TanStack Table, Apache ECharts, Tailwind CSS, shadcn/ui conventions, and
  lucide-react icons.
- Target backend architecture supports clear APIs, jobs, domain logic, storage,
  external provider boundaries, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic,
  and DuckDB analytical access.
- Target system architecture fits the confirmed deployment scope without
  unnecessary moving parts.
- Target data architecture supports durable product state, historical analysis,
  migration, and future extension.
- Database engines are confirmed as SQLite for product state and DuckDB for
  analytical data.
- Backend architecture is explicit, modular, testable, and maintainable.
- Core business logic is rewritten in the new stack with behavior parity.
- Storage architecture supports durable state, historical analysis, migration,
  and future extension.
- Runtime resource usage is reasonable for the confirmed deployment scope.
- Observability makes long-running jobs inspectable by humans and agents.

## Product Workflows

The rebuilt product must support these primary workflows:

- configure data source, strategy, review, archive, and analysis settings;
- run candidate selection and see progress;
- inspect candidates by date, strategy, score, reason, and chart evidence;
- run or import AI review and inspect normalized recommendation evidence;
- archive results and browse historical runs;
- compare strategy outcomes and historical review/archive state;
- diagnose failures through logs, status, artifacts, and retry paths.

## Rewrite Boundary

Legacy code is allowed to inform the rewrite, but should not dictate the new
architecture. The new product should own:

- domain models;
- storage schema;
- API contracts;
- frontend routes and components;
- job runtime;
- validation and observability.

Compatibility adapters are allowed for migration, import/export, and parity
tests. They are not the final architecture.

## Non-Goals Until Confirmed

- real trading or broker integration;
- simulated trading / paper trading rewrite or product UI;
- multi-user SaaS;
- cloud deployment;
- mobile app;
- changing strategy semantics for product polish;
- large live Gemini validation runs.

These may become product goals later, but each requires an explicit decision.
