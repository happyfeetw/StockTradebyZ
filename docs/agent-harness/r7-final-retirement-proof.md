# R7 Final Retirement Proof

Managing issue: #152
Parent epic: #23
Status date: 2026-05-28

This document is the R7 completion audit packet and final completion claim for
the confirmed product-refactor scope. It proves the React/FastAPI/SQLite/DuckDB
rewrite is complete for the primary trading research workstation workflows
while preserving strict business behavior and keeping simulated/paper trading
out of scope.

Current verdict: complete for the confirmed scope. The product-owned workflow
paths, storage, runtime, UI evidence, strict-parity contracts, and default
legacy retirements have named proof. Remaining legacy files and compatibility
entrypoints are intentionally retained as migration, rollback, or
behavior-oracle surfaces; permanent deletion is a future destructive cleanup
decision, not part of this completion claim.

## Completion Boundary Matrix

| Boundary | Required proof | Current evidence | Audit status |
| --- | --- | --- | --- |
| Primary research workflows run through the React/FastAPI product | Product launcher, Run Center/API replacement, and browser proof | `r7-product-launcher.md`, `r7-final-browser-proof.md`, `r7-run-all-retirement.md`, `r6-storage-cutover-plan.md` | Complete for credential-free local fixture workflows; live-provider execution remains credential-driven and is not required for acceptance |
| Strict-parity business areas pass without live credentials | Golden master, domain, selector, provider, storage, and archive contracts | `business-logic-spec.md`, `r7-selector-adapter-retirement.md`, contract/unit tests under `tests/` | Complete for rewritten in-scope behavior; legacy selector code remains an explicit behavior oracle |
| SQLite/DuckDB are the product-owned source of truth for in-scope state | Product write proof, backup/restore, provider evidence indexing, and no product reads from legacy generated paths | `r6-storage-cutover-plan.md`, `r7-legacy-write-freeze.md`, product workflow storage contracts | Complete; historical `data/` remains migration and rollback input |
| Legacy file-system and Streamlit/workbench paths are retired or compatibility-only | Default-stop guards, explicit rollback flags, replacement product paths, and compatibility notices | `r7-gemini-api-review-retirement.md`, `r7-gemini-cli-review-retirement.md`, `r7-chart-export-retirement.md`, `r7-archive-retirement.md`, `r7-preselect-cli-retirement.md`, `r7-dashboard-retirement.md`, `r7-workbench-retirement.md`, `r7-run-all-retirement.md` | Complete; retained entrypoints are compatibility-only behind explicit rollback flags |
| Product UI/UX passes the documented quality bar | Desktop/mobile browser evidence, route coverage, no-overflow checks, console/API notes, and chart artifact inspection | `r5-ui-browser-smoke.md`, `r7-final-browser-proof.md`, `uiux-quality-bar.md` | Complete with fixture-backed product UI proof |
| Product runtime is hardened for local use | Terminal-state immutability, startup recovery, cancellation semantics, resource envelope, and single-process concurrency notes | `r7-runtime-terminal-integrity.md`, `r7-runtime-recovery.md`, `r7-resource-envelope.md` | Complete for supported single-process local deployment; multi-process writes to the same local roots remain unsupported |
| Final cutover gates pass on the closing commit | R7 proof gate, R7 plan gate, product readiness, quick, and diff check on `main` | `scripts/harness/check.sh r7-final-retirement-proof`, `scripts/harness/check.sh r7-retirement-plan`, `scripts/harness/check.sh product-refactor-readiness`, `scripts/harness/check.sh quick`, `git diff --check` | Complete when the final closure comment records the merge commit and validation output |

## Final Closure Evidence

The final #152/#23 issue comments must include:

- the exact `main` commit being closed;
- the verdict for every row in the completion boundary matrix;
- validation output for:

```bash
scripts/harness/check.sh r7-final-retirement-proof
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
git diff --check
```

- an explicit statement that no historical `data/` files, credentials,
  generated market snapshots, or paper-trading state were committed;
- a rollback note for every legacy compatibility surface that remains callable
  behind an environment flag.

## Retained Compatibility Decisions

These decisions are part of the final state:

- Keep `data/` files and generated legacy state as migration and rollback
  source material. Do not delete or rewrite them in the product refactor.
- Keep `pipeline/Selector.py` and legacy selector oracle code as behavior
  evidence for future parity or incident work. Product preselect defaults use
  product-owned selectors, not the legacy formula factory.
- Keep compatibility rollback flags for retired surfaces. They are explicit
  incident/migration tools and are suppressed by default.
- Do not reopen simulated or paper trading; `data/trading` remains out of
  product-refactor scope.

## Harness Gate

Run:

```bash
scripts/harness/check.sh r7-final-retirement-proof
scripts/harness/check.sh r7-retirement-plan
scripts/harness/check.sh product-refactor-readiness
scripts/harness/check.sh quick
```

The `r7-final-retirement-proof` gate proves:

- this audit packet exists and names #152;
- the audit records a final completion verdict for the confirmed scope;
- every R7 evidence document and gate is named;
- the status map records completion plus retained compatibility decisions;
- final closure requires a validation packet on `main`.
