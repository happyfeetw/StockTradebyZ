# R6 Storage Cutover Plan

Managing issue: #115
Parent epic: #23
Status date: 2026-05-27

This document is the source-of-truth map for moving in-scope workflows from
legacy file-system state to product-owned SQLite, DuckDB, and artifact storage.
It is not a permission to delete legacy `data/` records. Legacy files remain
oracle and migration inputs until R7 retirement evidence exists.

## Scope Boundary

In scope:

- candidate preselect results;
- review results and recommendations;
- provider review evidence for Gemini CLI runs;
- chart artifacts used by review and archive flows;
- archive/history snapshots;
- backup and restore for product-owned state;
- migration from legacy `data/` into product storage.

Out of scope:

- simulated or paper trading under `data/trading/`;
- live Tushare fetch behavior and raw market-data acquisition;
- deleting historical `data/` files;
- changing strict business parity rules.

## Ownership Matrix

| Workflow State | Product Source Of Truth | Analytical Copy | Legacy Compatibility | R6 Decision |
| --- | --- | --- | --- | --- |
| Raw market CSV input | External/local input under `data/raw/*.csv` | None | `pipeline/fetch_kline.py` writes CSV | Keep as input source for now; do not migrate into SQLite/DuckDB in R6 |
| Candidate preselect results | SQLite `candidate_batches` and `candidates` | DuckDB `candidate_facts` | `data/candidates/candidates_latest.json` and dated files | New product runs write SQLite/DuckDB only; legacy CLI remains oracle/compatibility |
| Review results | SQLite `review_runs`, `reviews`, `recommendations` | DuckDB `review_facts` | `data/review/{pick_date}/*.json` and `suggestion.json` | Product review APIs write SQLite/DuckDB; legacy review files are migration source |
| Provider raw evidence | `var/artifacts/review-provider/{batch}/gemini-cli/` plus review payload lineage | None | `data/review/{pick_date}/gemini_cli_runs/*` | Must be indexed as product artifacts before final cutover |
| Chart evidence | SQLite `artifacts` rows plus `var/artifacts/{run_id}/charts/...` | DuckDB archive facts reference artifact ids | `data/kline/{pick_date}/*_day.*` | New chart export writes product artifacts; legacy charts import or copy into product artifacts |
| Archive/history | SQLite `archive_snapshots` and `archive_rows` | DuckDB `archive_facts` | `data/history/{pick_date}/summary.json`, `all.json`, strategy files, `index.json` | Product archive APIs write SQLite/DuckDB; legacy history becomes migration source |
| Run/job state | SQLite `runs`, `job_steps`, `job_events`, `artifacts` | Optional analytics facts by workflow | `data/runs/{run_id}/run_state.json` and logs | Product runtime is SQLite-first; legacy run snapshots are not product source of truth |
| Settings | SQLite `app_settings` plus safe config-file metadata | None | YAML config files | SQLite preferences own product UI settings; config files remain strategy/provider inputs |
| Backup/restore | `var/backups/{timestamp}/` manifest and copied DB/artifact state | Included with product DB backup | None | Must include artifact files, artifact manifest, SQLite, DuckDB, and schema metadata before write cutover |
| Paper trading | None for this refactor | None | `data/trading/` | Excluded; do not migrate or rewrite |

## Current Implementation Audit

Product-owned paths already exist:

- `JobRuntime.run_preselect_job()` writes candidate batches into SQLite through
  `RunRepository.create_candidate_batch()`.
- `ReviewRunService` writes review runs, reviews, recommendations, and DuckDB
  review facts.
- `ReviewProviderRunService` reads candidate batches and chart artifacts from
  product storage, then writes normalized review results through
  `ReviewRunService`.
- `ChartExportRunService` writes chart files under `var/artifacts/` and creates
  SQLite `artifacts` rows.
- `ArchiveRunService` writes archive snapshots/rows and DuckDB archive facts.
- `MigrationRepository` imports legacy candidates, reviews, history, and chart
  references into SQLite/DuckDB and can copy legacy charts into product
  artifacts.
