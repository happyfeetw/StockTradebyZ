# R7 Final Browser Proof

Managing issue: #152
Parent epic: #23
Status date: 2026-05-27

This document records the R7 browser proof for the rebuilt React/FastAPI
workstation using deterministic fixture data. It proves current product
surfaces can be inspected without live Tushare data, Gemini credentials,
legacy generated files, or simulated trading.

## Reproducible Setup

Seed fixture state:

```bash
PYTHONPATH=apps/api:src python3 scripts/harness/seed_ui_smoke.py --force
```

Start the smoke API:

```bash
PYTHONPATH=apps/api:src:. python3 -m uvicorn scripts.harness.ui_smoke_app:app --host 127.0.0.1 --port 8000
```

Start the React workstation:

```bash
cd apps/web
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

Mechanical preflight:

```bash
scripts/harness/check.sh ui-smoke-fixture
scripts/harness/check.sh r7-browser-proof
```

## Browser Matrix

Routes checked at desktop `1440x1000` and mobile `430x932`:

| Route | URL | Desktop | Mobile |
| --- | --- | --- | --- |
| Overview | `/` | PASS | PASS |
| Run Center | `/runs?run_id=run-ui-smoke-active` | PASS | PASS |
| Candidates | `/candidates?pick_date=2026-05-27` | PASS | PASS |
| Reviews | `/reviews?pick_date=2026-05-27` | PASS | PASS |
| Archive | `/archive?pick_date=2026-05-27` | PASS | PASS |
| Analytics | `/analytics?pick_date=2026-05-27` | PASS | PASS |
| Settings | `/settings` | PASS | PASS |
| Migrations | `/migrations` | PASS | PASS |

PASS means:

- route-specific page text was present;
- no visible `Failed to fetch`, `Internal Server Error`, or `NetworkError`;
- `documentElement.scrollWidth <= documentElement.clientWidth + 1`;
- no broken rendered images were reported;
- no browser console errors were captured.

## Evidence Notes

Screenshots were generated under ignored local state:

```text
var/ui-smoke/screenshots/r7-final/
```

Generated files:

- `desktop-overview.png`
- `desktop-runs.png`
- `desktop-candidates.png`
- `desktop-reviews.png`
- `desktop-archive.png`
- `desktop-analytics.png`
- `desktop-settings.png`
- `desktop-migrations.png`
- `mobile-overview.png`
- `mobile-runs.png`
- `mobile-candidates.png`
- `mobile-reviews.png`
- `mobile-archive.png`
- `mobile-analytics.png`
- `mobile-settings.png`
- `mobile-migrations.png`
- `mobile-archive-chart-scrolled.png`

The screenshots are not committed because they are reproducible generated
evidence and belong under ignored local smoke state.

## Artifact Proof

Archive chart evidence was inspectable through the product artifact API:

- desktop Archive rendered
  `/api/artifacts/artifact-ui-smoke-chart-000001-b2` as an image;
- rendered image dimensions: `267x150`;
- mobile Archive exposed the same artifact link and loaded the image after
  scroll-triggered lazy loading;
- API logs showed `GET /api/artifacts/artifact-ui-smoke-chart-000001-b2` as
  `200 OK`.

## API And Console Result

Observed API routes returned `200 OK` during the browser pass, including:

- `/api/health`
- `/api/settings`
- `/api/strategies`
- `/api/runs`
- `/api/runs/run-ui-smoke-active`
- `/api/runs/run-ui-smoke-active/artifacts`
- `/api/jobs/run-ui-smoke-active/events`
- `/api/candidates?pick_date=2026-05-27`
- `/api/candidate-batches?pick_date=2026-05-27`
- `/api/reviews?pick_date=2026-05-27`
- `/api/archive/2026-05-27`
- `/api/archive/rows/1`
- `/api/analytics/strategy-summary?pick_date=2026-05-27&limit=100`

Browser console error log: empty.

## Residual Risk

This proof uses deterministic fixture state, not live market data or provider
capacity. It is sufficient for R7 product-shell confidence, but it does not
replace parity tests, resource evidence, or surface-specific retirement proof.
Legacy write freeze and retirement PRs still need separate rollback notes.
