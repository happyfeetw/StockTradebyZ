# R7 Resource Envelope Evidence

Managing issue: #152
Parent epic: #23
Status date: 2026-05-27

This document records the credential-free resource evidence baseline for the
React/FastAPI/SQLite/DuckDB product path. It is a local guardrail, not a
production benchmark or a latency SLA.

## Reproducible Command

```bash
python3 scripts/harness/resource_envelope.py
scripts/harness/check.sh r7-resource-envelope
```

The script runs the supported product workflow in a temporary directory:

```text
preselect -> chart export -> provider review -> archive
```

It uses fixture market CSV files and a fixture provider executor. It does not
call Tushare, Gemini, Streamlit, `data/trading`, or live legacy generated state.
Simulated trading remains out of scope.

## Conservative Guardrails

These thresholds are intentionally loose so the gate catches runaway local
resource use without becoming a flaky performance benchmark.

| Metric | Limit |
| --- | ---: |
| API startup and schema preparation | 5.0 s |
| Product workflow runtime | 30.0 s |
| Python traced peak memory | 768 MB |
| Process peak RSS | 2048 MB |
| SQLite growth | 64 MB |
| DuckDB growth | 256 MB |
| Artifact growth | 128 MB |

## Evidence Snapshot

Collected on 2026-05-27 with:

```bash
python3 scripts/harness/resource_envelope.py
```

| Metric | Observed |
| --- | ---: |
| Startup runtime | 0.8455 s |
| Workflow runtime | 0.4190 s |
| Python traced peak memory | 82.8683 MB |
| Process peak RSS | 247.1094 MB |
| SQLite growth | 253,952 bytes |
| DuckDB growth | 2,895,872 bytes |
| Artifact growth | 261,629 bytes across 20 files |

SQLite rows after the fixture run:

| Table | Rows |
| --- | ---: |
| `runs` | 4 |
| `job_steps` | 4 |
| `job_events` | 12 |
| `artifacts` | 12 |
| `candidate_batches` | 1 |
| `candidates` | 2 |
| `review_runs` | 1 |
| `reviews` | 2 |
| `recommendations` | 2 |
| `archive_snapshots` | 1 |
| `archive_rows` | 2 |

DuckDB rows after the fixture run:

| Table | Rows |
| --- | ---: |
| `candidate_facts` | 2 |
| `review_facts` | 2 |
| `archive_facts` | 2 |
| `strategy_run_metrics` | 2 |

## Acceptance Boundary

The R7 resource slice is acceptable when:

- `scripts/harness/check.sh r7-resource-envelope` passes;
- the fixture writes only temporary SQLite, DuckDB, backup, artifact, and raw
  CSV state;
- artifact, SQLite, and DuckDB growth are visible in the JSON report;
- the report proves the product-owned API path, not direct legacy file writers.

This evidence must be refreshed when a later R7 PR materially changes runtime
startup, product workflow execution, chart artifact generation, provider
evidence indexing, archive writes, or storage layout.
