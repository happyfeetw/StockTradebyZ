# Agent Harness Quality Scorecard

Use this scorecard when reviewing harness changes or product refactors.

## Scores

| Area | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Entry map | Missing or huge | Exists but vague | Short `AGENTS.md` points to current docs |
| Architecture readability | Hidden in code | Partial docs | Clear domain map and storage contracts |
| Mechanical checks | None | Manual commands only | Scripts/tests enforce core invariants |
| Observability | Logs only | Some state files | Machine-readable run state, logs, checkpoints |
| Contract stability | Ad hoc files | Documented files | Identity, schema, and migrations validated |
| Runtime safety | Can mutate real state | Mostly local | Explicit no-real-trade and no-secret boundaries |
| Drift cleanup | Reactive only | Occasional docs | Repeated failures become checks/docs/tests |
| Product refactor readiness | No phase gates | Phase docs only | Product charter, phases, contracts, preconditions, and stop conditions are checked |
| Business rewrite safety | Legacy behavior implicit | Some contracts listed | Golden masters and parity rules define behavior rewrite |
| UI/UX quality bar | Subjective polish | Visual notes only | Workflow, accessibility, responsive, and state requirements are testable |
| Architecture quality bar | Stack-first rewrite | Partial target docs | Frontend, backend, storage, system, and resource bars are explicit |

Target score for this repo: at least 18/22 before starting product rewrite
implementation.

## Current Assessment

| Area | Score | Evidence |
| --- | ---: | --- |
| Entry map | 2 | `AGENTS.md` now exists as a short map |
| Architecture readability | 2 | `ARCHITECTURE.md` maps domains and storage |
| Mechanical checks | 1 | `scripts/harness/check.sh` provides lightweight gates |
| Observability | 2 | run state, logs, Gemini checkpoint, raw logs exist |
| Contract stability | 1 | key contracts documented, fixture schema checks still pending |
| Runtime safety | 2 | docs and AGENTS ban secrets and real trading |
| Drift cleanup | 1 | maintenance rule exists, scheduled cleanup not yet automated |
| Product refactor readiness | 2 | product charter, phases, contracts, preconditions, and readiness gate exist |
| Business rewrite safety | 1 | parity rules exist, golden master fixtures still pending |
| UI/UX quality bar | 2 | measurable workflow, accessibility, responsive, and state requirements exist |
| Architecture quality bar | 2 | frontend, backend, storage, system, maintainability, and resource bars exist |

Current score: 18/22.

## Next Improvements

- Add JSON fixtures for candidates, reviews, and history.
- Add a schema check for `data/history/{date}/summary.json` and `all.json`.
- Add a workbench smoke test that can run on a temporary port with fixture data.
- Add a doc freshness check that flags docs referencing deleted paths.
- Add a file-size or ownership check before `workbench/app.py` grows further.
- Turn R0/R1 inventory into committed golden master fixtures before starting
  product rewrite implementation.
- Add Playwright smoke once the target frontend stack is confirmed.
