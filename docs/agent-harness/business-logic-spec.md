# Business Logic Specification Harness

Business logic means observable product behavior, not source code reuse. The
rewrite must preserve decisions, identities, state transitions, and output
contracts that users depend on. The legacy implementation is the oracle for
golden master generation; the new implementation should be clean, typed,
testable, and owned by the new stack.

## Golden Master Rule

Before rewriting a domain area, capture representative inputs and expected
outputs. A golden master should include:

- fixture input;
- legacy output;
- expected new output;
- tolerated differences;
- explicit owner for intentional behavior changes.

If parity cannot be established, stop and decide whether the legacy behavior is
wrong, underspecified, or intentionally changing.

## Required Business Areas

### Strategy Selection

- Preserve strategy-specific candidate selection behavior unless a product
  decision changes it.
- Preserve candidate identity as `(code, strategy)`.
- Preserve same-date multi-strategy behavior and `--merge-same-date` semantics.
- Golden masters should include repeated codes across strategies.

### Candidate Persistence

- Preserve candidate fields needed for review, archive, chart evidence, and
  analysis workflows.
- New storage may replace JSON files, but the information in
  `candidates_latest.json` and dated candidate files must be importable.

### Gemini Review

- Preserve review normalization, recommendation fields, raw evidence policy,
  checkpoint/resume semantics, retry behavior, and `skip_existing`.
- Unit tests should not require live Gemini calls.
- Live review validation requires explicit task justification.

### History and Archive

- Preserve review matching, `review_key`, recommendation status, strategy
  partitions, all-row exports, and date-based browsing behavior.
- New storage must keep historical records traceable by date, run, strategy,
  code, and review status.

### Chart Evidence

- Preserve the ability to inspect candidate and review evidence with stable
  links to chart artifacts or generated chart views.
- Chart rendering may move into the new frontend, but evidence must stay
  reproducible.

### Backtest and Analytics

- Analytics must distinguish strategy, run, date, candidate, review status,
  archive state, and analysis outcome.
- Backtest-style results must be reproducible from versioned inputs and config.

## Out of Scope

Simulated trading / paper trading is not part of the product-level rewrite.
Existing legacy behavior should not be modified by refactor work unless the
user explicitly reopens that scope.

## Acceptance Rules

- behavior parity is required for strict-parity areas before old behavior is
  removed or rewritten beyond recognition.
- New implementation passes golden master parity before old behavior is removed.
- Business deviations are reviewed as product decisions, not hidden refactor
  side effects.
- IO, storage, model calls, and UI are separated from pure business logic.
- Test fixtures are small enough for routine local validation.
