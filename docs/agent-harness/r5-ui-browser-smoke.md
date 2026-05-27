# R5 UI Browser Smoke

Managing issue: #114
Parent epic: #23
Status date: 2026-05-27

This runbook gives agents a deterministic browser-review fixture for the React
workstation. It is not a full refactor completion claim. It proves that the R5
surfaces can be inspected without live Tushare data, Gemini credentials, or
legacy file-system state.

## Scope

The fixture covers the product UI surfaces that must stay usable while the
project moves toward the final React/FastAPI/SQLite/DuckDB rewrite:

- Overview, Analytics, Settings, Run Center, Candidates, Reviews, Archive, and
  Migrations navigation.
- SQLite-backed runs, job steps, events, artifacts, candidate batches, reviews,
  recommendations, archive snapshots, migration audit rows, and preferences.
- DuckDB-backed strategy-summary analytics.
- Product-owned chart artifact rendering through FastAPI artifact routes.
- Running, succeeded, failed, recommended, reviewed, unreviewed, empty-adjacent,
  and quarantine-adjacent states.

Out of scope:

- simulated or paper trading;
- live Tushare data;
- paid model/provider calls;
- final R7 legacy retirement proof.

## Fixture Command

```bash
PYTHONPATH=apps/api:src python3 scripts/harness/seed_ui_smoke.py --force
```

By default this writes only ignored local state:

- `var/ui-smoke/db/app.sqlite`
- `var/ui-smoke/db/analytics.duckdb`
- `var/ui-smoke/artifacts/`

The mechanical fixture gate is:

```bash
scripts/harness/check.sh ui-smoke-fixture
```

## Local Browser Harness

Start the smoke API:

```bash
PYTHONPATH=apps/api:src:. python3 -m uvicorn scripts.harness.ui_smoke_app:app --host 127.0.0.1 --port 8000
```

Start the web UI:

```bash
cd apps/web
npm run dev -- --host 127.0.0.1 --port 5173
```

Open:

- `http://127.0.0.1:5173/`
- `http://127.0.0.1:5173/runs?run_id=run-ui-smoke-active`
- `http://127.0.0.1:5173/candidates?pick_date=2026-05-27`
- `http://127.0.0.1:5173/reviews?pick_date=2026-05-27`
- `http://127.0.0.1:5173/archive?pick_date=2026-05-27`
- `http://127.0.0.1:5173/analytics?pick_date=2026-05-27`
- `http://127.0.0.1:5173/settings`
- `http://127.0.0.1:5173/migrations`

## Review Matrix

| Surface | Evidence To Inspect |
| --- | --- |
| Overview | product stack health, settings summary, recent runs, analytics preview |
| Run Center | selected running run, succeeded runs, failed review run, steps, events, artifact link |
| Candidates | batch readiness, strategy counts, dense list, selected detail, route links |
| Reviews | provider run, recommendation, review evidence, selected detail, candidate linkage |
| Archive | snapshot summary, recommended/reviewed/unreviewed rows, chart evidence preview |
| Analytics | DuckDB strategy summary for `2026-05-27` |
| Settings | SQLite-backed preferences and safe local path inventory |
| Migrations | import/verify controls plus migration audit data available through `/api/migrations/run-ui-smoke-migration` |

## Browser Acceptance Notes

For #114, capture or report:

- desktop screenshots at about 1440 px width for Overview, Run Center,
  Candidates, Reviews, Archive, and Analytics;
- mobile-width screenshots at about 430 px width for at least Settings and one
  dense result page;
- no horizontal overflow on every route above;
- keyboard spot checks for navigation, result rows, run rows, and primary
  buttons;
- chart artifact image renders from `/api/artifacts/artifact-ui-smoke-chart-000001-b2`;
- loading, empty-adjacent, failed, and quarantine-adjacent states are visible or
  reachable without credentials.

Current R5 status after this harness:

- PASS candidate/review/archive evidence chain, chart-evidence inspection, dense
  result-list scanning, and workstation information architecture when browser
  smoke passes.
- CONDITIONAL PASS migration and provider-error surfaces: the fixture exposes a
  failed review run and migration quarantine data, but full user-triggered
  import/verify error evidence belongs to R6/R7 hardening.

## 2026-05-27 Smoke Result

Local run against `scripts.harness.ui_smoke_app:app` and Vite on
`127.0.0.1`:

- desktop routes checked: Overview, Run Center, Candidates, Reviews, Archive,
  Analytics, Settings, and Migrations;
- mobile routes checked: Settings and Archive;
- screenshots written under `var/ui-smoke/screenshots/`;
- all checked routes reported `documentElement.scrollWidth <= viewport width`;
- keyboard spot checks reached navigation links, archive rows, evidence links,
  primary buttons, and form controls;
- chart artifact rendered as an image from
  `/api/artifacts/artifact-ui-smoke-chart-000001-b2`;
- no API responses at 400+ and no browser console errors were observed.

Observation: Run Center and Migrations do not currently expose the smoke date in
their top-level page text on every state. The backing API has the date for the
selected run and migration audit record; this is a UI copy/detail refinement,
not a harness blocker.

## Required Validation

Before marking #114 complete, run:

```bash
scripts/harness/check.sh ui-smoke-fixture
scripts/harness/check.sh docs
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
cd apps/web && npm run build && npm run lint
git diff --check
```