- `BackupService` copies SQLite, DuckDB, and product artifact files; writes
  `artifacts_manifest.json`; restores product artifacts; and records
  backup/restore runs.

Cutover gaps:

- Gemini CLI product provider stores raw logs, checkpoints, usage, and cache
  files under `var/artifacts/review-provider/...`. They are covered by artifact
  backup/restore as files, but they are not yet indexed as SQLite `artifacts`
  rows with explicit provider-evidence lineage.
- The legacy CLI still writes candidate files through `pipeline/pipeline_io.py`.
  This is acceptable as a compatibility path, but product UI/API flows should
  not depend on `data/candidates/candidates_latest.json`.
- The legacy archive script still writes `data/history/`. This remains valid as
  migration source, but product archive queries should use SQLite.
- Legacy dashboards/workbench can still read file contracts. They are outside
  the React/FastAPI product path and should not define new source-of-truth
  behavior.

## Cutover Sequence

1. Artifact backup/restore. Implemented first because later product-owned
   workflows depend on durable generated evidence.
   - Copy `var/artifacts/` into backups.
   - Store an artifact manifest with file path, backup path, size, and artifact
     root.
   - Restore artifacts by replacing the configured product artifact root with
     the backed-up artifact tree.
   - Verify SQLite artifact rows still resolve through the product artifact API
     after restore.

2. Provider evidence indexing.
   - Register Gemini CLI raw request/response directories, checkpoint, result
     cache, and usage file as product artifacts or a structured provider
     evidence manifest.
   - Keep large raw payloads out of SQLite blobs.
   - Preserve review row lineage to provider evidence.

3. Candidate product-write proof.
   - Run a fixture preselect through FastAPI using local CSV input.
   - Assert SQLite contains the candidate batch and candidates.
   - Assert DuckDB contains matching candidate facts.
   - Assert no product UI/API read path requires `data/candidates`.

4. Review and archive product-write proof.
   - Run fixture review results through FastAPI.
   - Run chart export and archive through FastAPI.
   - Assert SQLite, DuckDB, and product artifact API produce the full evidence
     chain without `data/review` or `data/history`.

5. Legacy import bridge.
   - Keep `POST /api/migrations/import-legacy` as the only write path from
     legacy `data/` into product storage.
   - Require pre-import backup when import writes to SQLite/DuckDB.
   - Preserve quarantine rows for malformed or incompatible records.

6. Legacy write freeze.
   - Mark `pipeline.cli`, `agent/*review*.py`, `pipeline.archive_results`, and
     Streamlit/workbench file readers as legacy compatibility surfaces.
   - Do not delete them until R7.
   - Any new React/FastAPI workflow must use product storage directly.

## Validation Requirements

Minimum gates for R6 cutover PRs:

```bash
scripts/harness/check.sh storage-cutover-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

Implementation PRs that change writes must also prove the touched runtime path:

- artifact backup/restore: backup, mutate/delete product artifacts, restore,
  serve artifact through `/api/artifacts/{artifact_id}`;
- candidate cutover: FastAPI preselect fixture writes SQLite and DuckDB facts;
- review cutover: FastAPI review fixture writes SQLite and DuckDB facts;
- archive cutover: FastAPI chart-export plus archive fixture writes product
  artifacts, SQLite archive rows, and DuckDB facts;
- legacy import cutover: import fixture records pre-import backup id/path and
  preserves quarantine rows.

## Rollback Rules

- Before any PR disables or bypasses a legacy write path, backup product SQLite,
  DuckDB, and artifacts.
- Restore must never mutate `data/`.
- Rollback for a failed product write cutover is: restore product backup,
  re-enable legacy compatibility path, and leave migration quarantine evidence.
- Do not delete legacy files as part of rollback or cutover.

## First Implementation Target

The first implementation target is artifact backup/restore. It is independent
of trading logic, protects every later product-owned workflow, and is covered by
an API contract test that backs up a product artifact, mutates local artifact
state, restores the backup, and serves the restored file through
`/api/artifacts/{artifact_id}`.
