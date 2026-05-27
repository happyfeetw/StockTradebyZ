# R7 Final Retirement Proof

Managing issue: #152
Parent epic: #23
Status date: 2026-05-28

This document is the R7 completion audit packet. It is not a completion claim.
Its job is to prevent agents from closing #152 or marking the full product
refactor complete until every completion-boundary item has a named proof,
rollback rule, and validation command.

Current verdict: not complete. R7 has retired the major legacy entrypoints by
default and has product runtime, resource, browser, storage, and selector proof,
but final closure still needs an explicit final-cutover validation comment on
#152 and a user-visible decision for any permanent deletion of compatibility
surfaces or legacy generated files.

## Completion Boundary Matrix

| Boundary | Required proof | Current evidence | Audit status |
| --- | --- | --- | --- |
| Primary research workflows run through the React/FastAPI product | Product launcher, Run Center/API replacement, and browser proof | `r7-product-launcher.md`, `r7-final-browser-proof.md`, `r7-run-all-retirement.md`, `r6-storage-cutover-plan.md` | Proven for credential-free local fixture workflows; live-provider execution remains credential-driven and is not required for acceptance |
| Strict-parity business areas pass without live credentials | Golden master, domain, selector, provider, storage, and archive contracts | `business-logic-spec.md`, `r7-selector-adapter-retirement.md`, contract/unit tests under `tests/` | Covered for rewritten in-scope behavior; legacy selector deletion still requires a separate decision |
| SQLite/DuckDB are the product-owned source of truth for in-scope state | Product write proof, backup/restore, provider evidence indexing, and no product reads from legacy generated paths | `r6-storage-cutover-plan.md`, `r7-legacy-write-freeze.md`, product workflow storage contracts | Product state is proven for in-scope workflows; historical `data/` remains migration and rollback input |
| Legacy file-system and Streamlit/workbench paths are retired or compatibility-only | Default-stop guards, explicit rollback flags, replacement product paths, and compatibility notices | `r7-gemini-api-review-retirement.md`, `r7-gemini-cli-review-retirement.md`, `r7-chart-export-retirement.md`, `r7-archive-retirement.md`, `r7-preselect-cli-retirement.md`, `r7-dashboard-retirement.md`, `r7-workbench-retirement.md`, `r7-run-all-retirement.md` | Retired by default with explicit rollback flags; permanent deletion is not approved in this audit |
| Product UI/UX passes the documented quality bar | Desktop/mobile browser evidence, route coverage, no-overflow checks, console/API notes, and chart artifact inspection | `r5-ui-browser-smoke.md`, `r7-final-browser-proof.md`, `uiux-quality-bar.md` | Fixture-backed product UI proof is available |
| Product runtime is hardened for local use | Terminal-state immutability, startup recovery, cancellation semantics, resource envelope, and single-process concurrency notes | `r7-runtime-terminal-integrity.md`, `r7-runtime-recovery.md`, `r7-resource-envelope.md` | Covered for supported single-process local deployment; multi-process writes to the same local roots remain unsupported |
| Final cutover gates pass on the closing commit | R7 proof gate, R7 plan gate, product readiness, quick, and diff check on `main` | `scripts/harness/check.sh r7-final-retirement-proof`, `scripts/harness/check.sh r7-retirement-plan`, `scripts/harness/check.sh product-refactor-readiness`, `scripts/harness/check.sh quick`, `git diff --check` | Not yet run as a final-cutover closure packet |

## Required Closing Evidence

Before #152 can be closed, post a final issue comment that includes:

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

## Open Decisions

These items block any stronger completion claim:

- Do not delete `data/` files or generated legacy state in #152 without a
  separate user-visible deletion decision and rollback plan.
- Do not delete `pipeline/Selector.py` or legacy selector oracle code until the
  user approves permanent removal or a replacement behavior-oracle plan.
- Do not remove compatibility rollback flags unless the corresponding product
  replacement has a final closure comment and incident rollback story.
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
- the audit still distinguishes proof from completion;
- every R7 evidence document and gate is named;
- the status map records the remaining blockers in concrete terms;
- final closure requires a validation packet on `main`.
